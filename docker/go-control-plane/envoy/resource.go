// Copyright 2020 Envoyproxy Authors
//
//   Licensed under the Apache License, Version 2.0 (the "License");
//   you may not use this file except in compliance with the License.
//   You may obtain a copy of the License at
//
//       http://www.apache.org/licenses/LICENSE-2.0
//
//   Unless required by applicable law or agreed to in writing, software
//   distributed under the License is distributed on an "AS IS" BASIS,
//   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//   See the License for the specific language governing permissions and
//   limitations under the License.

package example

import (
	"time"

	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/durationpb"

	cluster "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	endpoint "github.com/envoyproxy/go-control-plane/envoy/config/endpoint/v3"
	listener "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	route "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	router "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/http/router/v3"
	hcm "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/http_connection_manager/v3"
	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
)

const (
	ClusterName  = "example_proxy_cluster"
	ListenerName = "listener_0"
)

func MakeCluster(clusterName, upstreamHost string, upstreamPort uint32) *cluster.Cluster {
	return &cluster.Cluster{
		Name:                 clusterName,
		ConnectTimeout:       durationpb.New(5 * time.Second),
		ClusterDiscoveryType: &cluster.Cluster_Type{Type: cluster.Cluster_LOGICAL_DNS},
		LbPolicy:             cluster.Cluster_ROUND_ROBIN,
		LoadAssignment:       makeEndpoint(clusterName, upstreamHost, upstreamPort),
		DnsLookupFamily:      cluster.Cluster_V4_ONLY,
	}
}

func makeEndpoint(clusterName, upstreamHost string, upstreamPort uint32) *endpoint.ClusterLoadAssignment {
	return &endpoint.ClusterLoadAssignment{
		ClusterName: clusterName,
		Endpoints: []*endpoint.LocalityLbEndpoints{{
			LbEndpoints: []*endpoint.LbEndpoint{{
				HostIdentifier: &endpoint.LbEndpoint_Endpoint{
					Endpoint: &endpoint.Endpoint{
						Address: &core.Address{
							Address: &core.Address_SocketAddress{
								SocketAddress: &core.SocketAddress{
									Protocol: core.SocketAddress_TCP,
									Address:  upstreamHost,
									PortSpecifier: &core.SocketAddress_PortValue{
										PortValue: upstreamPort,
									},
								},
							},
						},
					},
				},
			}},
		}},
	}
}

func MakeHTTPListener(listenerName, cluster string) *listener.Listener {
	any_route, err := anypb.New(&router.Router{})
	if err != nil {
		panic(err)
	}
	httpcm := &hcm.HttpConnectionManager{
		RouteSpecifier: &hcm.HttpConnectionManager_RouteConfig{
			RouteConfig: &route.RouteConfiguration{
				VirtualHosts: []*route.VirtualHost{
					{
						Domains: []string{"*"},
						Routes: []*route.Route{
							{
								Match: &route.RouteMatch{
									PathSpecifier: &route.RouteMatch_Prefix{},
								},
								Action: &route.Route_Route{
									Route: &route.RouteAction{
										ClusterSpecifier: &route.RouteAction_Cluster{
											Cluster: cluster,
										},
									},
								},
							},
						},
					},
				},
			},
		},
		HttpFilters: []*hcm.HttpFilter{
			{
				Name: "router",
				ConfigType: &hcm.HttpFilter_TypedConfig{
					TypedConfig: any_route,
				},
			},
		},
	}
	any_hcm, err := anypb.New(httpcm)
	if err != nil {
		panic(err)
	}
	return &listener.Listener{
		Name: listenerName,
		ApiListener: &listener.ApiListener{
			ApiListener: any_hcm,
		},
	}
}

func BuildResources(listener, cluster, upstreamHost string, upstreamPort uint32) map[resource.Type][]types.Resource {
	return map[resource.Type][]types.Resource{
		resource.ClusterType:  {MakeCluster(cluster, upstreamHost, upstreamPort)},
		resource.ListenerType: {MakeHTTPListener(listener, cluster)},
	}
}

func GenerateSnapshot(upstreamHost string, upstreamPort uint32) *cache.Snapshot {
	snap, _ := cache.NewSnapshot("1", BuildResources(ListenerName, ClusterName, upstreamHost, upstreamPort))
	return snap
}
