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

import absl
from absl import flags
from absl.testing import absltest
from grpc_channelz.v1 import channelz_pb2

import framework
from framework.helpers import retryers
import framework.helpers.docker
import framework.helpers.logs
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


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


class FallbackTest(absltest.TestCase):
    bootstrap: framework.helpers.docker.Bootstrap = None
    dockerInternalIp: str

    @staticmethod
    def setUpClass():
        # Use the host IP for when we need to use IP address and not the host
        # name, such as EDS resources
        FallbackTest.dockerInternalIp = socket.gethostbyname(
            socket.gethostname()
        )
        FallbackTest.bootstrap = framework.helpers.docker.Bootstrap(
            framework.helpers.logs.log_dir_mkdir("bootstrap"),
            primary_port=get_free_port(),
            fallback_port=get_free_port(),
            host_name=_HOST_NAME.value,
        )

    def setUp(self):
        self.process_manager = framework.helpers.docker.ProcessManager(
            bootstrap=FallbackTest.bootstrap,
            node_id=_NODE_ID.value,
        )

    def start_client(self, port: int = None, name: str = None):
        return framework.helpers.docker.Client(
            manager=self.process_manager,
            name=name or framework.xds_flags.CLIENT_NAME.value,
            port=port or get_free_port(),
            url=f"xds:///{_LISTENER}",
            image=framework.xds_k8s_flags.CLIENT_IMAGE.value,
            stats_request_timeout_s=_STATS_REQUEST_TIMEOUT_S.value,
        )

    def start_control_plane(
        self, name: str, port: int, upstream_port: int, cluster_name=None
    ):
        return framework.helpers.docker.ControlPlane(
            self.process_manager,
            name=name,
            port=port,
            initial_resources=framework.helpers.xds_resources.build_listener_and_cluster(
                listener_name=_LISTENER,
                cluster_name=cluster_name or f"initial_cluster_for_{name}",
                upstream_host=FallbackTest.dockerInternalIp,
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
        primary_status: channelz_pb2.ChannelConnectivityState.State,
        fallback_status: channelz_pb2.ChannelConnectivityState.State,
    ):
        self.assertTrue(
            self.ads_connections_status_check_result(
                client, primary_status, fallback_status
            )
        )

    def ads_connections_status_check_result(
        self,
        client: framework.helpers.docker.Client,
        expected_primary_status: channelz_pb2.ChannelConnectivityState.State,
        expected_fallback_status: channelz_pb2.ChannelConnectivityState.State,
    ) -> bool:
        primary_status = client.expect_channel_status(
                self.bootstrap.primary_port,
                expected_primary_status,
                timeout=datetime.timedelta(
                    milliseconds=_STATUS_TIMEOUT_MS.value
                ),
                poll_interval=datetime.timedelta(
                    milliseconds=_STATUS_POLL_INTERVAL_MS.value
                ),
            )
        fallback_status = client.expect_channel_status(
                self.bootstrap.fallback_port,
                expected_fallback_status,
                timeout=datetime.timedelta(
                    milliseconds=_STATUS_TIMEOUT_MS.value
                ),
                poll_interval=datetime.timedelta(
                    milliseconds=_STATUS_POLL_INTERVAL_MS.value
                ),
            )
        return (
            primary_status == expected_primary_status
            and fallback_status == expected_fallback_status
        )

    def test_fallback_on_startup(self):
        with (
            self.start_server(name="server1") as server1,
            self.start_server(name="server2") as server2,
            self.start_client() as client,
        ):
            self.assert_ads_connections(
                client=client,
                primary_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                fallback_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
            )
            self.assertEqual(client.get_stats(5).num_failures, 5)
            # Fallback control plane start, send traffic to server2
            with self.start_control_plane(
                name="fallback_xds_config",
                port=self.bootstrap.fallback_port,
                upstream_port=server2.port,
            ):
                self.assert_ads_connections(
                    client=client,
                    primary_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                    fallback_status=channelz_pb2.ChannelConnectivityState.READY,
                )
                stats = client.get_stats(5)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Primary control plane start. Will use it
                with self.start_control_plane(
                    name="primary_xds_config",
                    port=self.bootstrap.primary_port,
                    upstream_port=server1.port,
                ):
                    self.assert_ads_connections(
                        client=client,
                        primary_status=channelz_pb2.ChannelConnectivityState.READY,
                        fallback_status=None,
                    )
                    stats = client.get_stats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)
                self.assert_ads_connections(
                    client=client,
                    primary_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                    fallback_status=None,
                )
                # Primary control plane down, cached value is used
                stats = client.get_stats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertEqual(stats.rpcs_by_peer["server1"], 5)
            self.assert_ads_connections(
                client=client,
                primary_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                fallback_status=None,
            )
            # Fallback control plane down, cached value is used
            stats = client.get_stats(5)
            self.assertEqual(stats.num_failures, 0)
            self.assertEqual(stats.rpcs_by_peer["server1"], 5)

    def test_fallback_mid_startup(self):
        # Run the mesh, excluding the client
        with (
            self.start_server(name="server1") as server1,
            self.start_server(name="server2") as server2,
            self.start_control_plane(
                "primary_xds_config_run_1",
                port=self.bootstrap.primary_port,
                upstream_port=server1.port,
                cluster_name="cluster_name",
            ) as primary,
            self.start_control_plane(
                "fallback_xds_config",
                port=self.bootstrap.fallback_port,
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
                    port=self.bootstrap.primary_port,
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

    def test_fallback_mid_update(self):
        with (
            self.start_server(name="server1") as server1,
            self.start_server(name="server2") as server2,
            self.start_server(name="server3") as server3,
            self.start_control_plane(
                "primary_xds_config_run_1",
                port=self.bootstrap.primary_port,
                upstream_port=server1.port,
            ) as primary,
            self.start_control_plane(
                "fallback_xds_config",
                port=self.bootstrap.fallback_port,
                upstream_port=server2.port,
            ),
            self.start_client() as client,
        ):
            self.check_ads_connections_statuses(
                client,
                primary_status=channelz_pb2.ChannelConnectivityState.READY,
                fallback_status=None,
            )
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
                    upstream_host=FallbackTest.dockerInternalIp,
                )
            )
            self.check_ads_connections_statuses(
                client,
                primary_status=channelz_pb2.ChannelConnectivityState.TRANSIENT_FAILURE,
                fallback_status=channelz_pb2.ChannelConnectivityState.READY,
            )
            retryer = retryers.constant_retryer(
                wait_fixed=datetime.timedelta(seconds=1),
                timeout=datetime.timedelta(seconds=20),
                check_result=lambda stats: stats.num_failures == 0
                and "server2" in stats.rpcs_by_peer,
            )
            retryer(client.get_stats, 10)
            # Check that post-recovery uses a new config
            with self.start_control_plane(
                name="primary_xds_config_run_2",
                port=self.bootstrap.primary_port,
                upstream_port=server3.port,
            ):
                self.check_ads_connections_statuses(
                    client,
                    primary_status=channelz_pb2.ChannelConnectivityState.READY,
                    fallback_status=None,
                )
                retryer = retryers.constant_retryer(
                    wait_fixed=datetime.timedelta(seconds=1),
                    timeout=datetime.timedelta(seconds=20),
                    check_result=lambda stats: stats.num_failures == 0
                    and "server3" in stats.rpcs_by_peer,
                )
                retryer(client.get_stats, 10)

    def check_ads_connections_statuses(
        self, client, primary_status, fallback_status
    ):
        retryer = retryers.constant_retryer(
            wait_fixed=datetime.timedelta(seconds=1),
            timeout=datetime.timedelta(minutes=3),
            check_result=lambda result: result is True,
        )
        retryer(
            self.ads_connections_status_check_result,
            client,
            expected_primary_status=primary_status,
            expected_fallback_status=fallback_status,
        )


if __name__ == "__main__":
    absl.testing.absltest.main()
