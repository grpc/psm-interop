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
from unittest import mock

from absl.testing import absltest

from framework.infrastructure.gcp.api import GcpProjectApiResource
from framework.infrastructure.gcp.api import OperationTimeoutError


class OperationTimeoutErrorTest(absltest.TestCase):
    def test_with_operation_dict(self):
        op_dict = {"name": "operations/test-op-123", "done": False}
        err = OperationTimeoutError(
            timeout_sec=1200,
            operation=op_dict,
            api_name="Cloud Run",
        )
        self.assertEqual(err.timeout_sec, 1200)
        self.assertEqual(err.api_name, "Cloud Run")
        self.assertEqual(err.name, "operations/test-op-123")
        self.assertEqual(err.operation, op_dict)
        self.assertIn("Cloud Run", str(err))
        self.assertIn("operations/test-op-123", str(err))
        self.assertIn("1200 seconds", str(err))
        self.assertIn("test-op-123", str(err))

    def test_without_operation_dict(self):
        err = OperationTimeoutError(
            timeout_sec=600,
            api_name="compute",
        )
        self.assertEqual(err.timeout_sec, 600)
        self.assertEqual(err.api_name, "compute")
        self.assertEqual(err.name, "unknown")
        self.assertIsNone(err.operation)
        self.assertIn(
            "compute operation timed out waiting to complete after 600 seconds.",
            str(err),
        )


class WaitForOperationTest(absltest.TestCase):
    def test_successful_operation(self):
        op_request = mock.MagicMock()
        op_request.execute.return_value = {
            "name": "op-1",
            "done": True,
        }

        result = GcpProjectApiResource.wait_for_operation(
            operation_request=op_request,
            test_success_fn=lambda op: op.get("done") is True,
            timeout_sec=5,
            wait_sec=0.01,
        )

        self.assertEqual(result, {"name": "op-1", "done": True})
        op_request.execute.assert_called_once()

    def test_timeout_raises_operation_timeout_error(self):
        op_request = mock.MagicMock()
        in_progress_op = {
            "name": "operations/cloudrun-123",
            "done": False,
        }
        op_request.execute.return_value = in_progress_op

        with self.assertRaises(OperationTimeoutError) as ctx:
            GcpProjectApiResource.wait_for_operation(
                operation_request=op_request,
                test_success_fn=lambda op: op.get("done") is True,
                timeout_sec=0.05,
                wait_sec=0.01,
                api_name="Cloud Run",
            )

        err = ctx.exception
        self.assertEqual(err.timeout_sec, 0.05)
        self.assertEqual(err.api_name, "Cloud Run")
        self.assertEqual(err.name, "operations/cloudrun-123")
        self.assertEqual(err.operation, in_progress_op)
        self.assertIn("Cloud Run", str(err))
        self.assertIn("operations/cloudrun-123", str(err))

    def test_exception_on_all_attempts_reraises_underlying_exception(self):
        op_request = mock.MagicMock()
        op_request.execute.side_effect = ConnectionResetError(
            "connection reset"
        )

        with self.assertRaises(ConnectionResetError):
            GcpProjectApiResource.wait_for_operation(
                operation_request=op_request,
                test_success_fn=lambda op: op.get("done") is True,
                timeout_sec=0.05,
                wait_sec=0.01,
                api_name="GCP",
            )

    def test_retries_transient_errors_then_succeeds(self):
        op_request = mock.MagicMock()
        op_request.execute.side_effect = [
            ConnectionResetError("temporary error"),
            {"name": "op-retry", "done": False},
            {"name": "op-retry", "done": True},
        ]

        result = GcpProjectApiResource.wait_for_operation(
            operation_request=op_request,
            test_success_fn=lambda op: op.get("done") is True,
            timeout_sec=5,
            wait_sec=0.01,
            api_name="GCP",
        )

        self.assertEqual(result, {"name": "op-retry", "done": True})
        self.assertEqual(op_request.execute.call_count, 3)


if __name__ == "__main__":
    absltest.main()
