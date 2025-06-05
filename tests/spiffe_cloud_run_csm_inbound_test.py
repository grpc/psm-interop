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
from typing import Final, TypeAlias

from absl.testing import absltest
from typing_extensions import override

from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.test_app.runners.k8s import k8s_xds_server_runner
from framework.test_cases import spiffe_testcase

logger = logging.getLogger(__name__)

# Type aliases.
_Lang: TypeAlias = skips.Lang
_XdsTestServer: TypeAlias = xds_k8s_testcase.XdsTestServer
_XdsTestClient: TypeAlias = xds_k8s_testcase.XdsTestClient
_SecurityMode = xds_k8s_testcase.SecurityXdsKubernetesTestCase.SecurityMode
KubernetesServerRunner: TypeAlias = k8s_xds_server_runner.KubernetesServerRunner
ServerDeploymentArgs: TypeAlias = k8s_xds_server_runner.ServerDeploymentArgs

CR_WORKLOAD_IDENTITY_POOL: Final[str] = "psm-interop-cloudrun-wip-cr"
NAMESPACE_NAME: Final[str] = "psm-interop-cloudrun-wip-cr"
MANAGED_IDENTITY_ID: Final[str] = "psm-interop-cloudrun-wip-cr-mwid"


class SpiffeCloudRunCsmInboundTest(
    spiffe_testcase.SpiffeMtlsXdsKubernetesCloudRunTestCase
):
    @staticmethod
    @override
    def is_supported(config: skips.TestConfig) -> bool:
        # TODO(emchandwani): Add version checks for other languages(CPP,
        # Python).
        if config.client_lang is _Lang.JAVA:
            return config.version_gte("v1.71.x")
        elif config.client_lang is _Lang.GO:
            return config.version_gte("v1.73.x")
        return False

    @override
    def initKubernetesServerRunner(self, **kwargs) -> KubernetesServerRunner:
        return super().initKubernetesServerRunner(
            deployment_args=ServerDeploymentArgs(
                enable_spiffe=True,
            )
        )

    def authz_rules(self):
        return [
            {
                "sources": {
                    "principals": [
                        (
                            f"spiffe://{self.project}.svc.id.goog/ns/"
                            f"{self.client_namespace}/sa/{self.client_name}"
                        ),
                        (
                            f"spiffe://{CR_WORKLOAD_IDENTITY_POOL}.global."
                            f"{self.project_number}.workload.id.goog/ns/"
                            f"{NAMESPACE_NAME}/sa/{MANAGED_IDENTITY_ID}"
                        ),
                    ],
                },
                "destinations": {
                    "hosts": [f"*:{self.server_xds_port}"],
                    "ports": [self.server_port],
                },
            },
        ]

    def test_spiffe_mtls_gke_to_cloud_run(self):
        with self.subTest("1_start_secure_test_server"):
            test_server: _XdsTestServer = self.startSecureTestServer()

        with self.subTest("2_setup_backend_for_grpc"):
            self.td.setup_backend_for_grpc(
                health_check_port=self.server_maintenance_port
            )

        with self.subTest("3_add_server_backends_to_backend_services"):
            self.setupServerBackends()

        with self.subTest("4_create_mesh"):
            self.td.create_mesh()

        with self.subTest("5_create_grpc_route"):
            self.td.create_grpc_route(
                self.server_xds_host, self.server_xds_port
            )

        with self.subTest("6_create_authz_policy_and_setup_security_policies"):
            self.td.create_authz_policy(
                action="ALLOW", rules=self.authz_rules()
            )
            self.setupSecurityPolicies(
                server_tls=True,
                server_mtls=True,
                client_tls=True,
                client_mtls=True,
            )
        with self.subTest("8_start_secure_test_client"):
            test_client: _XdsTestClient = self.startCloudRunTestClient(
                test_server, enable_spiffe=True
            )

        with self.subTest("9_assert_successful_rpcs"):
            self.assertTestAppSecurityWithRetry(
                _SecurityMode.MTLS,
                test_client,
                test_server,
                secure_channel=True,
                match_only_port=True,
            )
            logger.info("[SUCCESS] mTLS security mode confirmed.")
            self.assertSuccessfulRpcs(test_client, secure_channel=True)


if __name__ == "__main__":
    absltest.main()
