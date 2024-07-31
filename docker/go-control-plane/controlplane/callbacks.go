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
	"log"
	"os"
	"sync"

	v3discoverypb "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
)

// Implementation of the server.Callbacks interface that implements the behavior
// required by our tests.
type Callbacks struct {
	Signal         chan struct{}
	Filters        map[string]map[string]bool
	mu             sync.Mutex
}

// Abruptly stops the server when the client requests a resource that the test
// marked as one that should trigger this behavior
func (cb *Callbacks) OnStreamRequest(id int64, req *v3discoverypb.DiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
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
