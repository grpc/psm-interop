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
import datetime as dt
import logging
from typing import Final

from absl import flags
from absl.testing import absltest
from typing_extensions import TypeAlias, override

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

_SERVERS_APP_LABEL: Final[str] = "psm-interop-dualstack"


class DualStackTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):
    v4_server_runner: _KubernetesServerRunner = None
    v6_server_runner: _KubernetesServerRunner = None

    @staticmethod
    @override
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang in _Lang.CPP | _Lang.PYTHON | _Lang.JAVA:
            return config.version_gte("v1.66.x")
        if config.client_lang == _Lang.NODE:
            return config.version_gte("v1.12.x")
        return False

    @classmethod
    def setUpClass(cls):
        """Force the canonical test server for all languages."""
        # TODO: when Java 1.66 is available, make sure the canonical server is switched to it so
        #  that we have all features we need for the dualstack test
        super().setUpClass()
        if cls.lang_spec.client_lang is not _Lang.JAVA:
            cls.server_image = xds_k8s_flags.SERVER_IMAGE_CANONICAL.value

    def setUp(self):
        super().setUp()
        runner_args = dict(
            image_name=self.server_image,
            gcp_service_account=self.gcp_service_account,
            td_bootstrap_image=self.td_bootstrap_image,
            gcp_project=self.project,
            app_label=_SERVERS_APP_LABEL,
            gcp_api_manager=self.gcp_api_manager,
            xds_server_uri=self.xds_server_uri,
            network=self.network,
            debug_use_port_forwarding=self.debug_use_port_forwarding,
            reuse_namespace=True,
            reuse_service=True,
            enable_dualstack=True,
            enable_workload_identity=self.enable_workload_identity,
        )

        k8s_namespace = self.server_runner.k8s_namespace
        # Replaces self.server_runner initiated in self.initKubernetesServerRunner().
        self.server_runner = _KubernetesServerRunner(
            k8s_namespace,
            deployment_name=self.server_name,
            **runner_args,
        )
        self.v4_server_runner = _KubernetesServerRunner(
            k8s_namespace,
            deployment_name=self.server_name + "-v4",
            service_name=self.server_runner.service_name,
            **runner_args,
        )
        self.v6_server_runner = _KubernetesServerRunner(
            k8s_namespace,
            deployment_name=self.server_name + "-v6",
            service_name=self.server_runner.service_name,
            **runner_args,
        )

    def cleanup(self):
        self.td.cleanup(force=self.force_cleanup)
        self.client_runner.cleanup(
            force=self.force_cleanup, force_namespace=self.force_cleanup
        )
        self.server_runner.cleanup(
            force=self.force_cleanup, force_namespace=False
        )
        if self.v4_server_runner:
            self.v4_server_runner.cleanup(
                force=self.force_cleanup, force_namespace=False
            )
        if self.v6_server_runner:
            self.v6_server_runner.cleanup(
                force=self.force_cleanup, force_namespace=True
            )

    def test_dualstack(self) -> None:
        self.assertTrue(
            self.enable_dualstack,
            "Use common-dualstack.cfg to pickup all dualstack configurations",
        )

        with self.subTest("00_create_health_check"):
            self.td.create_health_check()

        with self.subTest("01_create_backend_service"):
            self.td.create_backend_service()

        with self.subTest("02_setup_routing_rule_map_for_grpc"):
            self.td.setup_routing_rule_map_for_grpc(
                self.server_xds_host, self.server_xds_port
            )

        test_servers: list[_XdsTestServer] = []
        with self.subTest("03_start_test_server-dualstack"):
            test_servers.append(
                self.startTestServers(
                    server_runner=self.server_runner,
                    address_type="ipv4_ipv6",
                )[0]
            )

        with self.subTest("03_start_test_server-v4"):
            test_servers.append(
                self.startTestServers(
                    server_runner=self.v4_server_runner,
                    address_type="ipv4",
                )[0]
            )

        with self.subTest("03_start_test_server-v6"):
            test_servers.append(
                self.startTestServers(
                    server_runner=self.v6_server_runner,
                    address_type="ipv6",
                )[0]
            )

        logger.info("Test servers: %s", test_servers)

        with self.subTest("04_add_server_backends_to_backend_services"):
            (
                neg_name,
                neg_zones,
            ) = self.server_runner.k8s_namespace.parse_service_neg_status(
                self.server_runner.service_name, self.server_port
            )

            # Add backends to the Backend Service
            self.td.backend_service_add_neg_backends(
                neg_name, neg_zones, max_rate_per_endpoint=5
            )

        test_client: _XdsTestClient
        with self.subTest("05_start_test_client"):
            test_client = self.startTestClient(test_servers[0])

        with self.subTest("06_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("07_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

        with self.subTest("08_confirm_all_servers_receive_traffic"):
            self.assertRpcsEventuallyGoToGivenServers(
                test_client,
                test_servers,
                retry_timeout=dt.timedelta(minutes=5),
                retry_wait=dt.timedelta(seconds=5),
            )


if __name__ == "__main__":
    absltest.main()
