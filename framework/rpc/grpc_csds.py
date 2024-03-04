# Copyright 2021 The gRPC Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
This contains helpers for gRPC services defined in
https://github.com/envoyproxy/envoy/blob/main/api/envoy/service/status/v3/csds.proto
"""
import json
import logging
import re
from typing import Any, Optional

from typing_extensions import TypeAlias

# Needed to load the descriptors so that Any is parsed
# TODO(sergiitk): replace with import xds_protos when it works
# isort: off
# pylint: disable=unused-import,ungrouped-imports
import framework.rpc.xds_protos_imports

# pylint: enable=unused-import,ungrouped-imports
# isort: on

from envoy.service.status.v3 import csds_pb2
from envoy.service.status.v3 import csds_pb2_grpc
import grpc

import framework.rpc

logger = logging.getLogger(__name__)

# Type aliases
ClientConfig: TypeAlias = csds_pb2.ClientConfig
_ClientStatusRequest: TypeAlias = csds_pb2.ClientStatusRequest
ClientStatusResponse: TypeAlias = csds_pb2.ClientStatusResponse
JsonType: TypeAlias = dict[Any]


class DumpedXdsConfig(dict):
    """
    A convenience class to check xDS config.

    Feel free to add more pre-compute fields.
    """

    def __init__(self, xds_json: JsonType):  # pylint: disable=too-many-branches
        super().__init__(xds_json)
        self.json_config = xds_json
        self.lds = None
        self.rds = None
        self.rds_version = None
        self.cds = []
        self.eds = []
        self.endpoints = []
        for xds_config in self.get("xdsConfig", []):
            try:
                if "listenerConfig" in xds_config:
                    self.lds = xds_config["listenerConfig"]["dynamicListeners"][
                        0
                    ]["activeState"]["listener"]
                elif "routeConfig" in xds_config:
                    self.rds = xds_config["routeConfig"]["dynamicRouteConfigs"][
                        0
                    ]["routeConfig"]
                    self.rds_version = xds_config["routeConfig"][
                        "dynamicRouteConfigs"
                    ][0]["versionInfo"]
                elif "clusterConfig" in xds_config:
                    for cluster in xds_config["clusterConfig"][
                        "dynamicActiveClusters"
                    ]:
                        self.cds.append(cluster["cluster"])
                elif "endpointConfig" in xds_config:
                    for endpoint in xds_config["endpointConfig"][
                        "dynamicEndpointConfigs"
                    ]:
                        self.eds.append(endpoint["endpointConfig"])
            # TODO(lidiz) reduce the catch to LookupError
            except Exception as e:  # pylint: disable=broad-except
                logging.debug(
                    "Parsing dumped xDS config failed with %s: %s", type(e), e
                )
        for generic_xds_config in self.get("genericXdsConfigs", []):
            try:
                if re.search(r"\.Listener$", generic_xds_config["typeUrl"]):
                    self.lds = generic_xds_config["xdsConfig"]
                elif re.search(
                    r"\.RouteConfiguration$", generic_xds_config["typeUrl"]
                ):
                    self.rds = generic_xds_config["xdsConfig"]
                    self.rds_version = generic_xds_config["versionInfo"]
                elif re.search(r"\.Cluster$", generic_xds_config["typeUrl"]):
                    self.cds.append(generic_xds_config["xdsConfig"])
                elif re.search(
                    r"\.ClusterLoadAssignment$", generic_xds_config["typeUrl"]
                ):
                    self.eds.append(generic_xds_config["xdsConfig"])
            # TODO(lidiz) reduce the catch to LookupError
            except Exception as e:  # pylint: disable=broad-except
                logging.debug(
                    "Parsing dumped xDS config failed with %s: %s", type(e), e
                )
        for endpoint_config in self.eds:
            for endpoint in endpoint_config.get("endpoints", {}):
                for lb_endpoint in endpoint.get("lbEndpoints", {}):
                    try:
                        if lb_endpoint["healthStatus"] == "HEALTHY":
                            self.endpoints.append(
                                "%s:%s"
                                % (
                                    lb_endpoint["endpoint"]["address"][
                                        "socketAddress"
                                    ]["address"],
                                    lb_endpoint["endpoint"]["address"][
                                        "socketAddress"
                                    ]["portValue"],
                                )
                            )
                    # TODO(lidiz) reduce the catch to LookupError
                    except Exception as e:  # pylint: disable=broad-except
                        logging.debug(
                            "Parse endpoint failed with %s: %s", type(e), e
                        )

    def __str__(self) -> str:
        return json.dumps(self, indent=2)


class CsdsClient(framework.rpc.grpc.GrpcClientHelper):
    stub: csds_pb2_grpc.ClientStatusDiscoveryServiceStub

    def __init__(
        self, channel: grpc.Channel, *, log_target: Optional[str] = ""
    ):
        super().__init__(
            channel,
            csds_pb2_grpc.ClientStatusDiscoveryServiceStub,
            log_target=log_target,
        )

    def fetch_client_status(self, **kwargs) -> Optional[ClientConfig]:
        """Fetches the active xDS configurations."""
        response = self.call_unary_with_deadline(
            rpc="FetchClientStatus", req=_ClientStatusRequest(), **kwargs
        )
        if len(response.config) != 1:
            logger.debug(
                "Unexpected number of client configs: %s", len(response.config)
            )
            return None
        return response.config[0]
