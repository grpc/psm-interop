# Copyright 2024 gRPC authors.
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
from absl.testing import parameterized

from framework import xds_k8s_testcase

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)


class FakeTest(xds_k8s_testcase.XdsKubernetesBaseTestCase):
    """A fake test class with known failures to debug BaseTestCase logs.

    This test case won't do any infra provisioning, just parses the flags and
    reads k8s contexts file.
    """

    def test_0_pass(self):
        self.assertTrue(True, "I'm a passing test in the beginning")

    def test_1_error(self):
        self.assertTrue(False, msg="Test 1 Assertion ERROR")

    def test_2_pass(self):
        self.assertTrue(True, "I'm a passing test in the middle")

    def test_3_failure(self):
        raise RuntimeError("Test 3 Uncaught Exception FAILURE")

    def test_4_pass(self):
        self.assertTrue(True, "I'm a passing test at the end")


class FakeParametrizedTest(
    xds_k8s_testcase.XdsKubernetesBaseTestCase,
    parameterized.TestCase,
):
    """A fake class to debug BaseTestCase logs produced by parametrized tests.

    See FakeTest for notes on provisioning.
    """

    @parameterized.parameters(True, False)
    def test_true(self, is_true):
        self.assertTrue(is_true)

    @parameterized.named_parameters(
        {"testcase_name": "pass", "is_true": True},
        {"testcase_name": "fail", "is_true": False},
    )
    def test_true_named(self, is_true):
        self.assertTrue(is_true)


class FakeSubtestTest(xds_k8s_testcase.XdsKubernetesBaseTestCase):
    """A fake class to debug BaseTestCase logs produced by tests with subtests.

    See FakeTest for notes on provisioning.
    """

    def test_even(self):
        for num in range(0, 6):
            with self.subTest(i=num, msg=f"num_{num}"):
                if num & 1:
                    self.fail(f"Integer {num} is odd")


if __name__ == "__main__":
    absltest.main()
