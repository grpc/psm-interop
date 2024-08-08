# A simple xDS control panel implementation for tests

## Overview

An xDS control panel implementation that provides special gRPC API for tests
that require a fine-grained control over the control plane behavior.

## Building

Docker requires that all the files are under the build root and this project
depends on `protos/grpc/testing/xdsconfig`, the build needs to be ran from
the root of `grpc/psm-interop` checkout.

```
docker build . -f docker/go-control-plane/Dockerfile
```

## Local development

Run the following command from this repository to generate code from the .proto
files:
```
protoc -I=. --go_out=docker/go-control-plane \
  protos/grpc/testing/xdsconfig/*.proto \
  --go-grpc_out=docker/go-control-plane/
```