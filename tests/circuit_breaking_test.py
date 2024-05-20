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

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_flags
from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.infrastructure import k8s
from framework.rpc import grpc_testing
from framework.test_app.runners.k8s import k8s_xds_server_runner

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_KubernetesServerRunner = k8s_xds_server_runner.KubernetesServerRunner
_Lang = skips.Lang

_QPS = 100
_INITIAL_UNARY_MAX_REQUESTS = 500
_INITIAL_EMPTY_MAX_REQUESTS = 1000
_UPDATED_UNARY_MAX_REQUESTS = 800


class CircuitBreakingTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):
    @classmethod
    def setUpClass(cls):
        """Force the java test server for languages not yet supporting
        the `keep-open` option of the `rpc-behavior` feature.

        https://github.com/grpc/grpc/blob/master/doc/xds-test-descriptions.md#server
        """
        super().setUpClass()
        if cls.lang_spec.client_lang is not _Lang.JAVA:
            # gRPC C++, go, python and node fallback to the gRPC Java.
            # TODO(https://github.com/grpc/grpc-go/issues/6288): use go server.
            # TODO(https://github.com/grpc/grpc/issues/33134): use python server.
            cls.server_image = xds_k8s_flags.SERVER_IMAGE_CANONICAL.value

    def setUp(self):
        super().setUp()
        self.alternate_k8s_namespace = k8s.KubernetesNamespace(
            self.k8s_api_manager, self.server_namespace
        )
        self.alternate_server_runner = _KubernetesServerRunner(
            self.alternate_k8s_namespace,
            deployment_name=self.server_name + "-alt",
            image_name=self.server_image,
            gcp_service_account=self.gcp_service_account,
            td_bootstrap_image=self.td_bootstrap_image,
            gcp_project=self.project,
            gcp_api_manager=self.gcp_api_manager,
            xds_server_uri=self.xds_server_uri,
            network=self.network,
            debug_use_port_forwarding=self.debug_use_port_forwarding,
            reuse_namespace=True,
        )

    def cleanup(self):
        super().cleanup()
        if hasattr(self, "alternate_server_runner"):
            self.alternate_server_runner.cleanup(
                force=self.force_cleanup, force_namespace=self.force_cleanup
            )

    def test_circuit_breaking(self) -> None:
        with self.subTest("00_create_health_check"):
            self.td.create_health_check()

        with self.subTest("01_create_backend_services"):
            self.td.create_backend_service()
            self.td.create_alternative_backend_service()

        with self.subTest("02_create_url_map"):
            src_address = f"{self.server_xds_host}:{self.server_xds_port}"
            matcher_name = self.td.make_resource_name(
                self.td.URL_MAP_PATH_MATCHER_NAME
            )
            route_rules = [
                {
                    "priority": 0,
                    # UnaryCall -> backend_service
                    "matchRules": [
                        {"fullPathMatch": "/grpc.testing.TestService/UnaryCall"}
                    ],
                    "service": self.td.backend_service.url,
                },
                {
                    "priority": 1,
                    # EmptyCall -> alternative_backend_service
                    "matchRules": [
                        {"fullPathMatch": "/grpc.testing.TestService/EmptyCall"}
                    ],
                    "service": self.td.alternative_backend_service.url,
                },
            ]

            self.td.create_url_map_with_content(
                {
                    "name": self.td.make_resource_name(self.td.URL_MAP_NAME),
                    "defaultService": self.td.backend_service.url,
                    "hostRules": [
                        {"hosts": [src_address], "pathMatcher": matcher_name}
                    ],
                    "pathMatchers": [
                        {
                            "name": matcher_name,
                            "defaultService": self.td.backend_service.url,
                            "routeRules": route_rules,
                        }
                    ],
                }
            )

        with self.subTest("03_create_target_proxy"):
            self.td.create_target_proxy()

        with self.subTest("04_create_forwarding_rule"):
            self.td.create_forwarding_rule(self.server_xds_port)

        with self.subTest("05_start_test_servers"):
            default_test_server: _XdsTestServer = self.startTestServers()[0]
            alternate_test_server: _XdsTestServer = self.startTestServers(
                server_runner=self.alternate_server_runner
            )[0]

        with self.subTest("06_add_server_backends_to_backend_services"):
            self.setupServerBackends()
            # Add backend to alternative backend service
            (
                neg_name_alt,
                neg_zones_alt,
            ) = self.alternate_k8s_namespace.parse_service_neg_status(
                self.alternate_server_runner.service_name, self.server_port
            )
            self.td.alternative_backend_service_add_neg_backends(
                neg_name_alt, neg_zones_alt
            )

        with self.subTest("07_patch_backends_with_circuit_breakers"):
            self.td.backend_service_patch_backends(
                circuit_breakers={"maxRequests": _INITIAL_UNARY_MAX_REQUESTS}
            )
            self.td.alternative_backend_service_patch_backends(
                circuit_breakers={"maxRequests": _INITIAL_EMPTY_MAX_REQUESTS}
            )

        test_client: _XdsTestClient
        with self.subTest("08_start_test_client"):
            test_client = self.startTestClient(
                default_test_server, rpc="UnaryCall,EmptyCall", qps=_QPS
            )

        with self.subTest("09_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("10_test_server_received_rpcs_from_test_client"):
            self.assertRpcsEventuallyGoToGivenServers(
                test_client, (default_test_server, alternate_test_server)
            )

        with self.subTest("11_configure_client_with_keep_open"):
            test_client.update_config.configure(
                rpc_types=grpc_testing.RPC_TYPES_BOTH_CALLS,
                metadata={
                    (
                        grpc_testing.RPC_TYPE_UNARY_CALL,
                        "rpc-behavior",
                        "keep-open",
                    ),
                    (
                        grpc_testing.RPC_TYPE_EMPTY_CALL,
                        "rpc-behavior",
                        "keep-open",
                    ),
                },
                timeout_sec=20,
            )

        with self.subTest("12_client_reaches_target_steady_state"):
            self.assertClientEventuallyReachesSteadyState(
                test_client,
                rpc_type=grpc_testing.RPC_TYPE_UNARY_CALL,
                num_rpcs=_INITIAL_UNARY_MAX_REQUESTS,
            )
            self.assertClientEventuallyReachesSteadyState(
                test_client,
                rpc_type=grpc_testing.RPC_TYPE_EMPTY_CALL,
                num_rpcs=_INITIAL_EMPTY_MAX_REQUESTS,
            )

        with self.subTest("13_increase_backend_max_requests"):
            self.td.backend_service_patch_backends(
                circuit_breakers={"maxRequests": _UPDATED_UNARY_MAX_REQUESTS}
            )

        with self.subTest("14_client_reaches_increased_steady_state"):
            self.assertClientEventuallyReachesSteadyState(
                test_client,
                rpc_type=grpc_testing.RPC_TYPE_UNARY_CALL,
                num_rpcs=_UPDATED_UNARY_MAX_REQUESTS,
            )


if __name__ == "__main__":
    absltest.main()
