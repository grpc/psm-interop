# Copyright 2024 gRPC authors.
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
import time
import unittest

import absl
from absl import flags
from absl.testing import absltest

import framework
import framework.helpers.docker
import framework.helpers.logs
import framework.helpers.retryers
import framework.helpers.xds_resources
import framework.xds_flags
import framework.xds_k8s_testcase

from grpc_channelz.v1 import channelz_pb2

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

_LISTENER = "listener_0"

absl.flags.adopt_module_key_flags(framework.xds_k8s_testcase)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


class FallbackTest(absltest.TestCase):
    bootstrap: framework.helpers.docker.Bootstrap = None

    @staticmethod
    def setUpClass():
        FallbackTest.bootstrap = framework.helpers.docker.Bootstrap(
            framework.helpers.logs.log_dir_mkdir("bootstrap"),
            primary_port=get_free_port(),
            fallback_port=get_free_port(),
            host_name=_HOST_NAME.value,
        )

    def setUp(self):
        logger.info("Starting %s", self.id())
        self.process_manager = framework.helpers.docker.ProcessManager(
            bootstrap=FallbackTest.bootstrap,
            node_id=_NODE_ID.value,
        )

    def start_client(self, port: int = None, name: str = None):
        logger.debug("Starting client process")
        return framework.helpers.docker.Client(
            manager=self.process_manager,
            name=name or framework.xds_flags.CLIENT_NAME.value,
            port=port or get_free_port(),
            url=f"xds:///{_LISTENER}",
            image=framework.xds_k8s_flags.CLIENT_IMAGE.value,
        )

    def start_control_plane(
        self, name: str, port: int, upstream_port: int, cluster_name=None
    ):
        logger.debug('Starting control plane "%s"', name)
        return framework.helpers.docker.ControlPlane(
            self.process_manager,
            name=name,
            port=port,
            initial_resources=framework.helpers.xds_resources.build_listener_and_cluster(
                listener_name=_LISTENER,
                cluster_name=cluster_name or f"initial_cluster_for_{name}",
                upstream_host=_HOST_NAME.value,
                upstream_port=upstream_port,
            ),
            image=_CONTROL_PLANE_IMAGE.value,
        )

    def start_server(self, name: str, port: int = None):
        logger.debug('Starting server "%s"', name)
        port = get_free_port() if port is None else port
        return framework.helpers.docker.GrpcProcess(
            name=name,
            port=port,
            image=framework.xds_k8s_flags.SERVER_IMAGE_CANONICAL.value,
            manager=self.process_manager,
            ports={8080: port},
            command=[],
        )

    def assert_ads_connections(
        self,
        client: framework.helpers.docker.Client,
        primary_status: channelz_pb2.ChannelConnectivityState,
        fallback_status: channelz_pb2.ChannelConnectivityState,
    ):
        self.assertEqual(
            client.expect_channel_status(
                self.bootstrap.primary_port,
                primary_status,
                timeout=datetime.timedelta(
                    milliseconds=_STATUS_TIMEOUT_MS.value
                ),
                poll_interval=datetime.timedelta(
                    milliseconds=_STATUS_POLL_INTERVAL_MS.value
                ),
            ),
            primary_status,
        )
        self.assertEqual(
            client.expect_channel_status(
                self.bootstrap.fallback_port,
                fallback_status,
                timeout=datetime.timedelta(
                    milliseconds=_STATUS_TIMEOUT_MS.value
                ),
                poll_interval=datetime.timedelta(
                    milliseconds=_STATUS_POLL_INTERVAL_MS.value
                ),
            ),
            fallback_status,
        )

    @unittest.skip("Boop")
    def test_fallback_on_startup(self):
        primary_port = self.bootstrap.ports[0]
        fallback_port = self.bootstrap.ports[1]
        with (
            self.start_server(name="server1") as server1,
            self.start_server(name="server2") as server2,
            self.start_client() as client,
        ):
            self.assertTrue(
                client.expect_message_in_output(
                    "UNAVAILABLE: xDS channel for server"
                )
            )
            self.assertEqual(client.get_stats(5).num_failures, 5)
            # Fallback control plane start, send traffic to server2
            with self.start_control_plane(
                name="fallback_xds_config",
                port=fallback_port,
                upstream_port=server2.port,
            ):
                self.assert_ads_connection(
                    client=client,
                    port=fallback_port,
                    expected_status=channelz_pb2.ChannelConnectivityState.READY,
                )
                stats = client.get_stats(5)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Primary control plane start. Will use it
                with self.start_control_plane(
                    name="primary_xds_config",
                    port=primary_port,
                    upstream_port=server1.port,
                ):
                    self.assert_ads_connection(
                        client=client,
                        port=primary_port,
                        expected_status=channelz_pb2.ChannelConnectivityState.READY,
                    )
                    # No connection to fallback server
                    self.assert_ads_connection(
                        client=client,
                        port=fallback_port,
                        expected_status=None,
                    )
                    stats = client.get_stats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)
                self.assert_ads_connection(
                    client=client,
                    port=primary_port,
                    expected_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                )
                # Primary control plane down, cached value is used
                stats = client.get_stats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertEqual(stats.rpcs_by_peer["server1"], 5)
            self.assert_ads_connection(
                client=client,
                port=fallback_port,
                expected_status=None,
            )
            # Fallback control plane down, cached value is used
            stats = client.get_stats(5)
            self.assertEqual(stats.num_failures, 0)
            self.assertEqual(stats.rpcs_by_peer["server1"], 5)

    def test_fallback_mid_startup(self):
        primary_port = self.bootstrap.primary_port
        fallback_port = self.bootstrap.fallback_port
        # Run the mesh, excluding the client
        with (
            self.start_server(name="server1") as server1,
            self.start_server(name="server2") as server2,
            self.start_control_plane(
                "primary_xds_config_run_1",
                port=primary_port,
                upstream_port=server1.port,
                cluster_name="cluster_name",
            ) as primary,
            self.start_control_plane(
                "fallback_xds_config",
                port=fallback_port,
                upstream_port=server2.port,
            ),
        ):
            primary.stop_on_resource_request(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "cluster_name",
            )
            # Run client
            with self.start_client() as client:
                self.assert_ads_connections(
                    client,
                    primary_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                    fallback_status=channelz_pb2.ChannelConnectivityState.READY,
                )
                # Secondary xDS config start, send traffic to server2
                stats = client.get_stats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Rerun primary control plane
                with self.start_control_plane(
                    "primary_xds_config_run_2",
                    port=primary_port,
                    upstream_port=server1.port,
                ):
                    self.assert_ads_connections(
                        client,
                        primary_status=channelz_pb2.ChannelConnectivityState.READY,
                        fallback_status=None,
                    )
                    stats = client.get_stats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)

    @unittest.skip("Boop")
    def test_fallback_mid_update(self):
        with (
            self.start_server(name="server1") as server1,
            self.start_server(name="server2") as server2,
            self.start_server(name="server3") as server3,
            self.start_control_plane(
                "primary_xds_config_run_1", 0, server1.port
            ) as primary,
            self.start_control_plane("fallback_xds_config", 1, server2.port),
            self.start_client() as client,
        ):
            self.assertTrue(
                client.expect_message_in_output("creating xds client")
            )
            time.sleep(5)
            # Secondary xDS config start, send traffic to server2
            stats = client.get_stats(5)
            self.assertGreater(stats.rpcs_by_peer["server1"], 0)
            primary.stop_on_resource_request(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "test_cluster_2",
            )
            primary.update_resources(
                framework.helpers.xds_resources.build_listener_and_cluster(
                    cluster_name="test_cluster_2",
                    listener_name=_LISTENER,
                    upstream_port=server3.port,
                    upstream_host=_HOST_NAME.value,
                )
            )
            stats = client.get_stats(10)
            self.assertEqual(stats.num_failures, 0)
            self.assertIn("server2", stats.rpcs_by_peer)
            # Check that post-recovery uses a new config
            with self.start_control_plane(
                name="primary_xds_config_run_2",
                port=self.bootstrap.primary_port,
                upstream_port=server3.port,
            ):
                time.sleep(5)
                stats = client.get_stats(20)
                self.assertEqual(stats.num_failures, 0)
                self.assertIn("server3", stats.rpcs_by_peer)


if __name__ == "__main__":
    absl.testing.absltest.main()
