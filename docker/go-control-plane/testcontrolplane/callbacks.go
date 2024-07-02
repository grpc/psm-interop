package testcontrolplane

import (
	"context"
	"log"
	"os"
	"sync"

	core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	discovery "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
)

type Callbacks struct {
	Signal         chan struct{}
	Debug          bool
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
	log.Printf("server callbacks fetches=%d requests=%d responses=%d\n", cb.Fetches, cb.Requests, cb.Responses)
}

func (cb *Callbacks) OnStreamOpen(_ context.Context, id int64, typ string) error {
	if cb.Debug {
		log.Printf("stream %d open for %s\n", id, typ)
	}
	return nil
}
func (cb *Callbacks) OnStreamClosed(id int64, node *core.Node) {
	if cb.Debug {
		log.Printf("stream %d of node %s closed\n", id, node.Id)
	}
}

func (cb *Callbacks) OnDeltaStreamOpen(_ context.Context, id int64, typ string) error {
	if cb.Debug {
		log.Printf("delta stream %d open for %s\n", id, typ)
	}
	return nil
}
func (cb *Callbacks) OnDeltaStreamClosed(id int64, node *core.Node) {
	if cb.Debug {
		log.Printf("delta stream %d of node %s closed\n", id, node.Id)
	}
}

func (cb *Callbacks) OnStreamRequest(id int64, req *discovery.DiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.Requests++
	if cb.Signal != nil {
		close(cb.Signal)
		cb.Signal = nil
	}
	if cb.Debug {
		log.Printf("received request for %s on stream %d: %v:%v", req.GetTypeUrl(), id, req.VersionInfo, req.ResourceNames)
	}
	filtered := cb.Filters[req.GetTypeUrl()]
	if filtered != nil {
		for _, name := range req.ResourceNames {
			if filtered[name] {
				log.Printf("Self destructing: %s/%s", req.GetTypeUrl(), name)
				os.Exit(0)
			}
		}
	}
	return nil
}

func (cb *Callbacks) OnStreamResponse(ctx context.Context, id int64, req *discovery.DiscoveryRequest, res *discovery.DiscoveryResponse) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.Responses++
	if cb.Debug {
		log.Printf("responding to request for %s on stream %d", req.GetTypeUrl(), id)
	}
}

func (cb *Callbacks) OnStreamDeltaResponse(id int64, req *discovery.DeltaDiscoveryRequest, res *discovery.DeltaDiscoveryResponse) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.DeltaResponses++
}
func (cb *Callbacks) OnStreamDeltaRequest(int64, *discovery.DeltaDiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.DeltaRequests++
	if cb.Signal != nil {
		close(cb.Signal)
		cb.Signal = nil
	}

	return nil
}
func (cb *Callbacks) OnFetchRequest(context.Context, *discovery.DiscoveryRequest) error {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.Fetches++
	if cb.Signal != nil {
		close(cb.Signal)
		cb.Signal = nil
	}
	return nil
}
func (cb *Callbacks) OnFetchResponse(*discovery.DiscoveryRequest, *discovery.DiscoveryResponse) {}

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
