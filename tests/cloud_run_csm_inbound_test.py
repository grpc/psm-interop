# Copyright 2020 gRPC authors.
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
import time

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_testcase

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)


class CloudRunCsmInboundTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):
    def test_gke_to_cloud_run_setup(self):
        with self.subTest("0_started"):
            time.sleep(2)

        with self.subTest("1_executing"):
            time.sleep(2)

        with self.subTest("2_finished"):
            time.sleep(2)

    def test_cloud_run_to_cloud_run_setup(self):
        with self.subTest("0_started"):
            time.sleep(2)

        with self.subTest("1_executing"):
            time.sleep(2)

        with self.subTest("2_finished"):
            time.sleep(2)


if __name__ == "__main__":
    absltest.main(failfast=True)
