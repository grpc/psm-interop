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
from typing import Final, Optional

from absl import flags
from absl.testing import absltest
from typing_extensions import TypeAlias, override

from framework import xds_gamma_testcase
from framework import xds_k8s_testcase
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
REPLICA_COUNT: Final[int] = 4
# We never actually hit this timeout under normal circumstances, so this large
# value is acceptable.
# TODO(sergiitk): update comment
TERMINATION_GRACE_PERIOD: Final[dt.timedelta] = dt.timedelta(minutes=10)
DRAINING_TIMEOUT: Final[dt.timedelta] = dt.timedelta(minutes=10)
TRAFFIC_PIN_TIMEOUT: Final[dt.timedelta] = dt.timedelta(minutes=2)
TRAFFIC_PIN_RETRY_WAIT: Final[dt.timedelta] = dt.timedelta(seconds=5)


class AffinitySessionDrainTest(  # pylint: disable=too-many-ancestors
    xds_gamma_testcase.GammaXdsKubernetesTestCase,
    session_affinity_mixin.SessionAffinityMixin,
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Force the python client to use the reference server image (C++)
        # because the python server doesn't yet support session drain test.
        if cls.lang_spec.client_lang == _Lang.PYTHON:
            cls.server_image = cls.csm_server_image_canonical

    @staticmethod
    @override
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang in _Lang.CPP | _Lang.PYTHON:
            return config.version_gte("v1.62.x")
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
            **kwargs,
        )

    @override
    def getClientRpcStats(
        self,
        test_client: client_app.XdsTestClient,
        num_rpcs: int,
        *,
        metadata_keys: Optional[tuple[str, ...]] = None,
        secure_channel: bool = False,
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
        initial_pods: set[str]
        with self.subTest("01_run_test_server"):
            test_servers = self.startTestServers(replica_count=REPLICA_COUNT)
            initial_pods = set(self.server_runner.pods_started.keys())
            self.assertLen(initial_pods, REPLICA_COUNT)

        with self.subTest("02_create_ssa_policies"):
            self.server_runner.create_session_affinity_policy_route()
            self.server_runner.create_backend_policy(
                draining_timeout=DRAINING_TIMEOUT,
            )

        # Default is round-robin LB policy.
        # TODO(sergiitk): what does it mean? The doc says MAGLEV.
        # https://cloud.google.com/kubernetes-engine/docs/how-to/configure-gateway-resources#session_affinity

        test_client: client_app.XdsTestClient
        with self.subTest("03_start_test_client"):
            test_client = self.startTestClient(test_servers[0])

        with self.subTest("04_confirm_at_least_two_servers_receive_traffic"):
            # Note: the output of this call may or may not print out the
            # cookie. This is *not* any kind of signal, just depends on system
            # latency. This subtest does not use any cookies.
            self.assertRpcsEventuallyReachMinServers(
                test_client,
                num_expected_servers=2,
                num_rpcs=120,  # Nice and even.
                retry_timeout=TRAFFIC_PIN_TIMEOUT,
                retry_wait=TRAFFIC_PIN_RETRY_WAIT,
            )
            logger.info("Confirmed at least two servers received traffic")

        with self.subTest("05_retrieve_cookie"):
            result = self.assertSsaCookieAssigned(test_client, test_servers)
            cookie: Final[str] = result[0]
            chosen_server: Final[server_app.XdsTestServer] = result[1]
            # The name of the chosen server, same as the pod name.
            chosen_name: Final[str] = chosen_server.hostname
            logger.info("Chosen server: %s, cookie: %s", chosen_name, cookie)

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
            logger.info("Confirmed all RPCs went to %s", chosen_name)

        with self.subTest("07_stopping_chosen_server"):
            self.server_runner.delete_pod(
                chosen_name,
                grace_period=TERMINATION_GRACE_PERIOD,
                ignore_errors=False,
                wait_for_deletion=False,
            )

        with self.subTest("08_test_client_csds_shows_chosen_server_draining"):
            self.assertDrainingEndpointsCount(test_client, 1)

        with self.subTest("09_pinned_traffic_stays_on_draining_server"):
            self.assertRpcsEventuallyGoToGivenServers(
                test_client,
                (chosen_server,),
                retry_timeout=TRAFFIC_PIN_TIMEOUT,
                retry_wait=TRAFFIC_PIN_RETRY_WAIT,
            )
            logger.info(
                "Confirmed all RPCs with Cookie header are still sent to the"
                " chosen server while it's in the DRAINING state: %s",
                chosen_name,
            )

        with self.subTest("10_new_traffic_not_sent_to_draining_server"):
            logger.info("Configuring client to send no cookies")
            test_client.update_config.configure_unary()
            # Note: we may need a retryer here if found to be flaky.
            lb_stats = self.getClientRpcStats(test_client, num_rpcs=120)
            peers = set(lb_stats.rpcs_by_peer.keys())
            self.assertNotIn(
                chosen_name,
                peers,
                f"Draining server {chosen_name} received new traffic.",
            )
            not_draining = initial_pods - {chosen_name}
            self.assertContainsSubset(
                not_draining,
                peers,
                f"Initial servers {not_draining} did not receive new traffic",
            )
            logger.info(
                "Confirmed draining server %s received no new traffic,"
                " while %s did",
                chosen_name,
                not_draining,
            )

        with self.subTest("11_chosen_server_release_prestop"):
            logger.info("Releasing prestop hook on, %s", chosen_name)
            chosen_server.release_prestop_hook()

        with self.subTest("12_confirm_chosen_server_stopped"):
            logger.info("Waiting on the chosen server to be deleted")
            self.server_runner.k8s_namespace.wait_for_pod_deleted(chosen_name)
            lb_stats = self.getClientRpcStats(test_client, num_rpcs=30)
            # Shouldn't happen.
            self.assertNotIn(
                chosen_name,
                set(lb_stats.rpcs_by_peer.keys()),
                f"Chosen server {chosen_name} received traffic after deletion",
            )

        # TODO(sergiitk): refresh server list when implemented. Handle new pod
        #   port forwarding, logging. Handle deleted pod stop logic.

        with self.subTest("13_get_new_cookie_and_new_server"):
            # Find another server
            result = self.assertSsaCookieAssigned(test_client, test_servers)
            new_cookie: Final[str] = result[0]
            new_server: Final[server_app.XdsTestServer] = result[1]
            new_name: Final[str] = new_server.hostname
            self.assertNotEqual(
                chosen_name,
                new_name,
                f"Shouldn't happen: chosen server {chosen_name} is deleted",
            )
            logger.info(
                "New chosen server: %s, cookie: %s", new_name, new_cookie
            )

        with self.subTest("14_new_chosen_server_receives_rpcs_with_cookie"):
            logger.info("Configuring client to send cookie %s", cookie)
            test_client.update_config.configure_unary(
                metadata=(
                    (grpc_testing.RPC_TYPE_UNARY_CALL, "cookie", new_cookie),
                ),
            )
            self.assertRpcsEventuallyGoToGivenServers(
                test_client,
                (new_server,),
                retry_timeout=TRAFFIC_PIN_TIMEOUT,
                retry_wait=TRAFFIC_PIN_RETRY_WAIT,
            )
            logger.info(
                "Confirmed all RPCs went to the new chosen server: %s", new_name
            )


if __name__ == "__main__":
    absltest.main()
