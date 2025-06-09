# Copyright 2024 gRPC authors.
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

from envoy.config.cluster.v3 import cluster_pb2
from envoy.config.core.v3 import address_pb2
from envoy.config.core.v3 import base_pb2
from envoy.config.core.v3 import config_source_pb2
from envoy.config.endpoint.v3 import endpoint_components_pb2
from envoy.config.endpoint.v3 import endpoint_pb2
from envoy.config.listener.v3 import api_listener_pb2
from envoy.config.listener.v3 import listener_pb2
from envoy.config.listener.v3 import listener_components_pb2
from envoy.config.route.v3 import route_components_pb2
from envoy.config.route.v3 import route_pb2
from envoy.extensions.filters.http.router.v3 import router_pb2
from envoy.extensions.filters.network.http_connection_manager.v3 import (
    http_connection_manager_pb2,
)
from google.protobuf import any_pb2
from google.protobuf import message
from google.protobuf import wrappers_pb2

from protos.grpc.testing.xdsconfig import xdsconfig_pb2


def _wrap_in_any(msg: message.Message) -> any_pb2.Any:
    any_msg = any_pb2.Any()
    any_msg.Pack(msg)
    return any_msg


def build_listener(listener_name: str, cluster_name: str):
    hcm = http_connection_manager_pb2.HttpConnectionManager(
        route_config=route_pb2.RouteConfiguration(
            virtual_hosts=[
                route_components_pb2.VirtualHost(
                    domains=["*"],
                    routes=[
                        route_components_pb2.Route(
                            match=route_components_pb2.RouteMatch(prefix=""),
                            route=route_components_pb2.RouteAction(
                                cluster=cluster_name
                            ),
                        )
                    ],
                )
            ]
        ),
        http_filters=[
            http_connection_manager_pb2.HttpFilter(
                name="router", typed_config=_wrap_in_any(router_pb2.Router())
            )
        ],
    )
    return listener_pb2.Listener(
        api_listener=api_listener_pb2.ApiListener(
            api_listener=_wrap_in_any(hcm)
        ),
        name=listener_name,
    )


def build_endpoint(
    cluster_name: str, upstream_host: str, upstream_port: int
) -> endpoint_pb2.ClusterLoadAssignment:
    endpoint = endpoint_components_pb2.Endpoint(
        address=address_pb2.Address(
            socket_address=address_pb2.SocketAddress(
                protocol=address_pb2.SocketAddress.TCP,
                address=upstream_host,
                port_value=upstream_port,
            )
        )
    )
    return endpoint_pb2.ClusterLoadAssignment(
        cluster_name=cluster_name,
        endpoints=[
            endpoint_components_pb2.LocalityLbEndpoints(
                locality=base_pb2.Locality(
                    region="xds_default_locality_region",
                    zone="xds_default_locality_zone",
                    sub_zone="locality0",
                ),
                lb_endpoints=[
                    endpoint_components_pb2.LbEndpoint(
                        endpoint=endpoint,
                        health_status=1,
                        load_balancing_weight=wrappers_pb2.UInt32Value(value=1),
                    )
                ],
                load_balancing_weight=wrappers_pb2.UInt32Value(value=1),
            )
        ],
    )


def build_cluster(cluster_name: str, service_name: str) -> cluster_pb2.Cluster:
    return cluster_pb2.Cluster(
        name=cluster_name,
        type=cluster_pb2.Cluster.DiscoveryType.EDS,
        eds_cluster_config=cluster_pb2.Cluster.EdsClusterConfig(
            eds_config=config_source_pb2.ConfigSource(
                self=config_source_pb2.SelfConfigSource(),
            ),
            service_name=service_name,
        ),
    )

def build_server_listener(server_listener_name: str, port: int, route_config_name: str) -> listener_pb2.Listener:
    hcm = http_connection_manager_pb2.HttpConnectionManager(
        rds=http_connection_manager_pb2.Rds(
            route_config_name=route_config_name,
            config_source=config_source_pb2.ConfigSource(
                ads=config_source_pb2.AggregatedConfigSource()
            )
        ),
        http_filters=[
            http_connection_manager_pb2.HttpFilter(
                name="router", typed_config=_wrap_in_any(router_pb2.Router())
            )
        ],
    )
    return listener_pb2.Listener(
        name=server_listener_name,
        address=address_pb2.Address(
            socket_address=address_pb2.SocketAddress(
                protocol=address_pb2.SocketAddress.TCP,
                address="0.0.0.0",
                port_value=port
            )
        ),
        filter_chains=[listener_components_pb2.FilterChain(
            filters=[
                listener_components_pb2.Filter(
                    name='default_filter',
                    typed_config=_wrap_in_any(hcm)
                )
            ]
        )]
    )

def build_server_route_config(route_config_name: str) -> route_pb2.RouteConfiguration:
    route = route_pb2.RouteConfiguration(
        name=route_config_name,
        virtual_hosts=[
            route_components_pb2.VirtualHost(
                domains=["*"],
                routes=[
                    route_components_pb2.Route(
                        match=route_components_pb2.RouteMatch(prefix=""),
                        non_forwarding_action=route_components_pb2.NonForwardingAction()
                    )
                ]
            )
        ]
    )
    return route


def _build_resource_to_set(resource: message.Message):
    name = (
        resource.cluster_name
        if hasattr(resource, "cluster_name")
        else resource.name
    )
    return xdsconfig_pb2.SetResourcesRequest.ResourceToSet(
        type_url=f"type.googleapis.com/{resource.DESCRIPTOR.full_name}",
        name=name,
        body=_wrap_in_any(resource),
    )


def build_set_resources_request(resources: list[message.Message]):
    return xdsconfig_pb2.SetResourcesRequest(
        resources=[
            _build_resource_to_set(r) for r in resources
        ]
    )


def build_listener_and_cluster(
    listener_name: str,
    cluster_name: str,
    upstream_host: str,
    upstream_port: int,
) -> xdsconfig_pb2.SetResourcesRequest:
    service_name = f"{cluster_name}_eds_service"
    listener = build_listener(listener_name, cluster_name)
    cluster = build_cluster(cluster_name, service_name)
    load_assignment = build_endpoint(
        service_name, upstream_host, upstream_port
    )
    return build_set_resources_request([listener, cluster, load_assignment])
