/*
 *
 * Copyright 2024 gRPC authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 */

// The fallback_control_plane executable is an xDS control plane for testing
// purposes. This control plane serves xDS traffic and exposes an API for test
// scripts to perform fine-grained configuration or trigger specific control
// plane behaviors.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"strconv"
	"sync"

	"google.golang.org/grpc"
	channelz "google.golang.org/grpc/channelz/service"
	"google.golang.org/grpc/reflection"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/anypb"

	"github.com/grpc/psm-interop/docker/go-control-plane/controlplane"
	xdsconfigpb "github.com/grpc/psm-interop/docker/go-control-plane/grpc/interop/grpc_testing/xdsconfig"

	v3clusterpb "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	v3listenerpb "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	v3discoverypb "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"

	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
)

var (
	port     = flag.Uint("port", 3333, "Port to listen on")
	nodeid   = flag.String("nodeid", "test-id", "Node ID")
	upstream = flag.String("upstream", "localhost:3000", "upstream server")
)

type Filter struct {
	ResourceType string
	ResourceName string
}

// controlService provides a gRPC API to configure test-specific control plane
// behaviors.
type controlService struct {
	xdsconfigpb.UnsafeXdsConfigControlServiceServer
	version   uint32
	mu        sync.Mutex // Guards access to all fields listed below
	clusters  map[string]*v3clusterpb.Cluster
	listeners map[string]*v3listenerpb.Listener
	filters   map[string]map[string]bool
	cache     cache.SnapshotCache
}

// StopOnRequest instructs the control plane to stop if any xDS client request
// the specific resource.
func (srv *controlService) StopOnRequest(_ context.Context, req *xdsconfigpb.StopOnRequestRequest) (*xdsconfigpb.StopOnRequestResponse, error) {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	if val, ok := srv.filters[req.GetResourceType()]; ok {
		val[req.GetResourceName()] = true
	} else {
		srv.filters[req.GetResourceType()] = map[string]bool{req.GetResourceName(): true}
	}
	res := xdsconfigpb.StopOnRequestResponse{}
	for t, names := range srv.filters {
		for name, _ := range names {
			res.Filters = append(res.Filters, &xdsconfigpb.StopOnRequestResponse_ResourceFilter{ResourceType: t, ResourceName: name})
		}
	}
	return &res, nil
}

// UpsertResources allows the test to provide a new or replace existing xDS
// resource. Notification will be sent to any control plane clients watching
// the resource being updated.
func (srv *controlService) UpsertResources(_ context.Context, req *xdsconfigpb.UpsertResourcesRequest) (*xdsconfigpb.UpsertResourcesResponse, error) {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	srv.version++
	listener := controlplane.ListenerName
	if req.Listener != nil {
		listener = *req.Listener
	}
	srv.clusters[req.Cluster] = controlplane.MakeCluster(req.Cluster, req.UpstreamHost, req.UpstreamPort)
	srv.listeners[listener] = controlplane.MakeHTTPListener(listener, req.Cluster)
	if err := srv.RefreshSnapshot(); err != nil {
		return nil, err
	}
	res := &xdsconfigpb.UpsertResourcesResponse{}
	for _, l := range srv.listeners {
		a, err := anypb.New(l)
		if err != nil {
			log.Fatalf("Failed to convert listener %v to pb: %v\n", l, err)
		}
		res.Resource = append(res.Resource, a)
	}
	for _, c := range srv.clusters {
		a, err := anypb.New(c)
		if err != nil {
			log.Fatalf("Failed to convert cluster %v to pb: %v\n", c, err)
		}
		res.Resource = append(res.Resource, a)
	}
	return res, nil
}

// Abruptly stops the server when the client requests a resource that the test
// marked as one that should trigger this behavior
func (srv *controlService) onStreamRequest(id int64, req *v3discoverypb.DiscoveryRequest) error {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	log.Printf("Received request for %s on stream %d: %v:%v\n", req.GetTypeUrl(), id, req.VersionInfo, req.ResourceNames)
	filtered := srv.filters[req.GetTypeUrl()]
	if filtered != nil {
		for _, name := range req.ResourceNames {
			if filtered[name] {
				log.Printf("Self destructing: %s/%s\n", req.GetTypeUrl(), name)
				os.Exit(0)
			}
		}
	}
	return nil
}

