# Copyright 2025 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import datetime
import logging
import socket

import absl
from absl import flags
from absl.testing import absltest
from google.protobuf import message
from grpc_channelz.v1 import channelz_pb2
from typing_extensions import override

import framework
import framework.helpers.docker
import framework.helpers.logs
import framework.helpers.retryers
import framework.helpers.xds_resources
import framework.xds_flags
import framework.xds_k8s_testcase

logger = logging.getLogger(__name__)

_CONTROL_PLANE_IMAGE = flags.DEFINE_string(
    "control_plane_image",
    "us-docker.pkg.dev/grpc-testing/psm-interop/test-control-plane:latest",
    "Control plane (xDS config) server image",
)
_HOST_NAME = flags.DEFINE_string(
    "host_name",
    "host.docker.internal",
    "Host name all the services are bound on",
)
_NODE_ID = flags.DEFINE_string("node", "test-id", "Node ID")
_STATUS_TIMEOUT_MS = flags.DEFINE_integer(
    "status_timeout_ms",
    15000,
    "Duration (in ms) that the test will wait for xDS channel to change the status",
)
_STATUS_POLL_INTERVAL_MS = flags.DEFINE_integer(
    "status_poll_interval_ms", 300, "Channel status poll interval (in ms)"
)
_STATS_REQUEST_TIMEOUT_S = flags.DEFINE_integer(
    "stats_request_timeout_s",
    300,
    "Number of seconds the client will wait for the requested number of RPCs",
)
_LISTENER = "listener_0"

absl.flags.adopt_module_key_flags(framework.xds_k8s_testcase)


