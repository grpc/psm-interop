module github.com/eugeneo/fallback-control-plane

go 1.20

require (
	github.com/envoyproxy/go-control-plane v0.12.0
	google.golang.org/grpc v1.64.0
	google.golang.org/protobuf v1.34.2
)

require (
	cel.dev/expr v0.15.0 // indirect
	github.com/census-instrumentation/opencensus-proto v0.4.1 // indirect
	github.com/cncf/xds/go v0.0.0-20240423153145-555b57ec207b // indirect
	github.com/envoyproxy/protoc-gen-validate v1.0.4 // indirect
	github.com/golang/protobuf v1.5.4 // indirect
	github.com/planetscale/vtprotobuf v0.6.1-0.20240319094008-0393e58bdf10 // indirect
	golang.org/x/net v0.26.0 // indirect
	golang.org/x/sys v0.21.0 // indirect
	golang.org/x/text v0.16.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20240604185151-ef581f913117 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20240604185151-ef581f913117 // indirect
)

// replace github.com/envoyproxy/go-control-plane v0.12.0 => ../go-control-plane
