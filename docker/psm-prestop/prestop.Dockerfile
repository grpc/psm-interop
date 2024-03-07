# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# TODO(sergiitk): Implement automated build, publish.

FROM alpine:latest as grpcurl

WORKDIR /tmp/grpcurl/
ENV GRPCURL_URI="https://github.com/fullstorydev/grpcurl/releases/download/v1.8.9/grpcurl_1.8.9_linux_x86_64.tar.gz" \
    GRPCURL_CHECKSUM="a422d1e8ad854a305c0dd53f2f2053da242211d3d1810e7addb40a041e309516"

ADD "$GRPCURL_URI" grpcurl.tar.gz
RUN \
  echo "$GRPCURL_CHECKSUM grpcurl.tar.gz" | sha256sum -c - \
  && tar -xf grpcurl.tar.gz

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
