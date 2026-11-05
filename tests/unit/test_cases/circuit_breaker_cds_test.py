# Copyright 2026 gRPC authors.
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
from unittest import mock

from absl.testing import absltest

from framework.rpc import grpc_csds
from framework.xds_k8s_testcase import RegularXdsKubernetesTestCase


def _make_cds_config(max_requests: int) -> grpc_csds.DumpedXdsConfig:
    config = grpc_csds.DumpedXdsConfig({})
    config.cds = [
        {
            "name": "cds_test-backend-service",
            "circuitBreakers": {
                "thresholds": [{"maxRequests": max_requests}],
            },
        }
    ]
    return config


class CircuitBreakerCdsTest(absltest.TestCase):
    """Unit tests for assertCdsCircuitBreakerRequestsLimit."""

    def test_retries_until_expected_max_requests(self):
        test_case = mock.create_autospec(
            RegularXdsKubernetesTestCase, instance=True
        )
        test_client = mock.Mock()
        test_client.csds.fetch_client_status_parsed.side_effect = [
            None,
            _make_cds_config(100),
            _make_cds_config(500),
        ]

        RegularXdsKubernetesTestCase.assertCdsCircuitBreakerRequestsLimit(
            test_case,
            test_client,
            "test-backend-service",
            500,
            retry_timeout=dt.timedelta(seconds=2),
            retry_wait=dt.timedelta(milliseconds=10),
        )

        self.assertEqual(
            test_client.csds.fetch_client_status_parsed.call_count, 3
        )
        test_case.fail.assert_not_called()

    def test_fails_when_max_requests_never_updates(self):
        test_case = mock.create_autospec(
            RegularXdsKubernetesTestCase, instance=True
        )
        test_client = mock.Mock()
        test_client.csds.fetch_client_status_parsed.return_value = (
            _make_cds_config(100)
        )

        RegularXdsKubernetesTestCase.assertCdsCircuitBreakerRequestsLimit(
            test_case,
            test_client,
            "test-backend-service",
            500,
            retry_timeout=dt.timedelta(milliseconds=50),
            retry_wait=dt.timedelta(milliseconds=10),
        )

        self.assertGreaterEqual(
            test_client.csds.fetch_client_status_parsed.call_count, 2
        )
        test_case.fail.assert_called_once()


if __name__ == "__main__":
    absltest.main()
