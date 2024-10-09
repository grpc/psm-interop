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
"""Base test case used for xds test suites."""
import inspect
import traceback
from typing import Optional, Union
import unittest

from absl import logging
from absl.testing import absltest


class BaseTestCase(absltest.TestCase):
    # @override
    def run(self, result: unittest.TestResult | None = None) -> None:
        super().run(result)
        # TODO(sergiitk): should this method be returning result? See
        #   super().run and xds_k8s_testcase.XdsKubernetesBaseTestCase.subTest
        test_errors = [error for test, error in result.errors if test is self]
        test_failures = [
            failure for test, failure in result.failures if test is self
        ]
        test_unexpected_successes = [
            test for test in result.unexpectedSuccesses if test is self
        ]
        test_skipped = next(
            (reason for test, reason in result.skipped if test is self),
            None,
        )
        # Assume one test case will only have one status.
        if test_errors or test_failures:
            total_errors = len(test_errors) + len(test_failures)
            logging.error(
                "----- PSM Test Case FAILED: %s -----", self.test_name
            )
            self._log_test_errors(test_errors, is_unexpected=True)
            self._log_test_errors(test_failures)
            logging.info(
                "----- PSM Test Case %s Error Count: %s -----",
                self.test_name,
                total_errors,
            )
        elif test_unexpected_successes:
            logging.error(
                "----- PSM Test Case UNEXPECTEDLY SUCCEEDED: %s -----\n",
                self.test_name,
            )
        elif test_skipped:
            logging.info(
                "----- PSM Test Case SKIPPED: %s -----"
                "\nReason for skipping: %s",
                self.test_name,
                test_skipped,
            )
        else:
            logging.info(
                "----- PSM Test Case PASSED: %s -----\n",
                self.test_name,
            )

    @property
    def test_name(self) -> str:
        """Pretty test name (details in the description).

        Test id returned from self.id() can have two forms:
        1. Regular:       __main__.MyClassTest.test_method
        2. Parametrized:  __main__.MyClassTest.test_method{case} ('param'),
           where {case} is
           a) An integer for @parameterized.parameters: test_method0,
              test_method1, ...
           b) {testcase_name} for @parameterized.named_parameters:
              test_method_pass, test_method_fail, ...

        This method:
        1. Removes "__main__." if it's present
        2. Removes the " ('param')" if present
        """
        return self.id().removeprefix("__main__.").split(" ", 1)[0]

    def _log_test_errors(self, errors: list[str], is_unexpected: bool = False):
        for err in errors:
            self._log_framed_test_failure(self.test_name, err, is_unexpected)

    @classmethod
    def _log_class_hook_failure(cls, error: Exception):
        """
        Log error helper for failed unittest hooks, e.g. setUpClass.

        Normally we don't want to make external calls in setUpClass.
        But when we do, we want to wrap them into try/except, and call
        _log_class_hook_failure, so the error is logged in our standard format.
        Don't forget to re-raise!

        Example:
            @classmethod
            def setUpClass(cls):
                try:
                    # Making bad external calls that end up raising
                    raise OSError("Network bad!")
                except Exception as error:  # noqa pylint: disable=broad-except
                    cls._log_class_hook_failure(error)
                    raise
        """
        caller: str
        try:
            caller_info: inspect.FrameInfo = inspect.stack()[1]
            caller: str = caller_info.function
        except (IndexError, AttributeError):
            caller = "undefined_hook"

        fake_test_id = f"{cls.__name__}.{caller}"
        # The same test name transformation as in self.test_name().
        # TODO(sergiitk): move the transformation to a classmethod.
        test_name = fake_test_id.removeprefix("__main__.").split(" ", 1)[0]
        logging.error("----- PSM Test Case FAILED: %s -----", test_name)
        cls._log_framed_test_failure(test_name, error, is_unexpected=True)

    @classmethod
    def _log_framed_test_failure(
        cls,
        test_name: str,
        error: Union[str, Exception],
        is_unexpected: bool = False,
    ) -> None:
        trace: str
        if isinstance(error, Exception):
            trace = cls._format_error_with_trace(error)
        else:
            trace = error

        # FAILURE is an errors explicitly signalled using one of the
        # TestCase.assert*() methods, while ERROR means an unexpected exception.
        fail_type: str = "ERROR" if is_unexpected else "FAILURE"
        logging.error(
            "(%(fail_type)s) PSM Interop Test Failed: %(test_id)s"
            "\n^^^^^"
            "\n# [%(test_id)s] PSM Failed Test Traceback BEGIN"
            "\n%(error)s"
            "# [%(test_id)s] PSM Failed Test Traceback END\n",
            {
                "test_id": test_name,
                "fail_type": fail_type,
                "error": trace,
            },
        )

    @classmethod
    def _format_error_with_trace(cls, error: Exception) -> str:
        return "".join(
            traceback.TracebackException.from_exception(error).format()
        )
