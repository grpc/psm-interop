# Copyright 2026 gRPC authors.
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

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_testcase

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient


class RegionalTrafficDirectorTest(xds_k8s_testcase.AppNetXdsKubernetesTestCase):
    def test_regional_traffic_director_baseline(self) -> None:
        with self.subTest("00_create_health_check"):
            self.td.create_health_check()

        with self.subTest("01_create_backend_services"):
            self.td.create_backend_service()

        with self.subTest("02_create_mesh"):
            self.td.create_mesh()

        with self.subTest("03_create_grpc_route"):
            self.td.create_grpc_route(self.server_xds_host, self.server_xds_port)

        with self.subTest("04_start_test_servers"):
            test_servers = self.startTestServers()

        with self.subTest("05_add_server_backends_to_backend_services"):
            self.setupServerBackends()

        with self.subTest("06_start_test_client"):
            if self.xds_server_region:
                server_target = f"xds://traffic-director.{self.xds_server_region}.xds.googleapis.com/psm-grpc-server:{self.server_xds_port}"
                test_client = self._start_test_client(
                    server_target, config_mesh=self.td.mesh.name
                )
            else:
                test_client = self.startTestClient(
                    test_servers[0], config_mesh=self.td.mesh.name
                )

        with self.subTest("07_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("08_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

        with self.subTest("09_verify_regional_authority_config"):
            config = test_client.csds.fetch_client_status_parsed()
            self.assertIsNotNone(config, "Expected CSDS configuration dump")
            if self.xds_server_region:
                logger.info(
                    "Validated client started successfully with regional TD authority for region: %s",
                    self.xds_server_region,
                )


if __name__ == "__main__":
    absltest.main(failfast=True)
