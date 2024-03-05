# Copyright 2020 gRPC authors.
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
https://github.com/grpc/grpc/blob/master/src/proto/grpc/testing/test.proto
"""
from collections.abc import Sequence
import logging
from typing import Any, Final, Optional, cast

import grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc
from typing_extensions import TypeAlias

import framework.rpc
from protos.grpc.testing import empty_pb2
from protos.grpc.testing import messages_pb2
from protos.grpc.testing import test_pb2_grpc

# Type aliases
LoadBalancerStatsResponse: TypeAlias = messages_pb2.LoadBalancerStatsResponse
LoadBalancerAccumulatedStatsResponse: TypeAlias = (
    messages_pb2.LoadBalancerAccumulatedStatsResponse
)
MethodStats: TypeAlias = (
    messages_pb2.LoadBalancerAccumulatedStatsResponse.MethodStats
)
RpcsByPeer: TypeAlias = messages_pb2.LoadBalancerStatsResponse.RpcsByPeer

# RPC Metadata
RpcMetadata: TypeAlias = messages_pb2.LoadBalancerStatsResponse.RpcMetadata
MetadataByPeer: TypeAlias = (
    messages_pb2.LoadBalancerStatsResponse.MetadataByPeer
)
# An argument to XdsUpdateClientConfigureService.Configure.
# Rpc type name, key, value.
ConfigureMetadata: TypeAlias = Sequence[tuple[str, str, str]]

# LoadBalancerStatsResponse parsed as a dict.
LbStatsDict: TypeAlias = dict[Any]

# Constants.
# ProtoBuf translatable RpcType enums
RPC_TYPE_UNARY_CALL: Final[str] = "UNARY_CALL"
RPC_TYPE_EMPTY_CALL: Final[str] = "EMPTY_CALL"
RPC_TYPES_BOTH_CALLS: Final[tuple[str, str]] = (
    RPC_TYPE_UNARY_CALL,
    RPC_TYPE_EMPTY_CALL,
)


class RpcDistributionStats:
    """A convenience class to check RPC distribution.

    Feel free to add more pre-compute fields.
    """

    num_failures: int
    num_oks: int
    default_service_rpc_count: int
    alternative_service_rpc_count: int
    unary_call_default_service_rpc_count: int
    empty_call_default_service_rpc_count: int
    unary_call_alternative_service_rpc_count: int
    empty_call_alternative_service_rpc_count: int

    def __init__(self, json_lb_stats: LbStatsDict):
        self.num_failures = json_lb_stats.get("numFailures", 0)

        self.num_peers = 0
        self.num_oks = 0
        self.default_service_rpc_count = 0
        self.alternative_service_rpc_count = 0
        self.unary_call_default_service_rpc_count = 0
        self.empty_call_default_service_rpc_count = 0
        self.unary_call_alternative_service_rpc_count = 0
        self.empty_call_alternative_service_rpc_count = 0
        self.raw = json_lb_stats

        if "rpcsByPeer" in json_lb_stats:
            self.num_peers = len(json_lb_stats["rpcsByPeer"])
        if "rpcsByMethod" in json_lb_stats:
            for rpc_type in json_lb_stats["rpcsByMethod"]:
                for peer in json_lb_stats["rpcsByMethod"][rpc_type][
                    "rpcsByPeer"
                ]:
                    count = json_lb_stats["rpcsByMethod"][rpc_type][
                        "rpcsByPeer"
                    ][peer]
                    self.num_oks += count
                    if rpc_type == "UnaryCall":
                        if "alternative" in peer:
                            self.unary_call_alternative_service_rpc_count = (
                                count
                            )
                            self.alternative_service_rpc_count += count
                        else:
                            self.unary_call_default_service_rpc_count = count
                            self.default_service_rpc_count += count
                    else:
                        if "alternative" in peer:
                            self.empty_call_alternative_service_rpc_count = (
                                count
                            )
                            self.alternative_service_rpc_count += count
                        else:
                            self.empty_call_default_service_rpc_count = count
                            self.default_service_rpc_count += count


class LoadBalancerStatsServiceClient(framework.rpc.grpc.GrpcClientHelper):
    stub: test_pb2_grpc.LoadBalancerStatsServiceStub
    STATS_PARTIAL_RESULTS_TIMEOUT_SEC = 1200
    STATS_ACCUMULATED_RESULTS_TIMEOUT_SEC = 600

    def __init__(
        self, channel: grpc.Channel, *, log_target: Optional[str] = ""
    ):
        super().__init__(
            channel,
            test_pb2_grpc.LoadBalancerStatsServiceStub,
            log_target=log_target,
        )

    def get_client_stats(
        self,
        *,
        num_rpcs: int,
        timeout_sec: Optional[int] = STATS_PARTIAL_RESULTS_TIMEOUT_SEC,
        metadata_keys: Optional[tuple[str, ...]] = None,
    ) -> LoadBalancerStatsResponse:
        if timeout_sec is None:
            timeout_sec = self.STATS_PARTIAL_RESULTS_TIMEOUT_SEC

        stats = self.call_unary_with_deadline(
            rpc="GetClientStats",
            req=messages_pb2.LoadBalancerStatsRequest(
                num_rpcs=num_rpcs,
                timeout_sec=timeout_sec,
                metadata_keys=metadata_keys or None,
            ),
            deadline_sec=timeout_sec,
            log_level=logging.INFO,
        )
        return cast(LoadBalancerStatsResponse, stats)

    def get_client_accumulated_stats(
        self, *, timeout_sec: Optional[int] = None
    ) -> LoadBalancerAccumulatedStatsResponse:
        if timeout_sec is None:
            timeout_sec = self.STATS_ACCUMULATED_RESULTS_TIMEOUT_SEC

        stats = self.call_unary_with_deadline(
            rpc="GetClientAccumulatedStats",
            req=messages_pb2.LoadBalancerAccumulatedStatsRequest(),
            deadline_sec=timeout_sec,
            log_level=logging.INFO,
        )
        return cast(LoadBalancerAccumulatedStatsResponse, stats)


class XdsUpdateClientConfigureServiceClient(
    framework.rpc.grpc.GrpcClientHelper
):
    stub: test_pb2_grpc.XdsUpdateClientConfigureServiceStub
    CONFIGURE_TIMEOUT_SEC: Final[int] = 5

    def __init__(
        self, channel: grpc.Channel, *, log_target: Optional[str] = ""
    ):
        super().__init__(
            channel,
            test_pb2_grpc.XdsUpdateClientConfigureServiceStub,
            log_target=log_target,
        )

    def configure(
        self,
        *,
        rpc_types: Sequence[str],
        metadata: Optional[ConfigureMetadata] = None,
        app_timeout: Optional[int] = None,
        timeout_sec: Optional[int] = CONFIGURE_TIMEOUT_SEC,
    ) -> None:
        request = messages_pb2.ClientConfigureRequest()
        for rpc_type in rpc_types:
            request.types.append(
                messages_pb2.ClientConfigureRequest.RpcType.Value(rpc_type)
            )
        if metadata:
            for entry in metadata:
                request.metadata.append(
                    messages_pb2.ClientConfigureRequest.Metadata(
                        type=messages_pb2.ClientConfigureRequest.RpcType.Value(
                            entry[0]
                        ),
                        key=entry[1],
                        value=entry[2],
                    )
                )
        if app_timeout:
            request.timeout_sec = app_timeout
        if timeout_sec is None:
            timeout_sec = self.CONFIGURE_TIMEOUT_SEC

        # The response is empty.
        self.call_unary_with_deadline(
            rpc="Configure",
            req=request,
            deadline_sec=timeout_sec,
            log_level=logging.INFO,
        )

    def configure_unary(
        self,
        *,
        metadata: Optional[ConfigureMetadata] = None,
        app_timeout: Optional[int] = None,
        timeout_sec: Optional[int] = CONFIGURE_TIMEOUT_SEC,
    ) -> None:
        self.configure(
            rpc_types=(RPC_TYPE_UNARY_CALL,),
            metadata=metadata,
            app_timeout=app_timeout,
            timeout_sec=timeout_sec,
        )

    def configure_empty(
        self,
        *,
        metadata: Optional[ConfigureMetadata] = None,
        app_timeout: Optional[int] = None,
        timeout_sec: Optional[int] = CONFIGURE_TIMEOUT_SEC,
    ) -> None:
        self.configure(
            rpc_types=(RPC_TYPE_EMPTY_CALL,),
            metadata=metadata,
            app_timeout=app_timeout,
            timeout_sec=timeout_sec,
        )


class XdsUpdateHealthServiceClient(framework.rpc.grpc.GrpcClientHelper):
    stub: test_pb2_grpc.XdsUpdateHealthServiceStub

    def __init__(self, channel: grpc.Channel, log_target: Optional[str] = ""):
        super().__init__(
            channel,
            test_pb2_grpc.XdsUpdateHealthServiceStub,
            log_target=log_target,
        )

    def set_serving(self):
        self.call_unary_with_deadline(
            rpc="SetServing", req=empty_pb2.Empty(), log_level=logging.INFO
        )

    def set_not_serving(self):
        self.call_unary_with_deadline(
            rpc="SetNotServing", req=empty_pb2.Empty(), log_level=logging.INFO
        )


class HealthClient(framework.rpc.grpc.GrpcClientHelper):
    stub: health_pb2_grpc.HealthStub

    def __init__(self, channel: grpc.Channel, log_target: Optional[str] = ""):
        super().__init__(
            channel, health_pb2_grpc.HealthStub, log_target=log_target
        )

    def check_health(self):
        return self.call_unary_with_deadline(
            rpc="Check",
            req=health_pb2.HealthCheckRequest(),
            log_level=logging.INFO,
        )
