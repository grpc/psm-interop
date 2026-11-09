# Copyright 2026 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from unittest import mock

from absl.testing import absltest

from framework.test_app import server_app
from framework.xds_k8s_testcase import RegularXdsKubernetesTestCase


class RegionalUriTest(absltest.TestCase):
    """Unit tests for regional target and expected URI construction logic."""

    def test_regional_server_target_uri_construction(self):
        """Verifies server target construction with xDS authority vs global."""
        test_case = mock.create_autospec(
            RegularXdsKubernetesTestCase, instance=True
        )
        test_server = server_app.XdsTestServer(
            ip="10.0.0.1", rpc_port=8080, hostname="test-server"
        )
        test_case.server_runner = mock.Mock()
        test_case.server_runner.run.return_value = [test_server]
        test_case.server_port = 8080
        test_case.server_maintenance_port = 8080
        test_case.server_xds_host = "my-test-service"
        test_case.server_xds_port = 8070
        test_case._start_test_client = mock.Mock()

        # Scenario 1: Regional xDS authority is configured
        test_case.server_xds_authority = (
            "traffic-director.us-west1.xds.googleapis.com"
        )
        RegularXdsKubernetesTestCase.startTestServers(test_case)
        RegularXdsKubernetesTestCase.startTestClient(test_case, test_server)

        test_case._start_test_client.assert_called_with(
            "xds://traffic-director.us-west1.xds.googleapis.com/my-test-service:8070"
        )

        # Scenario 2: Authority is NOT configured (global fallback)
        test_case._start_test_client.reset_mock()
        test_case.server_xds_authority = None
        RegularXdsKubernetesTestCase.startTestServers(test_case)
        RegularXdsKubernetesTestCase.startTestClient(test_case, test_server)

        test_case._start_test_client.assert_called_with(
            "xds:///my-test-service:8070"
        )


if __name__ == "__main__":
    absltest.main()
