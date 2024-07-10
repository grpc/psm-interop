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
	"strconv"
	"time"

	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/durationpb"

	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"

	clusterpb "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	corepb "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	endpointpb "github.com/envoyproxy/go-control-plane/envoy/config/endpoint/v3"
	listenerpb "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	routepb "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	routerpb "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/http/router/v3"
	hcmpb "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/http_connection_manager/v3"
)

const (
	ClusterName  = "example_proxy_cluster"
	ListenerName = "listener_0"
)

var snapshot_version int = 1;

// MakeCluster builds a CDS resource for serving to xDS clients
func MakeCluster(clusterName, upstreamHost string, upstreamPort uint32) *clusterpb.Cluster {
	return &clusterpb.Cluster{
		Name:                 clusterName,
		ConnectTimeout:       durationpb.New(5 * time.Second),
		ClusterDiscoveryType: &clusterpb.Cluster_Type{Type: clusterpb.Cluster_LOGICAL_DNS},
		LbPolicy:             clusterpb.Cluster_ROUND_ROBIN,
		LoadAssignment:       makeEndpoint(clusterName, upstreamHost, upstreamPort),
		DnsLookupFamily:      clusterpb.Cluster_V4_ONLY,
	}
}

func makeEndpoint(clusterName, upstreamHost string, upstreamPort uint32) *endpointpb.ClusterLoadAssignment {
	return &endpointpb.ClusterLoadAssignment{
		ClusterName: clusterName,
		Endpoints: []*endpointpb.LocalityLbEndpoints{{
			LbEndpoints: []*endpointpb.LbEndpoint{{
				HostIdentifier: &endpointpb.LbEndpoint_Endpoint{
					Endpoint: &endpointpb.Endpoint{
						Address: &corepb.Address{
							Address: &corepb.Address_SocketAddress{
								SocketAddress: &corepb.SocketAddress{
									Protocol: corepb.SocketAddress_TCP,
									Address:  upstreamHost,
									PortSpecifier: &corepb.SocketAddress_PortValue{
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

// MakeHTTPListener builds a LDS resource for serving to xDS clients
func MakeHTTPListener(listenerName, cluster string) *listenerpb.Listener {
	any_route, err := anypb.New(&routerpb.Router{})
	if err != nil {
		panic(err)
	}
	httpcm := &hcmpb.HttpConnectionManager{
		RouteSpecifier: &hcmpb.HttpConnectionManager_RouteConfig{
			RouteConfig: &routepb.RouteConfiguration{
				VirtualHosts: []*routepb.VirtualHost{
					{
						Domains: []string{"*"},
						Routes: []*routepb.Route{
							{
								Match: &routepb.RouteMatch{
									PathSpecifier: &routepb.RouteMatch_Prefix{},
								},
								Action: &routepb.Route_Route{
									Route: &routepb.RouteAction{
										ClusterSpecifier: &routepb.RouteAction_Cluster{
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
		HttpFilters: []*hcmpb.HttpFilter{
			{
				Name: "router",
				ConfigType: &hcmpb.HttpFilter_TypedConfig{
					TypedConfig: any_route,
				},
			},
		},
	}
	any_hcm, err := anypb.New(httpcm)
	if err != nil {
		panic(err)
	}
	return &listenerpb.Listener{
		Name: listenerName,
		ApiListener: &listenerpb.ApiListener{
			ApiListener: any_hcm,
		},
	}
}

// GenerateSnapshot prepares a new xDS config snapshot for serving to clients
func GenerateSnapshot(upstreamHost string, upstreamPort uint32) *cache.Snapshot {
	snap, _ := cache.NewSnapshot(strconv.Itoa(snapshot_version), map[resource.Type][]types.Resource{
		resource.ClusterType:  {MakeCluster(ClusterName, upstreamHost, upstreamPort)},
		resource.ListenerType: {MakeHTTPListener(ListenerName, ClusterName)},
	})
	snapshot_version++
	return snap
}
