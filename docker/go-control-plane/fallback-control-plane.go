package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"

	cs "github.com/eugeneo/fallback-control-plane/grpc/interop/grpc_testing/xdsconfig"
	"github.com/eugeneo/fallback-control-plane/testcontrolplane"

	cluster "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	listener "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
	"github.com/envoyproxy/go-control-plane/pkg/server/v3"
	example "github.com/eugeneo/fallback-control-plane/envoy"
	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/types/known/anypb"
)

var (
	l        example.Logger
	port     = flag.Uint("port", 3333, "Port to listen on")
	nodeID   = flag.String("node", "test-id", "Node ID")
	upstream = flag.String("upstream", "localhost:3000", "upstream server")
)

// init configures debug logging based on command line flag value
func init() {
	l = example.Logger{}
	flag.BoolVar(&l.Debug, "debug", false, "Enable xDS server debug logging")
}

// ControlService provides a gRPC API to configure test-specific control plane
// behaviors
type ControlService struct {
	cs.UnsafeXdsConfigControlServiceServer
	version   uint32
	clusters  map[string]*cluster.Cluster
	listeners map[string]*listener.Listener
	cache     cache.SnapshotCache
	Cb        *testcontrolplane.Callbacks
	mu        sync.Mutex
}

// StopOnRequest instructs the control plane to stop if any xDS client request
// the specific resource.
func (srv *ControlService) StopOnRequest(_ context.Context, req *cs.StopOnRequestRequest) (*cs.StopOnRequestResponse, error) {
	srv.Cb.AddFilter(req.GetResourceType(), req.GetResourceName())
	res := cs.StopOnRequestResponse{}
	for _, f := range srv.Cb.GetFilters() {
		res.Filters = append(res.Filters, &cs.StopOnRequestResponse_ResourceFilter{ResourceType: f.ResourceType, ResourceName: f.ResourceName})
	}
	return &res, nil
}

// UpsertResources allows the test to provide a new or replace existing xDS
// resource. Notification will be sent to any control plane clients watching
// the resource being updated.
func (srv *ControlService) UpsertResources(_ context.Context, req *cs.UpsertResourcesRequest) (*cs.UpsertResourcesResponse, error) {
	srv.mu.Lock()
	defer srv.mu.Unlock()
	srv.version++
	listener := example.ListenerName
	if req.Listener != nil {
		listener = *req.Listener
	}
	srv.clusters[req.Cluster] = example.MakeCluster(req.Cluster, req.UpstreamHost, req.UpstreamPort)
	srv.listeners[listener] = example.MakeHTTPListener(listener, req.Cluster)
	snapshot, err := srv.MakeSnapshot()
	if err != nil {
		l.Errorf("snapshot inconsistency: %+v", err)
		return nil, err
	}
	srv.cache.SetSnapshot(context.Background(), *nodeID, snapshot)
	res := &cs.UpsertResourcesResponse{}
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
func (srv *ControlService) MakeSnapshot() (*cache.Snapshot, error) {
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
		l.Errorf("snapshot inconsistency: %+v", err)
		for _, r := range snapshot.Resources {
			for name, resource := range r.Items {
				bytes, err := prototext.MarshalOptions{Multiline: true}.Marshal(resource.Resource)
				if err != nil {
					l.Errorf("Can't marshal %s", name)
				} else {
					l.Errorf("Resource: %s\n%s",
						resource.Resource,
						string(bytes))
				}
			}
		}
		return nil, err
	}
	l.Debugf("will serve snapshot:")
	for _, values := range snapshot.Resources {
		for name, item := range values.Items {
			text, err := prototext.MarshalOptions{Multiline: true}.Marshal(item.Resource)
			if err != nil {
				l.Errorf("Resource %+v, error: %+v", name, err)
			} else {
				l.Debugf("%+v => %+v", name, string(text))
			}
		}
	}
	return snapshot, nil
}

// main entry point. Configures and starts a gRPC server that serves xDS traffic
// and provides an interface for tests to manage control plane behavior
func main() {
	flag.Parse()
	sep := strings.LastIndex(*upstream, ":")
	if sep < 0 {
		l.Errorf("Incorrect upstream host name: %+v", upstream)
		os.Exit(1)
	}
	upstreamPort, err := strconv.Atoi((*upstream)[sep+1:])
	if err != nil {
		l.Errorf("Bad upstream port: %+v\n%+v", (*upstream)[sep+1:], err)
		os.Exit(1)
	}
	cb := &testcontrolplane.Callbacks{Debug: l.Debug, Filters: make(map[string]map[string]bool)}
	// The type needs to be checked
	controlService := &ControlService{Cb: cb, version: 1,
		clusters:  map[string]*cluster.Cluster{example.ListenerName: example.MakeCluster(example.ClusterName, (*upstream)[:sep], uint32(upstreamPort))},
		listeners: map[string]*listener.Listener{example.ListenerName: example.MakeHTTPListener(example.ListenerName, example.ClusterName)},
		cache:     cache.NewSnapshotCache(false, cache.IDHash{}, l),
	}
	// Create a cache
	snapshot, err := controlService.MakeSnapshot()
	if err != nil {
		l.Errorf("snapshot error %q for %+v", err, snapshot)
		os.Exit(1)
	}
	// Add the snapshot to the cache
	if err := controlService.cache.SetSnapshot(context.Background(), *nodeID, snapshot); err != nil {
		l.Errorf("snapshot error %q for %+v", err, snapshot)
		os.Exit(1)
	}

	// Run the xDS server
	ctx := context.Background()
	srv := server.NewServer(ctx, controlService.cache, cb)
	example.RunServer(srv, controlService, *port)
}