func (srv *controlService) RefreshSnapshot() error {
	var listeners []types.Resource
	for _, l := range srv.listeners {
		listeners = append(listeners, l)
	}
	var clusters []types.Resource
	for _, c := range srv.clusters {
		clusters = append(clusters, c)
	}
	resources := map[resource.Type][]types.Resource{resource.ListenerType: listeners, resource.ClusterType: clusters}
	// Create the snapshot that we'll serve to Envoy
	snapshot, err := cache.NewSnapshot(fmt.Sprint(srv.version), resources)
	if err != nil {
		return err
	}
	log.Printf("Snapshot contents:\n")
	for _, values := range snapshot.Resources {
		for name, item := range values.Items {
			text, err := protojson.MarshalOptions{Multiline: true}.Marshal(item.Resource)
			if err != nil {
				log.Printf("Resource %v, error: %v\n", name, err)
				continue
			}
			log.Printf("%v => %v\n", name, string(text))
		}
	}
	if err := snapshot.Consistent(); err != nil {
		log.Printf("Snapshot inconsistency: %v\n", err)
		return err
	}
	// Add the snapshot to the cache
	if err := srv.cache.SetSnapshot(context.Background(), *nodeid, snapshot); err != nil {
		log.Printf("Snapshot error %v for %v\n", err, snapshot)
		return err
	}
	return nil
}


func (srv *controlService) RunServer(port uint) error {
	if err := srv.RefreshSnapshot(); err != nil {
		log.Fatalf("Failed to refresh snapshot: %v\n", err)
	}
	// Run the xDS server
	server := server.NewServer(context.Background(), srv.cache, server.CallbackFuncs{
		StreamRequestFunc: srv.onStreamRequest,
	})
	lis, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
	if err != nil {
		return err
	}
	grpcServer := grpc.NewServer()
	reflection.Register(grpcServer)
	channelz.RegisterChannelzServiceToServer(grpcServer)
	xdsconfigpb.RegisterXdsConfigControlServiceServer(grpcServer, srv)
	v3discoverypb.RegisterAggregatedDiscoveryServiceServer(grpcServer, server)
	log.Printf("Management server listening on %d\n", port)
	if err = grpcServer.Serve(lis); err != nil {
		log.Println(err)
	}
	return nil
}

func parseHostPort(host_port string) (string, uint32, error) {
	host, upstreamPort, err := net.SplitHostPort(*upstream)
	if err != nil {
		return "", 0, fmt.Errorf("Incorrect upstream host name: %s: %v\n", host_port, err)
	}
	parsedUpstreamPort, err := strconv.Atoi(upstreamPort)
	if err != nil || parsedUpstreamPort <= 0 {
		return "", 0, fmt.Errorf("Not a valid port number: %d: %v\n", upstreamPort, err)
	}
	return host, uint32(parsedUpstreamPort), nil
}


// Main entry point. Configures and starts a gRPC server that serves xDS traffic
// and provides an interface for tests to manage control plane behavior.
func main() {
	flag.Parse()
	host, upstreamPort, err := parseHostPort(*upstream)
	if err != nil {
		log.Fatalf("Incorrect upstream host name: %s: %v\n", upstream, err)
	}
	initial_cds := controlplane.MakeCluster(controlplane.ClusterName, host, upstreamPort)
	initial_lds := controlplane.MakeHTTPListener(controlplane.ListenerName, controlplane.ClusterName)
	controlService := &controlService{version: 1,
		clusters:  map[string]*v3clusterpb.Cluster{controlplane.ListenerName: initial_cds},
		listeners: map[string]*v3listenerpb.Listener{controlplane.ListenerName: initial_lds},
		filters:   map[string]map[string]bool{},
		cache:     cache.NewSnapshotCache(false, cache.IDHash{}, nil),
	}
	if err := controlService.RunServer(*port); err != nil {
		log.Fatalf("Server startup failed: %v\n", err)
	}
}
