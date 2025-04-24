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
from framework.test_cases import cloud_run_testcase

logger = logging.getLogger(__name__)

# Type aliases.
_Lang: TypeAlias = skips.Lang
_XdsTestServer: TypeAlias = xds_k8s_testcase.XdsTestServer
_XdsTestClient: TypeAlias = xds_k8s_testcase.XdsTestClient


class CloudRunCsmOutboundTest(cloud_run_testcase.CloudRunXdsTestCase):
    @staticmethod
    @override
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang is _Lang.CPP:
            return config.version_gte("v1.69.x")
        elif config.client_lang is _Lang.PYTHON:
            return config.version_gte("v1.69.x")
        elif config.client_lang is _Lang.JAVA:
            return config.version_gte("v1.71.x")
        return False

    def test_cloudrun_to_cloudrun(self):
        with self.subTest("0_create_mesh"):
            self.td.create_mesh()

        with self.subTest("1_start_cloudrun_test_server"):
            test_server: _XdsTestServer = self.startTestServers()[0]

        with self.subTest("2_create_serverless_neg"):
            self.td.create_neg_serverless(self.server_namespace)

        with self.subTest("3_create_backend_service"):
            self.td.create_backend_service(
                protocol=gcp.compute.ComputeV1.BackendServiceProtocol.HTTP2
            )

        with self.subTest("4_add_server_backends_to_backend_service"):
            self.td.backend_service_add_cloudrun_backends()

        with self.subTest("5_create_grpc_route"):
            self.td.create_grpc_route(
                self.server_xds_host, self.server_xds_port
            )

        with self.subTest("6_start_test_client"):
            test_client: _XdsTestClient = self.startCloudRunTestClient(
                test_server
            )
        # When getting stats from the Cloud Run client, there is a proxy
        # involved.The proxy uses HTTP/1.1 for plaintext connections, but since
        # gRPC requires HTTP/2.0,we must use encrypted communication (HTTPS) to
        # ensure compatibility with gRPC.
        with self.subTest("7_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client, secure_mode=True)

        with self.subTest("8_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client, secure_mode=True)


if __name__ == "__main__":
    absltest.main(failfast=True)