class FederationTest(absltest.TestCase):
    dockerInternalIp: str
    authority1_port: int
    authority2_port: int

    @classmethod
    @override
    def setUpClass(cls):
        # Use the host IP for when we need to use IP address and not the host
        # name, such as EDS resources
        cls.dockerInternalIp = socket.gethostbyname(socket.gethostname())

    @override
    def setUp(self):
        self.authority1_port = framework.helpers.docker.get_free_port()
        self.authority2_port = framework.helpers.docker.get_free_port()
        authorities = {
            "authority1": f"{_HOST_NAME.value}:{self.authority1_port}",
            "authority2": f"{_HOST_NAME.value}:{self.authority2_port}",
        }
        server_template = "xdstp://authority2/envoy.config.listener.v3.Listener/grpc/server/%s"
        # TODO: find a better location for the bootstrap file
        bootstrap = framework.helpers.docker.Bootstrap(
            framework.helpers.logs.log_dir_mkdir("bootstrap"),
            # Use an invalid domain for the default server, to validate that no
            # endpoint tries to use it
            servers=["invalid.:8080"],
            authorities=authorities,
            server_template=server_template,
        )
        self.process_manager = framework.helpers.docker.ProcessManager(
            bootstrap=bootstrap,
            node_id=_NODE_ID.value,
        )

    def start_client(self, authority: str, port: int, name: str = None):
        return framework.helpers.docker.Client(
            manager=self.process_manager,
            name=name or framework.xds_flags.CLIENT_NAME.value,
            port=port,
            url=f"xds://{authority}/{_LISTENER}",
            image=framework.xds_k8s_flags.CLIENT_IMAGE.value,
            stats_request_timeout_s=_STATS_REQUEST_TIMEOUT_S.value,
        )

    def start_control_plane(
        self, name: str, port: int, resources: list[message.Message]
    ):
        return framework.helpers.docker.ControlPlane(
            self.process_manager,
            name=name,
            port=port,
            initial_resources=framework.helpers.xds_resources.build_set_resources_request(
                resources
            ),
            image=_CONTROL_PLANE_IMAGE.value,
        )

    def start_server(self, name: str, port: int, maintenance_port: int):
        logger.debug('Starting server "%s"', name)
        return framework.helpers.docker.Server(
            name=name,
            port=port,
            maintenance_port=maintenance_port,
            image=framework.xds_k8s_flags.SERVER_IMAGE.value,
            manager=self.process_manager,
        )

    def assert_ads_connections(
        self,
        endpoint: (
            framework.helpers.docker.Client | framework.helpers.docker.Server
        ),
        authority1_status: channelz_pb2.ChannelConnectivityState,
        authority2_status: channelz_pb2.ChannelConnectivityState,
    ):
        self.assertEqual(
            endpoint.expect_channel_status(
                self.authority1_port,
                authority1_status,
                timeout=datetime.timedelta(
                    milliseconds=_STATUS_TIMEOUT_MS.value
                ),
                poll_interval=datetime.timedelta(
                    milliseconds=_STATUS_POLL_INTERVAL_MS.value
                ),
            ),
            authority1_status,
        )
        self.assertEqual(
            endpoint.expect_channel_status(
                self.authority2_port,
                authority2_status,
                timeout=datetime.timedelta(
                    milliseconds=_STATUS_TIMEOUT_MS.value
                ),
                poll_interval=datetime.timedelta(
                    milliseconds=_STATUS_POLL_INTERVAL_MS.value
                ),
            ),
            authority2_status,
        )

    def test_federation(self):
        # The goal is to test the federation feature by requiring both the
        # client and the server to contact two different authorities
        # to get their full configurations. Only authority1 has the client
        # Listener with the embedded RouteConfiguration, and only authority2
        # has the client Cluster and ClusterLoadAssignment.
        # The server resources are a little different: go-control-plane
        # requires that Listeners that refer to a RouteConfiguration be
        # provided along with that corresponding RouteConfiguration, so both
        # control planes have both server resources. But the Listener is
        # labeled as being available from authority2 and the RouteConfiguration
        # is labeled as being available from authority1, so the server should
        # contact both of them to get both resources. We try to verify this by
        # testing that both the server and the client have active connections
        # to both control plane servers.
        server_port = framework.helpers.docker.get_free_port()
        server_maintenance_port = framework.helpers.docker.get_free_port()
        client_port = framework.helpers.docker.get_free_port()
        listener_name = (
            f"xdstp://authority1/envoy.config.listener.v3.Listener/{_LISTENER}"
        )
        cluster_name = (
            "xdstp://authority2/envoy.config.cluster.v3.Cluster/cluster1"
        )
        endpoint_name = "xdstp://authority2/envoy.config.endpoint.v3.ClusterLoadAssignment/endpoint1"
        server_listener_name = f"xdstp://authority2/envoy.config.listener.v3.Listener/grpc/server/0.0.0.0:{server_port}"
        server_route_config_name = "xdstp://authority1/envoy.config.route.v3.RouteConfiguration/route_config1"
        listener = framework.helpers.xds_resources.build_listener(
            listener_name, cluster_name
        )
        cluster = framework.helpers.xds_resources.build_cluster(
            cluster_name, endpoint_name
        )
        endpoint = framework.helpers.xds_resources.build_endpoint(
            endpoint_name, self.dockerInternalIp, server_port
        )
        server_listener = framework.helpers.xds_resources.build_server_listener(
            server_listener_name, server_port, server_route_config_name
        )
        server_route_config = (
            framework.helpers.xds_resources.build_server_route_config(
                server_route_config_name
            )
        )
        authority1_resources = [listener, server_listener, server_route_config]
        authority2_resources = [
            cluster,
            endpoint,
            server_listener,
            server_route_config,
        ]
        control_plane1 = self.start_control_plane(
            name="authority1",
            port=self.authority1_port,
            resources=authority1_resources,
        )
        control_plane2 = self.start_control_plane(
            name="authority2",
            port=self.authority2_port,
            resources=authority2_resources,
        )
        server = self.start_server(
            "server1", server_port, server_maintenance_port
        )
        client = self.start_client("authority1", client_port)
        with control_plane1, control_plane2, server, client:
            self.assert_ads_connections(
                endpoint=client,
                authority1_status=channelz_pb2.ChannelConnectivityState.READY,
                authority2_status=channelz_pb2.ChannelConnectivityState.READY,
            )
            self.assert_ads_connections(
                endpoint=server,
                authority1_status=channelz_pb2.ChannelConnectivityState.READY,
                authority2_status=channelz_pb2.ChannelConnectivityState.READY,
            )
            stats = client.get_stats(10)
            self.assertEqual(stats.num_failures, 0)


if __name__ == "__main__":
    absltest.main()
