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
	"sync"

	"google.golang.org/grpc"
	channelz "google.golang.org/grpc/channelz/service"
	"google.golang.org/grpc/reflection"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"

	v3discoverypb "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
	xdsconfigpb "github.com/grpc/psm-interop/docker/test-control-plane/grpc/interop/grpc_testing/xdsconfig"

	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
)

var (
	port   = flag.Uint("port", 3333, "Port to listen on")
	nodeid = flag.String("nodeid", "test-id", "Node ID")
)

type resourceKey struct {
	resourceType string
	resourceName string
}

// controlService provides a gRPC API to configure test-specific control plane
// behaviors.
type controlService struct {
	xdsconfigpb.UnsafeXdsConfigControlServiceServer
	version   uint32
	mu        sync.Mutex // Guards access to all fields listed below
	resources map[resourceKey]*proto.Message
	filters   map[string]map[string]bool
	cache     cache.SnapshotCache
}

// StopOnRequest instructs the control plane to stop if any xDS client request
// the specific resource.
func (srv *controlService) StopOnRequest(_ context.Context, req *xdsconfigpb.StopOnRequestRequest) (*xdsconfigpb.StopOnRequestResponse, error) {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	if val, ok := srv.filters[req.TypeUrl]; ok {
		val[req.Name] = true
	} else {
		srv.filters[req.TypeUrl] = map[string]bool{req.Name: true}
	}
	res := xdsconfigpb.StopOnRequestResponse{}
	for t, names := range srv.filters {
		for name := range names {
			res.Filters = append(res.Filters, &xdsconfigpb.StopOnRequestResponse_ResourceFilter{TypeUrl: t, Name: name})
		}
	}
	return &res, nil
}

// SetResources allows the test to provide a new or replace existing xDS
// resources. Notification will be sent to any control plane clients watching
// the resource being updated.
func (srv *controlService) SetResources(_ context.Context, req *xdsconfigpb.SetResourcesRequest) (*xdsconfigpb.SetResourcesResponse, error) {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	if len(req.Resources) > 0 {
		srv.version++
	}
	for _, resource := range req.Resources {
		key := resourceKey{resourceType: resource.TypeUrl, resourceName: resource.Name}
		contents := resource.Body
		if contents == nil {
			delete(srv.resources, key)
			continue
		}
		body, err := contents.UnmarshalNew()
		if err != nil {
			log.Printf("Failed to parse %s/%s: %v", key.resourceType, key.resourceName, err)
			continue
		}
		srv.resources[key] = &body
	}
	if err := srv.RefreshSnapshot(); err != nil {
		return nil, err
	}
	res := xdsconfigpb.SetResourcesResponse{}
	for key, message := range srv.resources {
		a, err := anypb.New(*message)
		if err != nil {
			log.Printf("Can not wrap resource %s/%s into any: %v", key.resourceType, key.resourceName, err)
		}
		res.Resource = append(res.Resource, a)
	}
	return &res, nil
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
	resources := map[resource.Type][]types.Resource{}
	for k, resource := range srv.resources {
		resources[k.resourceType] = append(resources[k.resourceType], *resource)
	}
	// Create the snapshot that we'll serve to Envoy
	snapshot, err := cache.NewSnapshot(fmt.Sprint(srv.version), resources)
	if err != nil {
		return err
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

// Main entry point. Configures and starts a gRPC server that serves xDS traffic
// and provides an interface for tests to manage control plane behavior.
func main() {
	flag.Parse()
	controlService := &controlService{version: 1,
		resources: map[resourceKey]*proto.Message{},
		filters:   map[string]map[string]bool{},
		cache:     cache.NewSnapshotCache(false, cache.IDHash{}, nil),
	}
	if err := controlService.RunServer(*port); err != nil {
		log.Fatalf("Server startup failed: %v\n", err)
	}
}
