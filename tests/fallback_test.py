import socket

import absl
import absl.testing

import framework
import framework.helpers.docker
import framework.helpers.logs
import framework.xds_flags
import framework.xds_k8s_testcase

FLAGS = absl.flags.FLAGS

absl.flags.DEFINE_string(
    "control_plane_image",
    "us-docker.pkg.dev/eostroukhov-xds-interop/docker/control-plane",
    "Control plane (xDS config) server image",
)
absl.flags.DEFINE_string(
    "host_name",
    "host.docker.internal",
    "Host name all the services are bound on",
)
absl.flags.DEFINE_string("node", "test-id", "Node ID")

absl.flags.adopt_module_key_flags(framework.xds_k8s_testcase)


def GetFreePort() -> int:
    with (sock := socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


class FallbackTest(absl.testing.absltest.TestCase):
    bootstrap: framework.helpers.docker.Bootstrap = None

    @staticmethod
    def setUpClass():
        FallbackTest.bootstrap = framework.helpers.docker.Bootstrap(
            framework.helpers.logs.log_dir_mkdir("bootstrap"),
            ports=[GetFreePort() for _ in range(2)],
            host_name=FLAGS.host_name,
        )

    def setUp(self):
        absl.logging.info(f"Starting %s", self.id())
        self.process_manager = framework.helpers.docker.ProcessManager(
            bootstrap=FallbackTest.bootstrap,
            nodeId=FLAGS.node,
        )

    def StartClient(self, port: int = None, name: str = None):
        absl.logging.debug("Starting client process")
        if name is None:
            name = framework.xds_flags.CLIENT_NAME.value
        return framework.helpers.docker.Client(
            manager=self.process_manager,
            name=name,
            port=GetFreePort() if port is None else port,
            url="xds:///listener_0",
            image=framework.xds_k8s_flags.CLIENT_IMAGE.value,
        )

    def StartControlPlane(self, name: str, index: int, upstream_port: int):
        absl.logging.debug(f'Starting control plane "%s"', name)
        port = self.bootstrap.xds_config_server_port(index)
        return framework.helpers.docker.ControlPlane(
            self.process_manager,
            name=name,
            port=port,
            upstream=f"{FLAGS.host_name}:{upstream_port}",
            image=FLAGS.control_plane_image,
        )

    def StartServer(self, name: str, port: int = None):
        absl.logging.debug(f'Starting server "%s"', name)
        port = GetFreePort() if port is None else port
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
                "fallback_xds_config", 1, server2.get_port()
            ):
                stats = client.GetStats(5)
                self.assertGreater(stats.rpcs_by_peer["server2"], 0)
                self.assertNotIn("server1", stats.rpcs_by_peer)
                # Primary control plane start. Will use it
                with self.StartControlPlane(
                    name="primary_xds_config",
                    index=0,
                    upstream_port=server1.get_port(),
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
                server1.get_port(),
            ) as primary,
            self.StartControlPlane(
                "fallback_xds_config",
                1,
                server2.get_port(),
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
                    server1.get_port(),
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
                "primary_xds_config_run_1", 0, server1.get_port()
            ) as primary,
            self.StartControlPlane(
                "fallback_xds_config", 1, server2.get_port()
            ),
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
                upstream_port=server3.get_port(),
                upstream_host=FLAGS.host_name,
            )
            stats = client.GetStats(10)
            self.assertEqual(stats.num_failures, 0)
            self.assertIn("server2", stats.rpcs_by_peer)
            # Check that post-recovery uses a new config
            with self.StartControlPlane(
                name="primary_xds_config_run_2",
                index=0,
                upstream_port=server3.get_port(),
            ) as primary2:
                self.assertTrue(
                    primary2.ExpectOutput("management server listening on")
                )
                stats = client.GetStats(20)
                self.assertEqual(stats.num_failures, 0)
                self.assertIn("server3", stats.rpcs_by_peer)


if __name__ == "__main__":
    absl.testing.absltest.main()
