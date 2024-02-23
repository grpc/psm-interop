FROM alpine:latest as tini

ENV TINI_VERSION v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod u+x /tini

FROM gcr.io/grpc-testing/xds-interop/cpp-server:v1.62.x

# tini serves as PID 1 and enables the server to properly respond to signals.
COPY --from=tini /tini /tini

ENTRYPOINT ["/tini", "-g", "-vv", "--",  "/xds_interop_server"]
