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

from absl import flags
from absl.testing import absltest

from framework import xds_k8s_testcase
from framework.helpers import rand
from framework.helpers import skips

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_SecurityMode = xds_k8s_testcase.SecurityXdsKubernetesTestCase.SecurityMode
_Lang = skips.Lang


class SecurityTest(xds_k8s_testcase.SecurityXdsKubernetesTestCase):
    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang in (
            _Lang.CPP | _Lang.GO | _Lang.JAVA | _Lang.PYTHON
        ):
            # Versions prior to v1.41.x don't support PSM Security.
            # https://github.com/grpc/grpc/blob/master/doc/grpc_xds_features.md
            return config.version_gte("v1.41.x")
        elif config.client_lang == _Lang.NODE:
            return config.version_gte("v1.13.x")
        return True

    def test_tls(self):
        """TLS test.

        Both client and server configured to use TLS and not use mTLS.
        """
        self.setupTrafficDirectorGrpc()
        self.setupSecurityPolicies(
            server_tls=True,
            server_mtls=False,
            client_tls=True,
            client_mtls=False,
        )

        test_server: _XdsTestServer = self.startSecureTestServer()
        self.setupServerBackends()
        test_client: _XdsTestClient = self.startSecureTestClient(test_server)

        self.assertTestAppSecurity(_SecurityMode.TLS, test_client, test_server)
        self.assertSuccessfulRpcs(test_client)
        logger.info("[SUCCESS] TLS security mode confirmed.")

if __name__ == "__main__":
    absltest.main()
