# Copyright 2025 gRPC authors.
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
from typing import Any, List, Optional

from framework import xds_flags
from framework import xds_k8s_testcase
from framework.helpers import retryers
from framework.infrastructure import k8s
from framework.infrastructure import traffic_director
from framework.infrastructure.gcp.compute import ComputeV1
import framework.infrastructure.traffic_director_cloud_run as td_cloud_run
from framework.test_app import server_app
from framework.test_app.runners.cloud_run import cloud_run_xds_server_runner
from framework.test_app.runners.k8s import k8s_xds_client_runner

logger = logging.getLogger(__name__)

# Type aliases
TrafficDirectorManager = traffic_director.TrafficDirectorManager
CloudRunServerRunner = cloud_run_xds_server_runner.CloudRunServerRunner
TrafficDirectorCloudRunManager = td_cloud_run.TrafficDirectorCloudRunManager
KubernetesClientRunner = k8s_xds_client_runner.KubernetesClientRunner
XdsTestServer = server_app.XdsTestServer


class CloudRunXdsKubernetesTestCase(
    xds_k8s_testcase.SecurityXdsKubernetesTestCase
):
    server_runner: CloudRunServerRunner
    td: TrafficDirectorCloudRunManager
    neg: Any

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compute = cls.gcp_api_manager.compute(cls.compute_api_version)
        cls.compute_v1 = ComputeV1(
            cls.gcp_api_manager, cls.project, version=cls.compute_api_version
        )
        cls.region = xds_flags.CLOUD_RUN_REGION.value

    def initTrafficDirectorManager(self) -> TrafficDirectorManager:
        return TrafficDirectorCloudRunManager(
            self.gcp_api_manager,
            project=self.project,
            resource_prefix=self.resource_prefix,
            resource_suffix=self.resource_suffix,
            network=self.network,
            compute_api_version=self.compute_api_version,
            enable_dualstack=self.enable_dualstack,
        )

    def initKubernetesClientRunner(self, **kwargs) -> KubernetesClientRunner:
        return KubernetesClientRunner(
            k8s.KubernetesNamespace(
                self.k8s_api_manager, self.client_namespace
            ),
            deployment_name=self.client_name,
            image_name=self.client_image,
            td_bootstrap_image=self.td_bootstrap_image,
            gcp_project=self.project,
            gcp_api_manager=self.gcp_api_manager,
            gcp_service_account=self.gcp_service_account,
            xds_server_uri=self.xds_server_uri,
            network=self.network,
            debug_use_port_forwarding=self.debug_use_port_forwarding,
            enable_workload_identity=self.enable_workload_identity,
            deployment_template="client.deployment.yaml",
            stats_port=self.client_port,
            reuse_namespace=self.server_namespace == self.client_namespace,
            **kwargs,
        )

    def setupServerBackends(
        self,
        *,
        wait_for_healthy_status=True,
        server_runner=None,
        max_rate_per_endpoint: Optional[int] = None,
    ):
        if server_runner is None:
            server_runner = self.server_runner
        service_url = server_runner.get_service_url()
        self.td.backend_service_add_backends(
            [service_url], max_rate_per_endpoint=max_rate_per_endpoint
        )
        if wait_for_healthy_status:
            self.td.wait_for_backends_healthy_status(
                replica_count=server_runner.replica_count
            )

    def startTestServers(self, server_runner=None) -> List[XdsTestServer]:
        if server_runner is None:
            self.server_runner = CloudRunServerRunner(
                project=self.project,
                service_name=self.server_namespace,
                image_name=self.server_image,
                network=self.network,
                region=self.region,
                gcp_api_manager=self.gcp_api_manager,
            )
        test_servers = self.server_runner.run()
        for test_server in test_servers:
            test_server.set_xds_address(
                self.server_xds_host, self.server_xds_port
            )
        return test_servers

    def assertEDSConfigExists(self, config):
        """No-op for Cloud Run as EDS is not required."""
        _ = config

    def cleanup(self):
        self.server_runner.cleanup(force=self.force_cleanup)
        self.td.cleanup(region=self.region, force=self.force_cleanup)
        self.client_runner.cleanup(
            force=self.force_cleanup, force_namespace=self.force_cleanup
        )

    def tearDown(self):
        logger.info("----- TestMethod %s teardown -----", self.test_name)
        logger.debug("Getting pods restart times")
        client_restarts: int = 0
        try:
            client_restarts = self.client_runner.get_pod_restarts(
                self.client_runner.deployment
            )
        except (retryers.RetryError, k8s.NotFound) as e:
            logger.exception(e)

        retryer = retryers.constant_retryer(
            wait_fixed=dt.timedelta(seconds=10),
            attempts=3,
            log_level=logging.INFO,
        )
        try:
            retryer(self.cleanup)
        except retryers.RetryError:
            logger.exception("Got error during teardown")
        finally:
            logger.info("----- Test client/server logs -----")
            self.client_runner.logs_explorer_run_history_links()
            self.server_runner.logs_explorer_run_history_links()

            # Fail if any of the pods restarted.
            self.assertEqual(
                client_restarts,
                0,
                msg=(
                    "Client container unexpectedly restarted"
                    f" {client_restarts} times during test. In most cases, this"
                    " is caused by the test client app crash."
                ),
            )
