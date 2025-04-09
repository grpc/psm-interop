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
from typing import TypeAlias

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_testcase
from framework.test_app.runners.k8s import k8s_xds_server_runner

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

XdsTestServer: TypeAlias = xds_k8s_testcase.XdsTestServer
XdsTestClient: TypeAlias = xds_k8s_testcase.XdsTestClient
KubernetesServerRunner: TypeAlias = k8s_xds_server_runner.KubernetesServerRunner
ServerDeploymentArgs: TypeAlias = k8s_xds_server_runner.ServerDeploymentArgs
SecurityMode = xds_k8s_testcase.SecurityXdsKubernetesTestCase.SecurityMode


class SecurityAppNetTest(xds_k8s_testcase.SecurityXdsKubernetesTestCase):
    def test_mtls(self):
        """mTLS test.

        Both client and server configured to use TLS and mTLS.
        """
        with self.subTest("0_create_health_check"):
            self.td.create_health_check(port=self.server_maintenance_port)

        with self.subTest("1_create_backend_service"):
            self.td.create_backend_service()

        with self.subTest("2_create_mesh"):
            self.td.create_mesh()

        with self.subTest("3_create_grpc_route"):
            self.td.create_grpc_route(
                self.server_xds_host,
                self.server_xds_port,
            )

        self.setupSecurityPolicies(
            server_tls=True, server_mtls=True, client_tls=True, client_mtls=True
        )

        test_server: XdsTestServer = self.startSecureTestServer(
            config_mesh=self.td.mesh.name
        )
        self.setupServerBackends()
        test_client: XdsTestClient = self.startSecureTestClient(
            test_server, config_mesh=self.td.mesh.name
        )

        self.assertTestAppSecurity(SecurityMode.MTLS, test_client, test_server)
        self.assertSuccessfulRpcs(test_client)
        logger.info("[SUCCESS] mTLS security mode confirmed.")


if __name__ == "__main__":
    absltest.main()
