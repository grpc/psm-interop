# Copyright 2022 gRPC authors.
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
import datetime
import logging
from typing import List

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_flags
from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.rpc import grpc_testing

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)
flags.mark_flag_as_required("server_image_canonical")

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_Lang = skips.Lang

# Testing consts
_QPS = 100
_REPLICA_COUNT = 5


class OutlierDetectionTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):
    """
    Implementation of https://github.com/grpc/grpc/blob/master/doc/xds-test-descriptions.md#outlier_detection

    This test verifies that the client applies the outlier detection
    configuration and temporarily drops traffic to a server that fails
    requests.
    """

    @classmethod
    def setUpClass(cls):
        """Force the java test server for languages not yet supporting
        the `rpc-behavior` feature.

        https://github.com/grpc/grpc/blob/master/doc/xds-test-descriptions.md#server
        """
        super().setUpClass()
        client_lang = cls.lang_spec.client_lang

        # gRPC Java implemented server "error-code-" rpc-behavior in v1.47.x.
        # gRPC Node implemented the server in v1.13.x
        if client_lang == _Lang.JAVA | _Lang.NODE:
            return

        # gRPC CPP implemented server "hostname" rpc-behavior in v1.57.x,
        # see https://github.com/grpc/grpc/pull/33446.
        if client_lang == _Lang.CPP and cls.lang_spec.version_gte("v1.57.x"):
            return

        # gRPC Go implemented server "hostname" rpc-behavior in v1.59.x,
        # see https://github.com/grpc/grpc-go/pull/6575.
        if client_lang == _Lang.GO and cls.lang_spec.version_gte("v1.59.x"):
            return

        # gRPC go and python fallback to the gRPC Java.
        # TODO(https://github.com/grpc/grpc-go/issues/6288): use go server.
        # TODO(https://github.com/grpc/grpc/issues/33134): use python server.
        cls.server_image = xds_k8s_flags.SERVER_IMAGE_CANONICAL.value

    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang in _Lang.CPP | _Lang.PYTHON:
            return config.version_gte("v1.48.x")
        if config.client_lang == _Lang.JAVA:
            return config.version_gte("v1.49.x")
        if config.client_lang == _Lang.NODE:
            return config.version_gte("v1.6.x")
        if config.client_lang == _Lang.GO:
            return config.version_gte("v1.50.x")
        return False

    def test_outlier_detection(self) -> None:
        with self.subTest("00_create_health_check"):
            self.td.create_health_check()

        with self.subTest("01_create_backend_service"):
            self.td.create_backend_service(
                outlier_detection={
                    "interval": {"seconds": 2, "nanos": 0},
                    "successRateRequestVolume": 20,
                }
            )

        with self.subTest("02_create_url_map"):
            self.td.create_url_map(self.server_xds_host, self.server_xds_port)

        with self.subTest("03_create_target_proxy"):
            self.td.create_target_proxy()

        with self.subTest("04_create_forwarding_rule"):
            self.td.create_forwarding_rule(self.server_xds_port)

        test_servers: List[_XdsTestServer]
        with self.subTest("05_start_test_servers"):
            test_servers = self.startTestServers(replica_count=_REPLICA_COUNT)

        with self.subTest("06_add_server_backends_to_backend_services"):
            self.setupServerBackends()

        test_client: _XdsTestClient
        with self.subTest("07_start_test_client"):
            test_client = self.startTestClient(
                test_servers[0],
                qps=_QPS,
                wait_for_server_channel_ready_timeout=datetime.timedelta(
                    minutes=10
                ),
            )

        with self.subTest("08_test_client_xds_config_exists"):
            self.assertXdsConfigExists(test_client)

        with self.subTest("09_test_servers_received_rpcs_from_test_client"):
            self.assertRpcsEventuallyGoToGivenServers(test_client, test_servers)

        with self.subTest("10_chosen_server_removed_by_outlier_detection"):
            test_client.update_config.configure_unary(
                metadata=(
                    (
                        grpc_testing.RPC_TYPE_UNARY_CALL,
                        "rpc-behavior",
                        f"hostname={test_servers[0].hostname} error-code-2",
                    ),
                ),
            )
            self.assertRpcsEventuallyGoToGivenServers(
                test_client, test_servers[1:]
            )

        with self.subTest("11_ejected_server_returned_after_failures_stopped"):
            # Reset error injection via metadata.
            test_client.update_config.configure_unary()
            self.assertRpcsEventuallyGoToGivenServers(test_client, test_servers)


if __name__ == "__main__":
    absltest.main(failfast=True)
