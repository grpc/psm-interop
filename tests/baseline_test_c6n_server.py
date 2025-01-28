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
import logging

from absl.testing import absltest

from framework import xds_k8s_testcase

logger = logging.getLogger(__name__)

_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient


class BaselineTest(xds_k8s_testcase.CloudRunXdsKubernetesTestCase):
    def test_traffic_director_grpc_setup(self):
        with self.subTest("0_create_mesh"):
            self.td.create_mesh()

        with self.subTest("1_start_cloudrun_test_server"):
            test_server: _XdsTestServer = self.startTestServers()[0]

        with self.subTest("2_create_serverless_neg"):
            neg = self.backend_service_add_serverless_neg_backends()

        with self.subTest("3_create_backend_service"):
            self.td.create_backend_service(
                protocol=self.compute_v1.BackendServiceProtocol.HTTP2
            )

        with self.subTest("4_add_server_backends_to_backend_service"):
            neg_resource = self.compute_v1.GcpResource(
                name=neg["name"],
                url=neg["selfLink"],
            )
            self.td.backend_service_add_backends([neg_resource], self.region)

        with self.subTest("5_create_grpc_route"):
            self.td.create_grpc_route(self.server_xds_host, self.server_port)

        with self.subTest("7_start_test_client"):
            test_client: _XdsTestClient = self.startTestClient(
                test_server, config_mesh=self.td.mesh.name
            )

        with self.subTest("8_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("9_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)


if __name__ == "__main__":
    absltest.main(failfast=True)
