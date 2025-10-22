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
from typing import TypeAlias

from absl.testing import absltest
from typing_extensions import override

from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.infrastructure import gcp
from framework.test_app.runners.k8s import k8s_xds_client_runner
from framework.test_cases import cloud_run_testcase

logger = logging.getLogger(__name__)

# Type aliases.
_Lang: TypeAlias = skips.Lang
_XdsTestServer: TypeAlias = xds_k8s_testcase.XdsTestServer
_XdsTestClient: TypeAlias = xds_k8s_testcase.XdsTestClient
KubernetesClientRunner: TypeAlias = k8s_xds_client_runner.KubernetesClientRunner
ClientDeploymentArgs: TypeAlias = k8s_xds_client_runner.ClientDeploymentArgs


class CloudRunCsmInboundTest(cloud_run_testcase.CloudRunXdsKubernetesTestCase):
    @staticmethod
    @override
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang is _Lang.CPP:
            return config.version_gte("v1.69.x")
        elif config.client_lang is _Lang.PYTHON:
            return config.version_gte("v1.69.x")
        elif config.client_lang is _Lang.JAVA:
            return config.version_gte("v1.77.x")               
        return False

    @override
    def initKubernetesClientRunner(self, **kwargs) -> KubernetesClientRunner:
        return super().initKubernetesClientRunner(
            deployment_args=ClientDeploymentArgs(
                is_trusted_xds_server_experimental=True,
            )
        )

    def test_gke_to_cloud_run(self):
        with self.subTest("0_create_mesh"):
            self.td.create_mesh()

        with self.subTest("1_start_cloud_run_test_server"):
            test_server: _XdsTestServer = self.startTestServers()[0]

        with self.subTest("2_create_serverless_neg"):
            self.td.create_neg_serverless(self.server_namespace)

        with self.subTest("3_create_backend_service"):
            self.td.create_backend_service(
                protocol=gcp.compute.ComputeV1.BackendServiceProtocol.HTTP2,
            )

        with self.subTest("4_add_server_backends_to_backend_service"):
            self.td.backend_service_add_cloudrun_backends()

        with self.subTest("5_create_grpc_route"):
            self.td.create_grpc_route(
                self.server_xds_host, self.server_xds_port
            )

        with self.subTest("6_start_test_client"):
            test_client: _XdsTestClient = self.startSecureTestClient(
                test_server,
                config_mesh=self.td.mesh.name,
            )

        with self.subTest("7_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("8_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)


if __name__ == "__main__":
    absltest.main()
