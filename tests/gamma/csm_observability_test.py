# Copyright 2023 gRPC authors.
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
from google.api_core import exceptions as gapi_errors
from google.cloud import monitoring_v3

from framework import xds_gamma_testcase
from framework import xds_k8s_testcase
from framework.helpers import skips

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_Lang = skips.Lang


class CsmObservabilityTest(xds_gamma_testcase.GammaXdsKubernetesTestCase):
    metric_client: monitoring_v3.MetricServiceClient

    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang == _Lang.CPP and config.server_lang == _Lang.CPP:
            # CSM Observability Test is only supported for CPP for now.
            return config.version_gte("v1.61.x")
        return False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metric_client = cls.gcp_api_manager.monitoring_metric_service("v3")

    def test_csm_observability(self):
        # TODO(sergiitk): [GAMMA] Consider moving out custom gamma
        #   resource creation out of self.startTestServers()
        with self.subTest("1_run_test_server"):
            test_server: _XdsTestServer = self.startTestServers(
                enable_csm_observability=True
            )[0]

        with self.subTest("2_start_test_client"):
            test_client: _XdsTestClient = self.startTestClient(
                test_server, enable_csm_observability=True,
                request_payload_size=271828,
                response_payload_size=314159,
            )

        with self.subTest("3_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

        # For now, this just makes a bogus call to ensure metrics client
        # connected to the remote API service.
        with self.subTest("4_check_monitoring_metric_client"):
            with self.assertRaises(gapi_errors.GoogleAPICallError) as cm:
                self.metric_client.list_metric_descriptors(
                    request=monitoring_v3.ListMetricDescriptorsRequest(
                        name="whatever",
                    )
                )
            err = cm.exception
            self.assertIsInstance(err, gapi_errors.InvalidArgument)
            self.assertIsNotNone(err.grpc_status_code)
            self.assertStartsWith(err.message, "Name must begin with")
            self.assertEndsWith(err.message, " got: whatever")


if __name__ == "__main__":
    absltest.main()
