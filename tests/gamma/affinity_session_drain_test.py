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
import datetime as dt
import logging
from typing import Final, NoReturn, Optional

from absl import flags
from absl.testing import absltest
from typing_extensions import TypeAlias, override

from framework import xds_gamma_testcase
from framework import xds_k8s_testcase
from framework.helpers import retryers
from framework.helpers import skips
from framework.rpc import grpc_testing
from framework.test_app import client_app
from framework.test_app import server_app
from framework.test_app.runners.k8s import gamma_server_runner
from framework.test_app.runners.k8s import k8s_xds_server_runner
from framework.test_cases import session_affinity_mixin

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases.
_Lang: TypeAlias = skips.Lang

# Constants.
REPLICA_COUNT: Final[int] = 3
# We never actually hit this timeout under normal circumstances, so this large
# value is acceptable.
# TODO(sergiitk): update comment
TERMINATION_GRACE_PERIOD: Final[dt.timedelta] = dt.timedelta(minutes=10)
DRAINING_TIMEOUT: Final[dt.timedelta] = dt.timedelta(minutes=10)
WAIT_FOR_CSDS_DRAINING_TIMEOUT: Final[dt.timedelta] = dt.timedelta(minutes=1)
TRAFFIC_PIN_TIMEOUT: Final[dt.timedelta] = dt.timedelta(minutes=1)
TRAFFIC_PIN_RETRY_WAIT: Final[dt.timedelta] = dt.timedelta(seconds=5)


