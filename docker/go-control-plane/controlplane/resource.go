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

package controlplane

import (
	"strconv"
	"time"

	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/durationpb"

	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"

	pb_cluster "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	pb_core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	pb_endpoint "github.com/envoyproxy/go-control-plane/envoy/config/endpoint/v3"
	pb_listener "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	pb_route "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	pb_router "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/http/router/v3"
	pb_hcm "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/http_connection_manager/v3"
)

const (
	ClusterName  = "example_proxy_cluster"
	ListenerName = "listener_0"
)

var snapshot_version int = 1;

// MakeCluster builds a CDS resource with a given clusterName that points
// the users to upstreamHost:upstreamPort
func MakeCluster(clusterName, upstreamHost string, upstreamPort uint32) *pb_cluster.Cluster {
	return &pb_cluster.Cluster{
		Name:                 clusterName,
		ConnectTimeout:       durationpb.New(5 * time.Second),
		ClusterDiscoveryType: &pb_cluster.Cluster_Type{Type: pb_cluster.Cluster_LOGICAL_DNS},
		LbPolicy:             pb_cluster.Cluster_ROUND_ROBIN,
		LoadAssignment:       makeEndpoint(clusterName, upstreamHost, upstreamPort),
		DnsLookupFamily:      pb_cluster.Cluster_V4_ONLY,
	}
}

func makeEndpoint(clusterName, upstreamHost string, upstreamPort uint32) *pb_endpoint.ClusterLoadAssignment {
	return &pb_endpoint.ClusterLoadAssignment{
		ClusterName: clusterName,
		Endpoints: []*pb_endpoint.LocalityLbEndpoints{{
			LbEndpoints: []*pb_endpoint.LbEndpoint{{
				HostIdentifier: &pb_endpoint.LbEndpoint_Endpoint{
					Endpoint: &pb_endpoint.Endpoint{
						Address: &pb_core.Address{
							Address: &pb_core.Address_SocketAddress{
								SocketAddress: &pb_core.SocketAddress{
									Protocol: pb_core.SocketAddress_TCP,
									Address:  upstreamHost,
									PortSpecifier: &pb_core.SocketAddress_PortValue{
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

// MakeHTTPListener builds a LDS resource that routes traffic to a given
// cluster.
func MakeHTTPListener(listenerName, cluster string) *pb_listener.Listener {
	any_route, _ := anypb.New(&pb_router.Router{})
	httpcm := &pb_hcm.HttpConnectionManager{
		RouteSpecifier: &pb_hcm.HttpConnectionManager_RouteConfig{
			RouteConfig: &pb_route.RouteConfiguration{
				VirtualHosts: []*pb_route.VirtualHost{
					{
						Domains: []string{"*"},
						Routes: []*pb_route.Route{
							{
								Match: &pb_route.RouteMatch{
									PathSpecifier: &pb_route.RouteMatch_Prefix{},
								},
								Action: &pb_route.Route_Route{
									Route: &pb_route.RouteAction{
										ClusterSpecifier: &pb_route.RouteAction_Cluster{
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
		HttpFilters: []*pb_hcm.HttpFilter{
			{
				Name: "router",
				ConfigType: &pb_hcm.HttpFilter_TypedConfig{
					TypedConfig: any_route,
				},
			},
		},
	}
	any_hcm, err := anypb.New(httpcm)
	if err != nil {
		panic(err)
	}
	return &pb_listener.Listener{
		Name: listenerName,
		ApiListener: &pb_listener.ApiListener{
			ApiListener: any_hcm,
		},
	}
}

// GenerateSnapshot prepares a new xDS config snapshot for serving to clients.
func GenerateSnapshot(upstreamHost string, upstreamPort uint32) *cache.Snapshot {
	snap, _ := cache.NewSnapshot(strconv.Itoa(snapshot_version), map[resource.Type][]types.Resource{
		resource.ClusterType:  {MakeCluster(ClusterName, upstreamHost, upstreamPort)},
		resource.ListenerType: {MakeHTTPListener(ListenerName, ClusterName)},
	})
	snapshot_version++
	return snap
}
