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

// Package controlplane provides components for the common control plane used
// for testing
package controlplane

import (
	"context"
	"log"
	"os"
	"sync"

	pb_core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	pb_discovery "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
)

// Implementation of the server.Callbacks interface that implements the behavior
// required by our tests.
type Callbacks struct {
	Signal         chan struct{}
	Fetches        int
	Requests       int
	Responses      int
	DeltaRequests  int
	DeltaResponses int
	Filters        map[string]map[string]bool
	mu             sync.Mutex
}

var _ server.Callbacks = &Callbacks{}

func (cb *Callbacks) Report() {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	log.Printf("Server callbacks fetches=%d requests=%d responses=%d\n", cb.Fetches, cb.Requests, cb.Responses)
}

func (cb *Callbacks) OnStreamOpen(_ context.Context, id int64, typ string) error {
	log.Printf("Stream %d open for %s\n", id, typ)
	return nil
}

func (cb *Callbacks) OnStreamClosed(id int64, node *pb_core.Node) {
	log.Printf("Stream %d of node %s closed\n", id, node.Id)
}

func (cb *Callbacks) OnDeltaStreamOpen(_ context.Context, id int64, typ string) error {
	log.Printf("Delta stream %d open for %s\n", id, typ)
	return nil
}

func (cb *Callbacks) OnDeltaStreamClosed(id int64, node *pb_core.Node) {
	log.Printf("Delta stream %d of node %s closed\n", id, node.Id)
}

// Abruptly stops the server when the client requests a resource that the test
// marked as one that should trigger this behavior
func (cb *Callbacks) OnStreamRequest(id int64, req *pb_discovery.DiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.Requests++
	if cb.Signal != nil {
		close(cb.Signal)
		cb.Signal = nil
	}
	log.Printf("Received request for %s on stream %d: %v:%v\n", req.GetTypeUrl(), id, req.VersionInfo, req.ResourceNames)
	filtered := cb.Filters[req.GetTypeUrl()]
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

func (cb *Callbacks) OnStreamResponse(ctx context.Context, id int64, req *pb_discovery.DiscoveryRequest, res *pb_discovery.DiscoveryResponse) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.Responses++
	log.Printf("Responding to request for %s on stream %d\n", req.GetTypeUrl(), id)
}

func (cb *Callbacks) OnStreamDeltaResponse(id int64, req *pb_discovery.DeltaDiscoveryRequest, res *pb_discovery.DeltaDiscoveryResponse) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.DeltaResponses++
}

func (cb *Callbacks) OnStreamDeltaRequest(int64, *pb_discovery.DeltaDiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.DeltaRequests++
	if cb.Signal != nil {
		close(cb.Signal)
		cb.Signal = nil
	}
	return nil
}

func (cb *Callbacks) OnFetchRequest(context.Context, *pb_discovery.DiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.Fetches++
	if cb.Signal != nil {
		close(cb.Signal)
		cb.Signal = nil
	}
	return nil
}

func (cb *Callbacks) OnFetchResponse(*pb_discovery.DiscoveryRequest, *pb_discovery.DiscoveryResponse) {}

// Adds a resource type/name to the list of resources that stop the server
// if a client requests them
func (cb *Callbacks) AddFilter(resource_type string, resource_name string) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	if val, ok := cb.Filters[resource_type]; ok {
		val[resource_name] = true
	} else {
		cb.Filters[resource_type] = map[string]bool{resource_name: true}
	}
}

type Filter struct {
	ResourceType string
	ResourceName string
}

// Returns a list of resource name/types that stop the server when requested
func (cb *Callbacks) GetFilters() []Filter {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	result := []Filter{}
	for t, names := range cb.Filters {
		for name, b := range names {
			if b {
				result = append(result, Filter{ResourceType: t, ResourceName: name})
			}
		}
	}
	return result
}
