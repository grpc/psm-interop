#[eostroukhov] TODO migrate to Alpine to decrease image size
FROM golang:1.22.3-bookworm

WORKDIR /usr/src/app

RUN apt-get update -y && apt-get dist-upgrade -y && apt-get upgrade -y
RUN apt install -y unzip
RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
RUN go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

ENV PATH="$PATH:$(go env GOPATH)/bin"

# Download proto zip
ENV PROTOC_ZIP=protoc-27.2-linux-x86_64.zip
RUN curl -OL https://github.com/protocolbuffers/protobuf/releases/download/v27.2/${PROTOC_ZIP}
RUN unzip -o ${PROTOC_ZIP} -d ./proto 
RUN chmod 755 -R ./proto/bin
ENV BASE=/usr
# Copy into path
RUN cp ./proto/bin/protoc ${BASE}/bin/
RUN cp -R ./proto/include/* ${BASE}/include/

# pre-copy/cache go.mod for pre-downloading dependencies and only redownloading them in subsequent builds if they change
COPY docker/go-control-plane/go.mod docker/go-control-plane/go.sum ./
RUN go mod download && go mod verify

COPY protos ./protos
RUN protoc -I=. --go_out=. protos/grpc/testing/xdsconfig/*.proto --go-grpc_out=.
COPY docker/go-control-plane .
RUN go build -v -o /usr/local/bin/fallback-control-plane .

ENTRYPOINT ["fallback-control-plane", "-debug"]