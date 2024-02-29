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
from typing import Any, Final, Optional, Type, cast

from google.protobuf import json_format
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
ClientConfigDict: TypeAlias = dict[Any]

# Ignore this errors while parsing client_config_dict.
_PARSE_ERRORS: Final[tuple[Type[Exception], ...]] = (
    AttributeError,
    TypeError,
    KeyError,
)


class DumpedXdsConfig(dict):
    """A convenience class to check xDS config.

    Feel free to add more pre-compute fields.
    """

    def __init__(self, client_config_dict: ClientConfigDict):
        super().__init__(client_config_dict)
        self.client_config_dict = client_config_dict
        self.lds = None
        self.rds = None
        self.rds_version = None
        self.cds = []
        self.eds = []
        # Healthy endpoints.
        self.endpoints = []
        self.draining_endpoints = []

        # Parse old-style xDS Config.
        for xds_config in self.get("xdsConfig", []):
            self._parse_per_xds_config(xds_config)

        # Parse new generic xDS Config.
        for generic_xds_config in self.get("genericXdsConfigs", []):
            self._parse_generic_xds_config(generic_xds_config)

        # Parse endpoints
        for endpoint_config in self.eds:
            for endpoint in endpoint_config.get("endpoints", {}):
                for lb_endpoint in endpoint.get("lbEndpoints", {}):
                    self._parse_lb_endpoint(lb_endpoint)

    def _parse_per_xds_config(self, xds_config: ClientConfigDict):
        try:
            if "listenerConfig" in xds_config:
                listeners = xds_config["listenerConfig"]["dynamicListeners"]
                self.lds = listeners[0]["activeState"]["listener"]
            elif "routeConfig" in xds_config:
                routes = xds_config["routeConfig"]["dynamicRouteConfigs"]
                self.rds = routes[0]["routeConfig"]
                self.rds_version = routes[0]["versionInfo"]
            elif "clusterConfig" in xds_config:
                clusters = xds_config["clusterConfig"]["dynamicActiveClusters"]
                for cluster in clusters:
                    self.cds.append(cluster["cluster"])
            elif "endpointConfig" in xds_config:
                endpoints = xds_config["endpointConfig"][
                    "dynamicEndpointConfigs"
                ]
                for endpoint in endpoints:
                    self.eds.append(endpoint["endpointConfig"])
        except _PARSE_ERRORS as e:
            logging.debug(
                "Parsing dumped xDS config failed with %s: %s", type(e), e
            )

    def _parse_generic_xds_config(self, generic_xds_config: ClientConfigDict):
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
                r"\.ClusterLoadAssignment$",
                generic_xds_config["typeUrl"],
            ):
                self.eds.append(generic_xds_config["xdsConfig"])
        except _PARSE_ERRORS as e:
            logging.debug(
                "Parsing dumped generic xDS config failed with %s: %s",
                type(e),
                e,
            )

    def _parse_lb_endpoint(self, lb_endpoint: ClientConfigDict):
        try:
            endpoint_address = self._lb_endpoint_address(lb_endpoint)
            if lb_endpoint["healthStatus"] == "HEALTHY":
                self.endpoints.append(endpoint_address)
            elif lb_endpoint["healthStatus"] == "DRAINING":
                self.draining_endpoints.append(endpoint_address)
        except _PARSE_ERRORS as e:
            logging.debug("Parse endpoint failed with %s: %s", type(e), e)

    @classmethod
    def _lb_endpoint_address(cls, lb_endpoint: ClientConfigDict) -> str:
        host = lb_endpoint["endpoint"]["address"]["socketAddress"]["address"]
        port = lb_endpoint["endpoint"]["address"]["socketAddress"]["portValue"]
        return f"{host}:{port}"

    @classmethod
    def from_message(cls, client_config: ClientConfig) -> "DumpedXdsConfig":
        return DumpedXdsConfig(json_format.MessageToDict(client_config))

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
        response: ClientStatusResponse = self.call_unary_with_deadline(
            rpc="FetchClientStatus",
            req=_ClientStatusRequest(),
            **kwargs,
        )
        response = cast(ClientStatusResponse, response)
        if len(response.config) != 1:
            logger.debug(
                "Unexpected number of client configs: %s", len(response.config)
            )
            return None
        return response.config[0]

    def fetch_client_status_parsed(self, **kwargs) -> Optional[DumpedXdsConfig]:
        """Same as fetch_client_status, but also parses."""
        client_config = self.fetch_client_status(**kwargs)
        if client_config is None:
            return None
        return DumpedXdsConfig.from_message(client_config)
