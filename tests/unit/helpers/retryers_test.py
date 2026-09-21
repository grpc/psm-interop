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
import datetime as dt
from unittest import mock

from absl.testing import absltest

from framework.helpers import retryers


class RetryErrorTest(absltest.TestCase):
    def test_construct(self):
        state_mock = mock.MagicMock()
        state_mock.fn = lambda val: ...
        state_mock.outcome.exception.return_value = OSError("my err")

        retry_err = retryers.RetryError(state_mock, attempts=42)
        want_msg = (
            "Retry error calling"
            " __main__.RetryErrorTest.test_construct.<locals>.<lambda>:"
            " 42 attempts exhausted. Last Retry Attempt Error: OSError(my err)"
        )
        with self.assertRaisesWithLiteralMatch(retryers.RetryError, want_msg):
            raise retry_err

        self.assertNotEmpty(str(retry_err))
        self.assertNotEmpty(repr(retry_err))

    def test_retry_with_trace(self):
        retryer = retryers.constant_retryer(
            wait_fixed=dt.timedelta(microseconds=1),
            timeout=dt.timedelta(seconds=1),
            attempts=2,
        )

        errors = (ValueError, TypeError, AttributeError)
        error_generator = self._error_generator(errors)

        with self.assertRaises(retryers.RetryError) as capture:
            retryer(self._raise_error, error_generator)

        retry_err = capture.exception
        retry_err_str = str(retry_err)
        retry_err_lines = retry_err_str.split("\n")
        self.assertEqual(
            retry_err_lines[0],
            "Retry error calling __main__.RetryErrorTest._raise_error:"
            " timeout 0:00:01 (h:mm:ss) exceeded or 2 attempts exhausted."
            " Last Retry Attempt Error:"
            " TypeError(error msg test_retry_with_trace)",
        )
        self.assertContainsInOrder(
            (
                # First line see the exact match above
                "Retry error calling",
                "Last Retry Attempt Error: TypeError(error msg test_retry_with_trace)\n",
                # our custom stack trace header
                "\n^\nLast Retry Attempt Error Traceback (most recent call last):",
                # ...
                #   File "tests/unit/helpers/retryers_test.py", line 121, in _raise_error
                #     raise err(err_msg)
                # TypeError: error msg test_retry_with_trace'
                'retryers_test.py", line',
                ", in _raise_error\n",
                "  raise err(err_msg)\n",
                "TypeError: error msg test_retry_with_trace",
            ),
            retry_err_str,
        )
        self.assertEqual(
            retry_err_lines[-1],
            "TypeError: error msg test_retry_with_trace",
        )

    def test_retry_without_trace(self):
        retryer = retryers.constant_retryer(
            wait_fixed=dt.timedelta(microseconds=1),
            timeout=dt.timedelta(seconds=1),
            attempts=3,
        )

        will_throw = (ValueError, TypeError, AttributeError)
        error_generator = self._error_generator(will_throw)

        with self.assertRaises(retryers.RetryError) as capture:
            # this will be a general pattern to disable last err trace.
            try:
                retryer(self._raise_error, error_generator)
            except retryers.RetryError as retry_err:
                retry_err.with_last_trace(False)
                raise

        # just a single line with no tracebacks
        self.assertEqual(
            str(capture.exception),
            "Retry error calling __main__.RetryErrorTest._raise_error:"
            " timeout 0:00:01 (h:mm:ss) exceeded or 3 attempts exhausted."
            " Last Retry Attempt Error:"
            " AttributeError(error msg test_retry_without_trace)",
        )

    def _raise_error(self, generator, msg: str = "error msg"):
        for err in generator:
            err_msg = f"{msg} {self.id().split('.')[-1]}"
            raise err(err_msg)

    def _error_generator(self, errors):
        for err in errors:
            yield err


if __name__ == "__main__":
    absltest.main()
