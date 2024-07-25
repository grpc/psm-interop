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
import logging
from typing import List

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_flags
from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.test_app.runners.k8s import k8s_xds_server_runner

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_KubernetesServerRunner = k8s_xds_server_runner.KubernetesServerRunner
_Lang = skips.Lang

_QPS = 100


class DualStackTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):
    @classmethod
    def setUpClass(cls):
        """Force the canonical test server for all languages.
        """
        super().setUpClass()
        if cls.lang_spec.client_lang is not _Lang.JAVA:
            cls.server_image = xds_k8s_flags.SERVER_IMAGE_CANONICAL.value

    def setUp(self):
        self.enable_dualstack = True
        super().setUp()
        runner_args = dict(
            image_name=self.server_image,
            gcp_service_account=self.gcp_service_account,
            td_bootstrap_image=self.td_bootstrap_image,
            gcp_project=self.project,
            gcp_api_manager=self.gcp_api_manager,
            xds_server_uri=self.xds_server_uri,
            network=self.network,
            debug_use_port_forwarding=self.debug_use_port_forwarding,
            reuse_namespace=True,
            reuse_service=True,
            enable_dualstack=True,
            enable_workload_identity=self.enable_workload_identity,
        )

        self.v4_server_runner = _KubernetesServerRunner(
            self.server_runner.k8s_namespace,
            deployment_name=self.server_name + "-v4",
            **runner_args,
        )
        self.v6_server_runner = _KubernetesServerRunner(
            self.server_runner.k8s_namespace,
            deployment_name=self.server_name + "-v6",
            **runner_args,
        )

    def cleanup(self):
        super().cleanup()
        if hasattr(self, "v4_server_runner"):
            self.v4_server_runner.cleanup(
                force=self.force_cleanup, force_namespace=self.force_cleanup
            )
        if hasattr(self, "v6_server_runner"):
            self.v6_server_runner.cleanup(
                force=self.force_cleanup, force_namespace=self.force_cleanup
            )

    def test_dual_stack(self) -> None:
        with self.subTest("00_create_health_check"):
            self.td.create_health_check()

        with self.subTest("01_create_backend_services"):
            self.td.create_backend_service()

        with self.subTest("02_setup_routing_rule_map_for_grpc"):
            self.td.setup_routing_rule_map_for_grpc(self.server_xds_host, self.server_xds_port)

        test_servers: List[_XdsTestServer] = []
        with self.subTest("03_start_test_server-default"):
            test_servers.append(self.startTestServers()[0])

        with self.subTest("03_start_test_server-v4"):
            test_servers.append(self.startTestServers(
                server_runner=self.v4_server_runner,
                address_type="ipv4",
            )[0])

        with self.subTest("03_start_test_server-v6"):
            test_servers.append(self.startTestServers(
                server_runner=self.v6_server_runner,
                address_type="ipv6",
            )[0])

        logger.info(f"Test servers: {test_servers}")  # TODO: change to debug

        with self.subTest("04_add_server_backends_to_backend_services"):
            self.setupServerBackends()
            self.setupServerBackends(server_runner=self.v4_server_runner)
            self.setupServerBackends(server_runner=self.v6_server_runner)

        test_client: _XdsTestClient
        with self.subTest("05_start_test_client"):
            test_client = self.startTestClient(test_servers[0])

        with self.subTest("06_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("07_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

        with self.subTest("08_round_robin"):
            num_rpcs = 100
            expected_rpcs_per_replica = num_rpcs / len(test_servers)

            rpcs_by_peer = self.getClientRpcStats(
                test_client, num_rpcs
            ).rpcs_by_peer
            total_requests_received = sum(rpcs_by_peer[x] for x in rpcs_by_peer)
            self.assertEqual(
                total_requests_received, num_rpcs, "Wrong number of RPCS"
            )
            for server in test_servers:
                hostname = server.hostname
                self.assertIn(
                    hostname,
                    rpcs_by_peer,
                    f"Server {hostname} did not receive RPCs",
                )
                self.assertGreater(
                    rpcs_by_peer[hostname],
                    3,
                    f"Insufficient RPCs for server {hostname}",
                )


if __name__ == "__main__":
    absltest.main()
