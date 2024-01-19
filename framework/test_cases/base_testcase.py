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

from typing import Optional
import unittest

from absl import logging
from absl.testing import absltest


class BaseTestCase(absltest.TestCase):
    def run(self, result: Optional[unittest.TestResult] = None) -> None:
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
                "----- PSM Test Case FAILED: %s%s -----",
                self.test_name,
                f" | Errors count: {total_errors}" if total_errors > 1 else "",
            )
            if test_errors:
                self._print_error_list(test_errors, is_unexpected_error=True)
            if test_failures:
                self._print_error_list(test_failures)
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

    def _print_error_list(
        self, errors: list[str], is_unexpected_error: bool = False
    ) -> None:
        # FAILURE is an errors explicitly signalled using one of the
        # TestCase.assert*() methods, while ERROR means an unexpected exception.
        fail_type: str = "ERROR" if is_unexpected_error else "FAILURE"
        for err in errors:
            logging.error(
                "(%(fail_type)s) PSM Interop Test Failed: %(test_id)s"
                "\n^^^^^"
                "\n[%(test_id)s] PSM Failed Test Traceback BEGIN"
                "\n%(error)s"
                "[%(test_id)s] PSM Failed Test Traceback END\n",
                {
                    "test_id": self.test_name,
                    "fail_type": fail_type,
                    "error": err,
                },
            )
