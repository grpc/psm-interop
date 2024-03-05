# Copyright 2023 gRPC authors.
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
"""This contains common helpers for working with grpc data structures."""
import collections
import dataclasses
import functools
from typing import Optional

import grpc
from typing_extensions import TypeAlias
import yaml

from framework.rpc import grpc_testing

# Type aliases
_RpcsByPeerPretty: TypeAlias = dict[str, int]
_RpcsByMethodPretty: TypeAlias = dict[str, _RpcsByPeerPretty]
_MetadataByPeerPretty: TypeAlias = dict[str, list[str]]
_MetadatasByPeerPretty: TypeAlias = dict[str, _MetadataByPeerPretty]


@functools.cache
def status_from_int(grpc_status_int: int) -> Optional[grpc.StatusCode]:
    """Converts the integer gRPC status code to the grpc.StatusCode enum."""
    for grpc_status in grpc.StatusCode:
        if grpc_status.value[0] == grpc_status_int:
            return grpc_status
    return None


def status_eq(grpc_status_int: int, grpc_status: grpc.StatusCode) -> bool:
    """Compares the integer gRPC status code with the grpc.StatusCode enum."""
    return status_from_int(grpc_status_int) is grpc_status


def status_pretty(grpc_status: grpc.StatusCode) -> str:
    """Formats the status code as (int, NAME), f.e. (4, DEADLINE_EXCEEDED)"""
    return f"({grpc_status.value[0]}, {grpc_status.name})"


@dataclasses.dataclass(frozen=True)
class PrettyStatsPerMethod:
    # The name of the method.
    method: str

    # The number of RPCs started for this method, completed and in-flight.
    rpcs_started: int

    # The number of RPCs that completed with each status for this method.
    # Format: status code -> RPC count, f.e.:
    # {
    #   "(0, OK)": 20,
    #   "(14, UNAVAILABLE)": 10
    # }
    result: dict[str, int]

    @property
    @functools.cache
    def rpcs_completed(self):
        """Returns the total count of competed RPCs across all statuses."""
        return sum(self.result.values())

    @staticmethod
    def from_response(
        method_name: str, method_stats: grpc_testing.MethodStats
    ) -> "PrettyStatsPerMethod":
        stats: dict[str, int] = dict()
        for status_int, count in method_stats.result.items():
            status: Optional[grpc.StatusCode] = status_from_int(status_int)
            status_formatted = status_pretty(status) if status else "None"
            stats[status_formatted] = count
        return PrettyStatsPerMethod(
            method=method_name,
            rpcs_started=method_stats.rpcs_started,
            result=stats,
        )


def accumulated_stats_pretty(
    accumulated_stats: grpc_testing.LoadBalancerAccumulatedStatsResponse,
    *,
    ignore_empty: bool = False,
) -> str:
    """Pretty print LoadBalancerAccumulatedStatsResponse.

    Example:
      - method: EMPTY_CALL
        rpcs_started: 0
        result:
          (2, UNKNOWN): 20
      - method: UNARY_CALL
        rpcs_started: 31
        result:
          (0, OK): 10
          (14, UNAVAILABLE): 20
    """
    # Only look at stats_per_method, as the other fields are deprecated.
    result: list[dict] = []
    for method, stats in accumulated_stats.stats_per_method.items():
        pretty_stats = PrettyStatsPerMethod.from_response(method, stats)
        # Skip methods with no RPCs reported when ignore_empty is True.
        if ignore_empty and not pretty_stats.rpcs_started:
            continue
        result.append(dataclasses.asdict(pretty_stats))

    return yaml.dump(result, sort_keys=False)


@dataclasses.dataclass(frozen=True)
class PrettyLoadBalancerStats:
    # The number of RPCs that failed to record a remote peer.
    num_failures: int

    # The number of completed RPCs for each peer.
    # Format: a dictionary from the host name (str) to the RPC count (int), f.e.
    # {"host-a": 10, "host-b": 20}
    rpcs_by_peer: _RpcsByPeerPretty

    # The number of completed RPCs per method per each pear.
    # Format: a dictionary from the method name to RpcsByPeer (see above), f.e.:
    # {
    #   "UnaryCall": {"host-a": 10, "host-b": 20},
    #   "EmptyCall": {"host-a": 42},
    # }
    rpcs_by_method: _RpcsByMethodPretty

    metadatas_by_peer: _MetadatasByPeerPretty

    @classmethod
    def _parse_rpcs_by_peer(
        cls, rpcs_by_peer: grpc_testing.RpcsByPeerMap
    ) -> _RpcsByPeerPretty:
        result: _RpcsByPeerPretty = dict()
        for peer, count in rpcs_by_peer.items():
            result[peer] = count
        return result

    @classmethod
    def _parse_rpcs_by_method(
        cls, rpcs_by_method: grpc_testing.RpcsByMethod
    ) -> _RpcsByMethodPretty:
        result: _RpcsByMethodPretty = dict()
        for method, stats in rpcs_by_method.items():
            if stats:
                result[method] = cls._parse_rpcs_by_peer(stats.rpcs_by_peer)
        return result

    @staticmethod
    def _parse_metadatas_by_peer(
        metadatas_by_peer: grpc_testing.MetadatasByPeer,
    ) -> _MetadatasByPeerPretty:
        result: _MetadatasByPeerPretty = collections.defaultdict(dict)
        for peer, metadata_by_peer in metadatas_by_peer.items():
            for rpc_metadata in metadata_by_peer.rpc_metadata:
                # Metadata values are repeated all the time, only add unique.
                uniq_vals: dict[str, set[str]] = collections.defaultdict(set)
                for metadata in rpc_metadata.metadata:
                    key: str = metadata.key
                    if metadata.type == grpc_testing.MetadataType.TRAILING:
                        key = f"TRAILING_{key}"
                    uniq_vals[key].add(metadata.value)

                for key, uniq_val in uniq_vals.items():
                    result[peer][key] = list(uniq_val)
        return dict(result)

    @classmethod
    def from_response(
        cls, lb_stats: grpc_testing.LoadBalancerStatsResponse
    ) -> "PrettyLoadBalancerStats":
        return PrettyLoadBalancerStats(
            num_failures=lb_stats.num_failures,
            rpcs_by_peer=cls._parse_rpcs_by_peer(lb_stats.rpcs_by_peer),
            rpcs_by_method=cls._parse_rpcs_by_method(lb_stats.rpcs_by_method),
            metadatas_by_peer=cls._parse_metadatas_by_peer(
                lb_stats.metadatas_by_peer
            ),
        )


def lb_stats_pretty(lb_stats: grpc_testing.LoadBalancerStatsResponse) -> str:
    """Pretty print LoadBalancerStatsResponse.

    Example:
      num_failures: 13
      rpcs_by_method:
        UnaryCall:
          psm-grpc-server-a: 100
          psm-grpc-server-b: 42
        EmptyCall:
          psm-grpc-server-a: 200
      rpcs_by_peer:
        psm-grpc-server-a: 200
        psm-grpc-server-b: 42
      metadatas_by_peer:
        psm-grpc-server-a:
          cookie: [c1, c2]
          TRAILING_cookie: [foo]
        psm-grpc-server-b:
          cookie: [bar]
    """
    pretty_lb_stats = PrettyLoadBalancerStats.from_response(lb_stats)
    stats_as_dict = dataclasses.asdict(pretty_lb_stats)

    # Don't print metadatas_by_peer unless it has data
    if not stats_as_dict["metadatas_by_peer"]:
        stats_as_dict.pop("metadatas_by_peer")

    return yaml.dump(stats_as_dict, sort_keys=False)
