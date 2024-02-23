FROM alpine:latest as grpcurl

WORKDIR /tmp/grpcurl/
ADD https://github.com/fullstorydev/grpcurl/releases/download/v1.8.9/grpcurl_1.8.9_linux_x86_64.tar.gz .
RUN \
  echo "a422d1e8ad854a305c0dd53f2f2053da242211d3d1810e7addb40a041e309516 grpcurl_1.8.9_linux_x86_64.tar.gz" | sha256sum -c - \
  && tar -xf grpcurl_1.8.9_linux_x86_64.tar.gz

# ---

FROM alpine:latest

# Environment
ENV APP_DIR=/usr/src/psm-prestop
WORKDIR "$APP_DIR"

# Provision grpcurl binary
COPY --from=grpcurl /tmp/grpcurl/grpcurl .

# Provision protos and the init script
COPY protos/grpc/testing/*.proto ./protos/grpc/testing/
COPY docker/psm-prestop/prestop-init-volume.sh .

ENTRYPOINT ["./prestop-init-volume.sh"]
