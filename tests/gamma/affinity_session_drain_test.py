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
from typing import Final, List, Optional

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

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_Lang: TypeAlias = skips.Lang
# RpcTypeUnaryCall = xds_url_map_testcase.RpcTypeUnaryCall

# Constants
# TODO(sergiitk): set to 3
REPLICA_COUNT: Final[int] = 1
# We never actually hit this timeout under normal circumstances, so this large
# value is acceptable.
# TODO(sergiitk): reset to 10
TERMINATION_GRACE_PERIOD: Final[dt.timedelta] = dt.timedelta(minutes=3)
DRAINING_TIMEOUT: Final[dt.timedelta] = dt.timedelta(minutes=10)


class AffinitySessionDrainTest(xds_gamma_testcase.GammaXdsKubernetesTestCase):
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
        deployment_args = k8s_xds_server_runner.ServerDeploymentArgs(
            pre_stop_hook=True,
            termination_grace_period=TERMINATION_GRACE_PERIOD,
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
        return super().getClientRpcStats(
            test_client,
            num_rpcs,
            metadata_keys=metadata_keys or client_app.REQ_LB_STATS_METADATA_ALL,
        )

    def test_session_drain(self):
        test_servers: List[server_app.XdsTestServer]
        with self.subTest("01_run_test_server"):
            test_servers = self.startTestServers(replica_count=REPLICA_COUNT)

        chosen_server = test_servers[0]
        logger.info("Chosen server %s", chosen_server.hostname)

        with self.subTest("02_stopping_chosen_server"):
            self.server_runner.request_pod_deletion(
                chosen_server.hostname,
                # TODO(sergiitk): move conversion to request_pod_deletion
                grace_period_seconds=int(
                    TERMINATION_GRACE_PERIOD.total_seconds()
                ),
            )
            self.server_runner._pod_stopped_logic(chosen_server.hostname)
            # self.server_runner.cleanup()

        with self.subTest("03_wait_for_pods"):
            # self.server_runner._wait_deployment_pod_count(
            #     self.server_runner.deployment, REPLICA_COUNT
            # )
            self.server_runner.k8s_namespace.wait_for_deployment_replica_count(
                self.server_runner.deployment, REPLICA_COUNT
            )

        new_pods = self.server_runner.list_deployment_pods()

        logger.info(
            "Result pods, len %i:\n\n%s",
            len(new_pods),
            self.server_runner.k8s_namespace.pretty_format_statuses(new_pods),
        )

        # with self.subTest("02_create_ssa_policy"):
        #     self.server_runner.create_session_affinity_policy_route()
        #
        # with self.subTest("03_create_backend_policy"):
        #     self.server_runner.create_backend_policy(
        #         draining_timeout=DRAINING_TIMEOUT,
        #     )

        # Default is round-robin LB policy.

        # cookie: str
        # test_client: client_app.XdsTestClient
        # chosen_server: server_app.XdsTestServer
        #
        # with self.subTest("04_start_test_client"):
        #     test_client = self.startTestClient(test_servers[0])
        #
        # logger.info("Just testing - client %s", test_client.hostname)


if __name__ == "__main__":
    absltest.main()
