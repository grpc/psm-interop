# Copyright 2021 The gRPC Authors
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
from typing import Tuple
import unittest

from absl import flags
from absl.testing import absltest
import grpc

from framework import xds_url_map_testcase
from framework.helpers import skips
from framework.rpc import grpc_csds
from framework.rpc import grpc_testing
from framework.test_app import client_app

# Type aliases
HostRule = xds_url_map_testcase.HostRule
PathMatcher = xds_url_map_testcase.PathMatcher
GcpResourceManager = xds_url_map_testcase.GcpResourceManager
ExpectedResult = xds_url_map_testcase.ExpectedResult
XdsTestClient = client_app.XdsTestClient
XdsUrlMapTestCase = xds_url_map_testcase.XdsUrlMapTestCase

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_url_map_testcase)

# Constants
# The first batch of RPCs don't count towards the result of test case. They are
# meant to prove the communication between driver and client is fine.
_NUM_RPCS = 25
_LENGTH_OF_RPC_SENDING_SEC = 10
_ERROR_TOLERANCE = 0.1


class _BaseXdsTimeOutTestCase(XdsUrlMapTestCase):
    @staticmethod
    def url_map_change(
        host_rule: HostRule, path_matcher: PathMatcher
    ) -> Tuple[HostRule, PathMatcher]:
        path_matcher["routeRules"] = [
            {
                "priority": 0,
                "matchRules": [
                    {"fullPathMatch": "/grpc.testing.TestService/UnaryCall"}
                ],
                "service": GcpResourceManager().default_backend_service(),
                "routeAction": {
                    "maxStreamDuration": {
                        "seconds": 3,
                    },
                },
            }
        ]
        return host_rule, path_matcher

    def xds_config_validate(self, xds_config: grpc_csds.DumpedXdsConfig):
        self.assertNumEndpoints(xds_config, 1)
        self.assertEqual(
            xds_config.rds["virtualHosts"][0]["routes"][0]["route"][
                "maxStreamDuration"
            ]["maxStreamDuration"],
            "3s",
        )
        self.assertEqual(
            xds_config.rds["virtualHosts"][0]["routes"][0]["route"][
                "maxStreamDuration"
            ]["grpcTimeoutHeaderMax"],
            "3s",
        )

    def rpc_distribution_validate(self, unused_test_client):
        raise NotImplementedError()


class TestTimeoutInRouteRule(_BaseXdsTimeOutTestCase):
    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        # TODO(lidiz) either add support for rpc-behavior to other languages, or we
        # should always use Java server as backend.
        if config.server_lang != skips.Lang.JAVA:
            return False
        if config.client_lang == skips.Lang.NODE:
            return config.version_gte("v1.4.x")
        return True

    def rpc_distribution_validate(self, test_client: XdsTestClient):
        self.configure_and_send(
            test_client,
            rpc_types=grpc_testing.RPC_TYPES_BOTH_CALLS,
            # UnaryCall and EmptyCall both sleep-4.
            # UnaryCall timeouts, EmptyCall succeeds.
            metadata=(
                (grpc_testing.RPC_TYPE_UNARY_CALL, "rpc-behavior", "sleep-4"),
                (grpc_testing.RPC_TYPE_EMPTY_CALL, "rpc-behavior", "sleep-4"),
            ),
            num_rpcs=_NUM_RPCS,
        )
        self.assertRpcStatusCode(
            test_client,
            expected=(
                ExpectedResult(
                    rpc_type=grpc_testing.RPC_TYPE_UNARY_CALL,
                    status_code=grpc.StatusCode.DEADLINE_EXCEEDED,
                ),
                ExpectedResult(
                    rpc_type=grpc_testing.RPC_TYPE_EMPTY_CALL,
                    status_code=grpc.StatusCode.OK,
                ),
            ),
            length=_LENGTH_OF_RPC_SENDING_SEC,
            tolerance=_ERROR_TOLERANCE,
        )


class TestTimeoutInApplication(_BaseXdsTimeOutTestCase):
    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        # TODO(lidiz) either add support for rpc-behavior to other languages, or we
        # should always use Java server as backend.
        if config.server_lang != skips.Lang.JAVA:
            return False
        if config.client_lang == skips.Lang.NODE:
            return config.version_gte("v1.4.x")
        return True

    def rpc_distribution_validate(self, test_client: XdsTestClient):
        self.configure_and_send(
            test_client,
            rpc_types=(grpc_testing.RPC_TYPE_UNARY_CALL,),
            # UnaryCall only with sleep-2; timeout=1s; calls timeout.
            metadata=(
                (grpc_testing.RPC_TYPE_UNARY_CALL, "rpc-behavior", "sleep-2"),
            ),
            app_timeout=1,
            num_rpcs=_NUM_RPCS,
        )
        self.assertRpcStatusCode(
            test_client,
            expected=(
                ExpectedResult(
                    rpc_type=grpc_testing.RPC_TYPE_UNARY_CALL,
                    status_code=grpc.StatusCode.DEADLINE_EXCEEDED,
                ),
            ),
            length=_LENGTH_OF_RPC_SENDING_SEC,
            tolerance=_ERROR_TOLERANCE,
        )


class TestTimeoutNotExceeded(_BaseXdsTimeOutTestCase):
    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang == skips.Lang.NODE:
            return config.version_gte("v1.4.x")
        return True

    def rpc_distribution_validate(self, test_client: XdsTestClient):
        self.configure_and_send(
            test_client,
            # UnaryCall only with no sleep; calls succeed.
            rpc_types=(grpc_testing.RPC_TYPE_UNARY_CALL,),
            num_rpcs=_NUM_RPCS,
        )
        self.assertRpcStatusCode(
            test_client,
            expected=(
                ExpectedResult(
                    rpc_type=grpc_testing.RPC_TYPE_UNARY_CALL,
                    status_code=grpc.StatusCode.OK,
                ),
            ),
            length=_LENGTH_OF_RPC_SENDING_SEC,
            tolerance=_ERROR_TOLERANCE,
        )


def load_tests(loader: absltest.TestLoader, unused_tests, unused_pattern):
    suite = unittest.TestSuite()
    test_cases = [
        TestTimeoutInRouteRule,
        TestTimeoutInApplication,
        TestTimeoutNotExceeded,
    ]
    for test_class in test_cases:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    return suite


if __name__ == "__main__":
    absltest.main()
