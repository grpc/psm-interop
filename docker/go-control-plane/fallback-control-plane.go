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
	"strconv"
	"sync"

	"github.com/eugeneo/fallback-control-plane/controlplane"
	pb_cs "github.com/eugeneo/fallback-control-plane/grpc/interop/grpc_testing/xdsconfig"

	pb_cluster "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	pb_listener "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/types/known/anypb"
)

var (
	port     = flag.Uint("port", 3333, "Port to listen on")
	nodeid   = flag.String("nodeid", "test-id", "Node ID")
	upstream = flag.String("upstream", "localhost:3000", "upstream server")
)

// controlService provides a gRPC API to configure test-specific control plane
// behaviors.
type controlService struct {
	pb_cs.UnsafeXdsConfigControlServiceServer
	version   uint32
	clusters  map[string]*pb_cluster.Cluster
	listeners map[string]*pb_listener.Listener
	cache     cache.SnapshotCache
	Cb        *controlplane.Callbacks
	mu        sync.Mutex
}

// StopOnRequest instructs the control plane to stop if any xDS client request
// the specific resource.
func (srv *controlService) StopOnRequest(_ context.Context, req *pb_cs.StopOnRequestRequest) (*pb_cs.StopOnRequestResponse, error) {
	srv.Cb.AddFilter(req.GetResourceType(), req.GetResourceName())
	res := pb_cs.StopOnRequestResponse{}
	for _, f := range srv.Cb.GetFilters() {
		res.Filters = append(res.Filters, &pb_cs.StopOnRequestResponse_ResourceFilter{ResourceType: f.ResourceType, ResourceName: f.ResourceName})
	}
	return &res, nil
}

// UpsertResources allows the test to provide a new or replace existing xDS
// resource. Notification will be sent to any control plane clients watching
// the resource being updated.
func (srv *controlService) UpsertResources(_ context.Context, req *pb_cs.UpsertResourcesRequest) (*pb_cs.UpsertResourcesResponse, error) {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	srv.version++
	listener := controlplane.ListenerName
	if req.Listener != nil {
		listener = *req.Listener
	}
	srv.clusters[req.Cluster] = controlplane.MakeCluster(req.Cluster, req.UpstreamHost, req.UpstreamPort)
	srv.listeners[listener] = controlplane.MakeHTTPListener(listener, req.Cluster)
	snapshot, err := srv.MakeSnapshot()
	if err != nil {
		log.Printf("snapshot inconsistency: %+v\n", err)
		return nil, err
	}
	srv.cache.SetSnapshot(context.Background(), *nodeid, snapshot)
	res := &pb_cs.UpsertResourcesResponse{}
	for _, l := range srv.listeners {
		a, err := anypb.New(l)
		if err != nil {
			panic(err)
		}
		res.Resource = append(res.Resource, a)
	}
	for _, c := range srv.clusters {
		a, err := anypb.New(c)
		if err != nil {
			panic(err)
		}
		res.Resource = append(res.Resource, a)
	}
	return res, nil
}

// MakeSnapshot builds xDS configuration snapshot that will be served
// to clients.
func (srv *controlService) MakeSnapshot() (*cache.Snapshot, error) {
	listeners := make([]types.Resource, len(srv.listeners))
	i := 0
	for _, l := range srv.listeners {
		listeners[i] = l
		i++
	}
	clusters := make([]types.Resource, len(srv.clusters))
	i = 0
	for _, c := range srv.clusters {
		clusters[i] = c
		i++
	}
	resources := map[resource.Type][]types.Resource{resource.ListenerType: listeners, resource.ClusterType: clusters}
	// Create the snapshot that we'll serve to Envoy
	snapshot, error := cache.NewSnapshot(fmt.Sprint(srv.version), resources)
	if error != nil {
		return nil, error
	}
	if err := snapshot.Consistent(); err != nil {
		log.Printf("snapshot inconsistency: %+v\n", err)
		for _, r := range snapshot.Resources {
			for name, resource := range r.Items {
				bytes, err := prototext.MarshalOptions{Multiline: true}.Marshal(resource.Resource)
				if err != nil {
					log.Printf("Can't marshal %s\n", name)
				} else {
					log.Printf("Resource: %s\n%s\n",
						resource.Resource,
						string(bytes))
				}
			}
		}
		return nil, err
	}
	log.Printf("will serve snapshot:\n")
	for _, values := range snapshot.Resources {
		for name, item := range values.Items {
			text, err := prototext.MarshalOptions{Multiline: true}.Marshal(item.Resource)
			if err != nil {
				log.Printf("Resource %+v, error: %+v\n", name, err)
			} else {
				log.Printf("%+v => %+v\n", name, string(text))
			}
		}
	}
	return snapshot, nil
}

// Main entry point. Configures and starts a gRPC server that serves xDS traffic
// and provides an interface for tests to manage control plane behavior.
func main() {
	flag.Parse()
	host, upstreamPort, err := net.SplitHostPort(*upstream)
	if err != nil {
		log.Fatalf("Incorrect upstream host name: %+v: %+v\n", upstream, err)
	}
	parsedUpstreamPort, err := strconv.Atoi(upstreamPort)
	if err != nil || parsedUpstreamPort <= 0 {
		log.Fatalf("Not a valid port number: %+v: %+v\n", upstreamPort, err)
	}
	cb := &controlplane.Callbacks{Filters: make(map[string]map[string]bool)}
	// The type needs to be checked
	controlService := &controlService{Cb: cb, version: 1,
		clusters:  map[string]*pb_cluster.Cluster{controlplane.ListenerName: controlplane.MakeCluster(controlplane.ClusterName, host, uint32(parsedUpstreamPort))},
		listeners: map[string]*pb_listener.Listener{controlplane.ListenerName: controlplane.MakeHTTPListener(controlplane.ListenerName, controlplane.ClusterName)},
		cache:     cache.NewSnapshotCache(false, cache.IDHash{}, nil),
	}
	// Create a cache
	snapshot, err := controlService.MakeSnapshot()
	if err != nil {
		log.Fatalf("snapshot error %q for %+v\n", err, snapshot)
	}
	// Add the snapshot to the cache
	if err := controlService.cache.SetSnapshot(context.Background(), *nodeid, snapshot); err != nil {
		log.Fatalf("snapshot error %q for %+v\n", err, snapshot)
	}

	// Run the xDS server
	ctx := context.Background()
	srv := server.NewServer(ctx, controlService.cache, server.CallbackFuncs{
		StreamRequestFunc: cb.OnStreamRequest,
	})
	err = controlplane.RunServer(srv, controlService, *port)
	if err != nil {
		log.Fatalf("Server startup failed: %q\n", err)
	}
}
