import socket
import unittest

from absl import flags
from absl import logging
from absl.testing import absltest

from framework import xds_k8s_testcase
from framework.helpers.docker import Bootstrap
from framework.helpers.docker import Client
from framework.helpers.docker import ControlPlane
from framework.helpers.docker import GrpcProcess
from framework.helpers.docker import ProcessManager
from framework.helpers.logs import log_dir_mkdir
from framework.xds_flags import CLIENT_NAME
from framework.xds_k8s_flags import CLIENT_IMAGE
from framework.xds_k8s_flags import SERVER_IMAGE_CANONICAL

FLAGS = flags.FLAGS

flags.DEFINE_string(
    "control_plane_image",
    "us-docker.pkg.dev/eostroukhov-xds-interop/docker/control-plane",
    "Control plane (xDS config) server image",
)
flags.DEFINE_string(
    "host_name",
    "host.docker.internal",
    "Host name all the services are bound on",
)
flags.DEFINE_string("node", "test-id", "Node ID")

flags.adopt_module_key_flags(xds_k8s_testcase)


def GetFreePort() -> int:
    with (sock := socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


class FallbackTest(unittest.TestCase):
    __bootstrap: Bootstrap = None

    @staticmethod
    def setUpClass():
        FallbackTest.__bootstrap = Bootstrap(
            log_dir_mkdir("bootstrap"),
            ports=[GetFreePort() for _ in range(2)],
            host_name=FLAGS.host_name,
        )

    def setUp(self):
        logging.info(f"Starting %s", self.id())
        self.__process_manager = ProcessManager(
            bootstrap=FallbackTest.__bootstrap,
            nodeId=FLAGS.node,
        )

    def StartClient(self, port: int = None, name: str = None):
        logging.debug("Starting client process")
        if name is None:
            name = CLIENT_NAME.value
        return Client(
            manager=self.__process_manager,
            name=name,
            port=GetFreePort() if port is None else port,
            url="xds:///listener_0",
            image=CLIENT_IMAGE.value,
        )

    def StartControlPlane(self, name: str, index: int, upstream_port: int):
        logging.debug(f'Starting control plane "%s"', name)
        port = self.__bootstrap.xds_config_server_port(index)
        return ControlPlane(
            self.__process_manager,
            name=name,
            port=port,
            upstream=f"{FLAGS.host_name}:{upstream_port}",
            image=FLAGS.control_plane_image,
        )

    def StartServer(self, name: str, port: int = None):
        logging.debug(f'Starting server "%s"', name)
        port = GetFreePort() if port is None else port
        return GrpcProcess(
            name=name,
            port=port,
            image=SERVER_IMAGE_CANONICAL.value,
            manager=self.__process_manager,
            ports={8080: port},
            command=[],
        )

    def test_fallback_on_startup(self):
        with (
            self.StartServer(name="server1") as server1,
            self.StartServer(name="server2") as server2,
            self.StartClient() as client,
        ):
            self.assertTrue(
                client.ExpectOutput("UNAVAILABLE: xDS channel for server")
            )
            self.assertEqual(client.GetStats(5).num_failures, 5)
            # Fallback control plane start, send traffic to server2
            with self.StartControlPlane(
                "fallback_xds_config", 1, server2.port()
            ):
                stats = client.GetStats(5)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Primary control plane start. Will use it
                with self.StartControlPlane(
                    name="primary_xds_config",
                    index=0,
                    upstream_port=server1.port(),
                ):
                    self.assertTrue(
                        client.ExpectOutput(
                            "parsed Cluster example_proxy_cluster"
                        )
                    )
                    stats = client.GetStats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)
                # Primary control plane down
                stats = client.GetStats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertEqual(stats.rpcs_by_peer["server1"], 5)
            # Fallback control plane down
            stats = client.GetStats(5)
            self.assertEqual(stats.num_failures, 0)
            self.assertEqual(stats.rpcs_by_peer["server1"], 5)

    def test_fallback_mid_startup(self):
        # Run the mesh, excluding the client
        with (
            self.StartServer(name="server1") as server1,
            self.StartServer(name="server2") as server2,
            self.StartControlPlane(
                "primary_xds_config_run_1",
                0,
                server1.port(),
            ) as primary,
            self.StartControlPlane(
                "fallback_xds_config",
                1,
                server2.port(),
            ),
        ):
            # Wait for control plane to start up, stop when the client asks for
            # a cluster from the primary server
            self.assertTrue(
                primary.ExpectOutput("management server listening on")
            )
            primary.StopOnResourceRequest(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "example_proxy_cluster",
            )
            # Run client
            with (self.StartClient() as client,):
                self.assertTrue(client.ExpectOutput("creating xds client"))
                # Secondary xDS config start, send traffic to server2
                stats = client.GetStats(5)
                self.assertEqual(stats.num_failures, 0)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Rerun primary control plane
                with self.StartControlPlane(
                    "primary_xds_config_run_2",
                    0,
                    server1.port(),
                ):
                    self.assertTrue(
                        primary.ExpectOutput("management server listening on")
                    )
                    stats = client.GetStats(10)
                    self.assertEqual(stats.num_failures, 0)
                    self.assertIn("server1", stats.rpcs_by_peer)
                    self.assertGreater(stats.rpcs_by_peer["server1"], 0)

    def test_fallback_mid_update(self):
        with (
            self.StartServer(name="server1") as server1,
            self.StartServer(name="server2") as server2,
            self.StartServer(name="server3") as server3,
            self.StartControlPlane(
                "primary_xds_config_run_1", 0, server1.port()
            ) as primary,
            self.StartControlPlane("fallback_xds_config", 1, server2.port()),
            self.StartClient() as client,
        ):
            self.assertTrue(client.ExpectOutput("creating xds client"))
            # Secondary xDS config start, send traffic to server2
            stats = client.GetStats(5)
            self.assertGreater(stats.rpcs_by_peer["server1"], 0)
            primary.StopOnResourceRequest(
                "type.googleapis.com/envoy.config.cluster.v3.Cluster",
                "test_cluster_2",
            )
            primary.UpdateResources(
                cluster="test_cluster_2",
                upstream_port=server3.port(),
                upstream_host=FLAGS.host_name,
            )
            stats = client.GetStats(10)
            self.assertEqual(stats.num_failures, 0)
            self.assertIn("server2", stats.rpcs_by_peer)
            # Check that post-recovery uses a new config
            with self.StartControlPlane(
                name="primary_xds_config_run_2",
                index=0,
                upstream_port=server3.port(),
            ) as primary2:
                self.assertTrue(
                    primary2.ExpectOutput("management server listening on")
                )
                stats = client.GetStats(20)
                self.assertEqual(stats.num_failures, 0)
                self.assertIn("server3", stats.rpcs_by_peer)


if __name__ == "__main__":
    absltest.main()
