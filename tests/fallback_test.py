import logging
import socket
import unittest

import absl
from absl import flags
from absl.testing import absltest

import framework
import framework.helpers.docker
import framework.helpers.logs
import framework.xds_flags
import framework.xds_k8s_testcase

logger = logging.getLogger(__name__)

_CONTROL_PLANE_IMAGE = flags.DEFINE_string(
    "control_plane_image",
    "us-docker.pkg.dev/eostroukhov-xds-interop/docker/control-plane",
    "Control plane (xDS config) server image",
)
_HOST_NAME = flags.DEFINE_string(
    "host_name",
    "host.docker.internal",
    "Host name all the services are bound on",
)
_NODE_ID = flags.DEFINE_string("node", "test-id", "Node ID")

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
            ports=[get_free_port() for _ in range(2)],
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
            url="xds:///listener_0",
            image=framework.xds_k8s_flags.CLIENT_IMAGE.value,
        )

    def start_control_plane(self, name: str, index: int, upstream_port: int):
        logger.debug('Starting control plane "%s"', name)
        return framework.helpers.docker.ControlPlane(
            self.process_manager,
            name=name,
            port=self.bootstrap.xds_config_server_port(index),
            upstream=f"{_HOST_NAME.value}:{upstream_port}",
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

    def test_fallback_on_startup(self):
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
                index=1,
                upstream_port=server2.port,
            ):
                stats = client.get_stats(5)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Primary control plane start. Will use it
                with self.start_control_plane(
                    name="primary_xds_config",
                    index=0,
                    upstream_port=server1.port,
                ):
                    self.assertTrue(
                        client.expect_message_in_output(
                            "parsed Cluster example_proxy_cluster", timeout_s=30
                        )
                    )
                    stats = client.get_stats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)
                # Primary control plane down, cached value is used
                stats = client.get_stats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertEqual(stats.rpcs_by_peer["server1"], 5)
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
                "primary_xds_config_run_1", 0, server1.port
            ) as primary,
            self.start_control_plane("fallback_xds_config", 1, server2.port),
        ):
            # Wait for control plane to start up, stop when the client asks for
            # a cluster from the primary server
            self.assertTrue(primary.expect_running())
            primary.stop_on_resource_request(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "example_proxy_cluster",
            )
            # Run client
            with (self.start_client() as client,):
                self.assertTrue(
                    client.expect_message_in_output("creating xds client")
                )
                # Secondary xDS config start, send traffic to server2
                stats = client.get_stats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Rerun primary control plane
                with self.start_control_plane(
                    "primary_xds_config_run_2", 0, server1.port
                ):
                    self.assertTrue(primary.expect_running())
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
                "primary_xds_config_run_1", 0, server1.port
            ) as primary,
            self.start_control_plane("fallback_xds_config", 1, server2.port),
            self.start_client() as client,
        ):
            self.assertTrue(
                client.expect_message_in_output("creating xds client")
            )
            # Secondary xDS config start, send traffic to server2
            stats = client.get_stats(5)
            self.assertGreater(stats.rpcs_by_peer["server1"], 0)
            primary.stop_on_resource_request(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "test_cluster_2",
            )
            primary.update_resources(
                cluster="test_cluster_2",
                upstream_port=server3.port,
                upstream_host=_HOST_NAME.value,
            )
            stats = client.get_stats(10)
            self.assertEqual(stats.num_failures, 0)
            self.assertIn("server2", stats.rpcs_by_peer)
            # Check that post-recovery uses a new config
            with self.start_control_plane(
                name="primary_xds_config_run_2",
                index=0,
                upstream_port=server3.port,
            ) as primary2:
                self.assertTrue(primary2.expect_running())
                stats = client.get_stats(20)
                self.assertEqual(stats.num_failures, 0)
                self.assertIn("server3", stats.rpcs_by_peer)


if __name__ == "__main__":
    absl.testing.absltest.main()
