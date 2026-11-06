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
from google.rpc import code_pb2

from framework.infrastructure import gcp

_TIMEOUT_MSG = "Waiting for container to become healthy. Deadline exceeded."


def _op_error(code: int, message: str) -> gcp.api.OperationError:
    return gcp.api.OperationError(
        "Cloud Run",
        {
            "name": "op-1",
            "done": True,
            "error": {"code": code, "message": message},
        },
    )


class CloudRunV2Test(absltest.TestCase):
    def setUp(self):
        super().setUp()
        api_manager = mock.create_autospec(gcp.api.GcpApiManager, instance=True)
        self.client = gcp.cloud_run.CloudRunV2(
            api_manager=api_manager, project="test-proj", region="us-central1"
        )
        self.enter_context(
            mock.patch.object(
                self.client, "_CREATE_SERVICE_RETRY_WAIT", dt.timedelta(0)
            )
        )
        self.events: list[str] = []
        self.mock_create = self.enter_context(
            mock.patch.object(self.client, "_create_resource", autospec=True)
        )
        self.mock_delete = self.enter_context(
            mock.patch.object(
                self.client,
                "delete_service",
                autospec=True,
                side_effect=lambda name: self.events.append(f"delete:{name}"),
            )
        )

    def test_create_service_retries_on_deadline_exceeded(self):
        outcomes = iter(
            [_op_error(code_pb2.DEADLINE_EXCEEDED, _TIMEOUT_MSG), None]
        )

        def _create(**kwargs):
            self.events.append(f"create:{kwargs['serviceId']}")
            if (err := next(outcomes)) is not None:
                raise err

        self.mock_create.side_effect = _create

        self.client.create_service("svc", {"template": {}})

        self.assertSequenceEqual(
            self.events, ["create:svc", "delete:svc", "create:svc"]
        )

    def test_create_service_does_not_retry_other_errors(self):
        self.mock_create.side_effect = _op_error(
            code_pb2.INVALID_ARGUMENT, "Container import failed."
        )

        with self.assertRaises(gcp.api.OperationError) as ctx:
            self.client.create_service("svc", {"template": {}})

        self.assertEqual(ctx.exception.error.code, code_pb2.INVALID_ARGUMENT)
        self.assertEqual(self.mock_create.call_count, 1)
        self.mock_delete.assert_not_called()

    def test_create_service_raises_after_exhausting_retries(self):
        def _always_timeout(**kwargs):
            self.events.append(f"create:{kwargs['serviceId']}")
            raise _op_error(code_pb2.DEADLINE_EXCEEDED, _TIMEOUT_MSG)

        self.mock_create.side_effect = _always_timeout

        with self.assertRaises(gcp.api.OperationError) as ctx:
            self.client.create_service("svc", {"template": {}})

        self.assertEqual(ctx.exception.error.code, code_pb2.DEADLINE_EXCEEDED)
        self.assertSequenceEqual(
            self.events,
            [
                "create:svc",
                "delete:svc",
                "create:svc",
                "delete:svc",
                "create:svc",
            ],
        )


if __name__ == "__main__":
    absltest.main()
