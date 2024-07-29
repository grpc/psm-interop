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
	"context"
	"fmt"
	"log"
	"net"
	"time"

	"google.golang.org/grpc"
	channelz "google.golang.org/grpc/channelz/service"
	"google.golang.org/grpc/reflection"

	cs "github.com/eugeneo/fallback-control-plane/grpc/interop/grpc_testing/xdsconfig"

	pb_discovery "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
	"github.com/envoyproxy/go-control-plane/pkg/test/v3"
)

const (
	grpcKeepaliveTime        = 30 * time.Second
	grpcKeepaliveTimeout     = 5 * time.Second
	grpcKeepaliveMinTime     = 30 * time.Second
	grpcMaxConcurrentStreams = 5
)

// Server serves xDS traffic.
type Server struct {
	xdsserver server.Server
}

// NewServer creates a new instance of the Server struct.
func NewServer(ctx context.Context, cache cache.Cache, cb *test.Callbacks) *Server {
	srv := server.NewServer(ctx, cache, cb)
	return &Server{srv}
}

// Registers gRPC services needed to serve xDS traffic.
func registerServer(grpcServer *grpc.Server, server server.Server) {
	// register services
	pb_discovery.RegisterAggregatedDiscoveryServiceServer(grpcServer, server)
}

// RunServer starts an xDS server at the given port. Blocks while the server is
// running
func RunServer(srv server.Server, controlService cs.XdsConfigControlServiceServer, port uint) error {
	// gRPC golang library sets a very small upper bound for the number gRPC/h2
	// streams over a single TCP connection. If a proxy multiplexes requests over
	// a single connection to the management server, then it might lead to
	// availability problems. Keepalive timeouts based on connection_keepalive parameter https://www.envoyproxy.io/docs/envoy/latest/configuration/overview/examples#dynamic
	grpcServer := grpc.NewServer()
	reflection.Register(grpcServer)
	channelz.RegisterChannelzServiceToServer(grpcServer)
	cs.RegisterXdsConfigControlServiceServer(grpcServer, controlService)
	lis, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
	if err != nil {
		return err
	}
	registerServer(grpcServer, srv)
	log.Printf("management server listening on %d\n", port)
	if err = grpcServer.Serve(lis); err != nil {
		log.Println(err)
	}
	return nil
}
