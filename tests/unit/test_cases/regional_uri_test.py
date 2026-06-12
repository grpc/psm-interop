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

# Since we want to test formatting logic residing in the test case base class:
from framework.xds_k8s_testcase import RegularXdsKubernetesTestCase


class RegionalUriTest(absltest.TestCase):
    """Unit tests for regional target and expected URI construction logic."""

    def test_regional_server_target_uri_construction(self):
        """Verifies server target construction with a region vs global."""
        # Create a mock test case instance with a mock client runner
        test_case = mock.create_autospec(
            RegularXdsKubernetesTestCase, instance=True
        )
        test_case.client_runner = mock.Mock()
        test_case._start_test_client = mock.Mock()

        # Mock the server app and its addresses
        mock_server = mock.Mock()
        mock_server.xds_address = "my-test-service:8070"
        mock_server.xds_uri = "xds:///my-test-service:8070"

        # Scenario 1: Region is configured
        test_case.xds_server_region = "us-west1"
        RegularXdsKubernetesTestCase.startTestClient(test_case, mock_server)

        # Verify it built the regional xDS authority target
        test_case._start_test_client.assert_called_with(
            "xds://traffic-director.us-west1.xds.googleapis.com/my-test-service:8070"
        )

        # Scenario 2: Region is NOT configured (global fallback)
        test_case._start_test_client.reset_mock()
        test_case.xds_server_region = None
        RegularXdsKubernetesTestCase.startTestClient(test_case, mock_server)

        # Verify it falls back to the standard global target
        test_case._start_test_client.assert_called_with(
            "xds:///my-test-service:8070"
        )




if __name__ == "__main__":
    absltest.main()
