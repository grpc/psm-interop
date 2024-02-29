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
"""
A mixin class for stateful session affinity tests.

This mixin is shared between test environments that configure SSA via
Kubernetes CRDs and environments that configure SSA directly through the
networkservices.googleapis.com API.
"""
import datetime as dt
import logging
from typing import Final, Sequence, Union

from typing_extensions import TypeAlias

from framework import xds_k8s_testcase
from framework.helpers import retryers
from framework.rpc import grpc_testing
from framework.test_cases import testcase_mixins

# Type aliases
_XdsTestServer: TypeAlias = xds_k8s_testcase.XdsTestServer
_XdsTestClient: TypeAlias = xds_k8s_testcase.XdsTestClient
_SessionAffinityMixinType: TypeAlias = Union[
    "SessionAffinityMixin", xds_k8s_testcase.XdsKubernetesBaseTestCase
]
_Cookies = dict[str, str]

# Constants
_COOKIE_METADATA_KEY: Final[str] = "set-cookie"
_SET_COOKIE_MAX_WAIT: Final[dt.timedelta] = dt.timedelta(minutes=5)


class SessionAffinityMixin(testcase_mixins.XdsKubernetesBaseTestCaseMixin):
    def __init_subclass__(cls, **kwargs):  # pylint: disable=arguments-differ
        super().__init_subclass__(**kwargs)
        if not issubclass(cls, xds_k8s_testcase.XdsKubernetesBaseTestCase):
            raise AttributeError(
                "Must be extended by a subclass of"
                " xds_k8s_testcase.XdsKubernetesBaseTestCase"
            )

    def assertSsaCookieAssigned(
        self: _SessionAffinityMixinType,
        test_client: _XdsTestClient,
        test_servers: Sequence[_XdsTestServer],
        *,
        timeout: dt.timedelta = _SET_COOKIE_MAX_WAIT,
    ) -> tuple[str, _XdsTestServer]:
        """Retrieves the initial cookie and corresponding server.

        Given a test client and set of backends for which SSA is enabled,
        samples a single RPC from the test client to the backends,
        with metadata collection enabled. The "set-cookie" header is
        retrieved and its contents are returned along with the server to
        which it corresponds.

        Since SSA config is supplied as a separate resource from the Route
        resource, there will be periods of time when the SSA config may not
        be applied. This is therefore an eventually consistent function.
        """
        retryer = retryers.exponential_retryer_with_timeout(
            wait_min=dt.timedelta(seconds=10),
            wait_max=dt.timedelta(seconds=25),
            timeout=timeout,
            log_level=logging.INFO,
        )
        try:
            return retryer(
                self._retrieve_cookie_and_server, test_client, test_servers
            )
        except retryers.RetryError:
            logging.error(
                "Rpcs did not go to expected servers before"
                " timeout %s (h:mm:ss)",
                _SET_COOKIE_MAX_WAIT,
            )
            raise

    def _retrieve_cookie_and_server(
        self: _SessionAffinityMixinType,
        test_client: _XdsTestClient,
        servers: Sequence[_XdsTestServer],
    ) -> tuple[str, _XdsTestServer]:
        # Request stats for a single RPC.
        lb_stats = self.assertSuccessfulRpcs(test_client, 1)
        cookies = self._get_cookies_by_peer(lb_stats.metadatas_by_peer)
        if not cookies:
            self.fail("No cookie header found")
        self.assertLen(
            cookies, 1, msg="More than one server has cookies assigned"
        )
        hostname, cookie = next(iter(cookies.items()))

        chosen_server_candidates = [
            server for server in servers if server.hostname == hostname
        ]
        self.assertLen(chosen_server_candidates, 1)
        chosen_server = chosen_server_candidates[0]
        return cookie, chosen_server

    @classmethod
    def _get_cookies_by_peer(
        cls,
        metadatas_by_peer: grpc_testing.MetadatasByPeer,
    ) -> _Cookies:
        cookies: _Cookies = dict()
        for peer, peer_metadatas in metadatas_by_peer.items():
            for rpc_metadata in peer_metadatas.rpc_metadata:
                for metadata in rpc_metadata.metadata:
                    if metadata.key.lower() == _COOKIE_METADATA_KEY:
                        cookies[peer] = metadata.value
        return cookies