class AffinitySessionDrainTest(  # pylint: disable=too-many-ancestors
    xds_gamma_testcase.GammaXdsKubernetesTestCase,
    session_affinity_mixin.SessionAffinityMixin,
):
    @staticmethod
    @override
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang == _Lang.CPP and config.server_lang == _Lang.CPP:
            # HookService is only added in CPP ....
            # TODO(sergiitk): Clarify the version.
            return config.version_gte("v1.61.x")
        return False

    @override
    def initKubernetesServerRunner(
        self, **kwargs
    ) -> gamma_server_runner.GammaServerRunner:
        # Note: no need for termination_grace_period for all servers,
        # we simply call pod delete with needed termination_grace_period.
        deployment_args = k8s_xds_server_runner.ServerDeploymentArgs(
            pre_stop_hook=True,
        )
        return super().initKubernetesServerRunner(
            deployment_args=deployment_args,
        )

    @override
    def getClientRpcStats(
        self,
        test_client: client_app.XdsTestClient,
        num_rpcs: int,
        *,
        metadata_keys: Optional[tuple[str, ...]] = None,
    ) -> grpc_testing.LoadBalancerStatsResponse:
        """Load all metadata_keys by default."""
        if not metadata_keys:
            metadata_keys = (session_affinity_mixin.COOKIE_METADATA_KEY,)
        return super().getClientRpcStats(
            test_client, num_rpcs, metadata_keys=metadata_keys
        )

    def test_session_drain(self):
        test_servers: list[server_app.XdsTestServer]
        # Pod names correspond to test_server hostnames.
        pod_names: tuple[str]
        with self.subTest("01_run_test_server"):
            test_servers = self.startTestServers(replica_count=REPLICA_COUNT)
            pod_names = tuple(self.server_runner.pods_started.keys())
            self.assertLen(pod_names, REPLICA_COUNT)

        with self.subTest("02_create_ssa_policies"):
            self.server_runner.create_session_affinity_policy_route()
            self.server_runner.create_backend_policy(
                draining_timeout=DRAINING_TIMEOUT,
            )

        # Default is round-robin LB policy.
        # TODO(sergiitk): what does it mean? The doc says MAGLEV.
        # https://cloud.google.com/kubernetes-engine/docs/how-to/configure-gateway-resources#session_affinity

        cookie: str
        test_client: client_app.XdsTestClient
        chosen_server: server_app.XdsTestServer

        with self.subTest("03_start_test_client"):
            test_client = self.startTestClient(test_servers[0])

        with self.subTest("04_confirm_all_servers_receive_traffic"):
            # Note: the output of this call may or may not print out the
            # cookie. This is *not* any kind of signal, just depends on system
            # latency. This subtest does not use any cookies.
            self.assertRpcsEventuallyGoToGivenServers(
                test_client,
                test_servers,
                num_rpcs=120,  # Nice and even.
                retry_timeout=TRAFFIC_PIN_TIMEOUT,
                retry_wait=TRAFFIC_PIN_RETRY_WAIT,
            )
            logger.info(
                "Confirmed all servers received traffic: %s",
                [server.hostname for server in test_servers],
            )

        with self.subTest("05_retrieve_cookie"):
            cookie, chosen_server = self.assertSsaCookieAssigned(
                test_client, test_servers
            )
            logger.info(
                "Chosen server: %s, cookie: %s", chosen_server.hostname, cookie
            )

        with self.subTest("06_only_chosen_server_receives_rpcs_with_cookie"):
            logger.info("Configuring client to send cookie %s", cookie)
            test_client.update_config.configure_unary(
                metadata=(
                    (grpc_testing.RPC_TYPE_UNARY_CALL, "cookie", cookie),
                ),
            )
            self.assertRpcsEventuallyGoToGivenServers(
                test_client,
                (chosen_server,),
                retry_timeout=TRAFFIC_PIN_TIMEOUT,
                retry_wait=TRAFFIC_PIN_RETRY_WAIT,
            )
            logger.info("Confirmed all RPCs went to %s", chosen_server.hostname)

        with self.subTest("07_stopping_chosen_server"):
            self.server_runner.request_pod_deletion(
                chosen_server.hostname,
                grace_period=TERMINATION_GRACE_PERIOD,
            )
            self.server_runner._pod_stopped_logic(chosen_server.hostname)

        with self.subTest("08_test_client_csds_shows_chosen_server_draining"):
            self.wait_for_draining_endpoint_count(test_client, 1)

        with self.subTest("09_chosen_server_receives_rpcs_while_draining"):
            self.assertRpcsEventuallyGoToGivenServers(
                test_client,
                (chosen_server,),
                retry_timeout=TRAFFIC_PIN_TIMEOUT,
                retry_wait=TRAFFIC_PIN_RETRY_WAIT,
            )
            logger.info(
                "Confirmed all RPCs are still sent to the chosen server"
                " while it's in the DRAINING state: %s",
                chosen_server.hostname,
            )

        with self.subTest("10_repin_cookie"):
            logger.info("Configuring test client to not send cookie metadata")
            test_client.update_config.configure_unary()

            # Find another server
            # TODO(sergiitk): we should return a map cookie-server.
            # TODO(sergiitk): confirm the new server is selected
            new_cookie, new_chosen_server = self.assertSsaCookieAssigned(
                test_client, test_servers
            )
            logger.info(
                "Chosen server: %s, cookie: %s",
                new_chosen_server.hostname,
                new_cookie,
            )

        with self.subTest("11_new_chosen_server_receives_rpcs_with_cookie"):
            logger.info("Configuring client to send cookie %s", cookie)
            test_client.update_config.configure_unary(
                metadata=(
                    (grpc_testing.RPC_TYPE_UNARY_CALL, "cookie", new_cookie),
                ),
            )
            self.assertRpcsEventuallyGoToGivenServers(
                test_client, (new_chosen_server,)
            )
            logger.info(
                "Confirmed all RPCs are still sent to the new chosen server"
                " while it's in the DRAINING state: %s",
                chosen_server.hostname,
            )

        with self.subTest("12_chosen_server_prestop_exit"):
            chosen_server.send_prestop_hook_release()

    def wait_for_draining_endpoint_count(
        self,
        test_client: client_app.XdsTestClient,
        expected_count: int,
        *,
        log_level: int = logging.INFO,
    ) -> NoReturn:
        retryer = retryers.constant_retryer(
            wait_fixed=dt.timedelta(seconds=10),
            timeout=WAIT_FOR_CSDS_DRAINING_TIMEOUT,
            check_result=lambda endpoints: len(endpoints) == expected_count,
        )
        try:
            retryer(
                self._get_csds_endpoints_draining,
                test_client,
                log_level=log_level,
            )
        except retryers.RetryError as retry_err:
            if retry_err.exception() is not None:
                reason = retry_err.exception_str()
            else:
                reason = retry_err.result_str()
            logger.error(
                "Timeout waiting for test client %s to report %i endpoint"
                " in DRAINING state. %s",
                test_client.hostname,
                expected_count,
                reason,
            )
            raise

    @classmethod
    def _get_csds_endpoints_draining(
        cls,
        test_client: client_app.XdsTestClient,
        *,
        log_level: int = logging.INFO,
    ) -> list[str]:
        client_config = test_client.csds.fetch_client_status_parsed(
            log_level=log_level,
        )
        if not client_config:
            return []

        logging.log(
            log_level,
            "Found EDS endpoints: HEALTHY: %s, DRAINING: %s",
            client_config.endpoints,
            client_config.draining_endpoints,
        )
        return client_config.draining_endpoints


if __name__ == "__main__":
    absltest.main()
