FROM fullstorydev/grpcurl:latest as grpcurl

FROM alpine:latest

# Environment
ENV APP_DIR=/usr/src/psm-prestop
WORKDIR "$APP_DIR"

# Provision grpcurl binary
COPY --from=grpcurl /bin/grpcurl .

# Provision protos and the init script
COPY protos/grpc/testing/*.proto ./protos/grpc/testing
COPY docker/psm-prestop/prestop-init-volume.sh .

ENTRYPOINT ["./prestop-init-volume.sh"]
