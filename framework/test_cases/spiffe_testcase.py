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

from typing_extensions import Final

from framework import xds_flags
from framework import xds_k8s_testcase
from framework.helpers import retryers
from framework.infrastructure import traffic_director
import framework.infrastructure.mesh_resource_manager.spiffe_mesh_manager as td_spiffe
from framework.test_app import client_app
from framework.test_app import server_app
from framework.test_app.runners.cloud_run import cloud_run_xds_client_runner
from framework.test_app.runners.k8s import k8s_xds_server_runner
from framework.test_cases import cloud_run_testcase

logger = logging.getLogger(__name__)

# Type aliases
TrafficDirectorManager = traffic_director.TrafficDirectorManager
SpiffeMeshManager = td_spiffe.SpiffeMeshManager
KubernetesServerRunner = k8s_xds_server_runner.KubernetesServerRunner
CloudRunClientRunner = cloud_run_xds_client_runner.CloudRunClientRunner
XdsTestServer = server_app.XdsTestServer
XdsTestClient = client_app.XdsTestClient
_SecurityMode = xds_k8s_testcase.SecurityXdsKubernetesTestCase.SecurityMode

TD_CONFIG_MAX_WAIT: Final[dt.timedelta] = dt.timedelta(minutes=10)


class SpiffeMtlsXdsKubernetesCloudRunTestCase(
    cloud_run_testcase.CloudRunXdsTestCase
):
    cr_workload_identity_pool: str
    mwid_namespace_name: str
    managed_identity_id: str

    td: SpiffeMeshManager
    server_runner: KubernetesServerRunner

    @classmethod
    def setUpClass(cls):
        """Hook method for setting up class fixture before running tests in
        the class.
        """
        super().setUpClass()
        cls.workload_identity_pool = xds_flags.WORKLOAD_IDENTITY.value
        cls.mwid_namespace_name = xds_flags.MANAGED_IDENTITY_NAMESPACE.value
        cls.managed_identity_id = xds_flags.MANAGED_IDENTITY.value

    def initTrafficDirectorManager(self) -> TrafficDirectorManager:
        return SpiffeMeshManager(
            self.gcp_api_manager,
            project=self.project,
            resource_prefix=self.resource_prefix,
            resource_suffix=self.resource_suffix,
            network=self.network,
            compute_api_version=self.compute_api_version,
            enable_dualstack=self.enable_dualstack,
        )

    def startCloudRunTestClient(
        self, test_server: XdsTestServer, *, enable_spiffe: bool = False
    ) -> XdsTestClient:
        self.client_runner = CloudRunClientRunner(
            project=self.project,
            project_number=self.project_number,
            service_name=self.client_namespace,
            image_name=self.client_image,
            network=self.network,
            region=self.region,
            gcp_api_manager=self.gcp_api_manager,
            stats_port=self.client_port,
            workload_identity_pool=self.workload_identity_pool,
            namespace=self.mwid_namespace_name,
            managed_identity=self.managed_identity_id,
            enable_spiffe=enable_spiffe,
        )
        test_client = self.client_runner.run(
            server_target=test_server.xds_uri,
            mesh_name=self.td.mesh.url,
        )
        return test_client

    def assertTestAppSecurityWithRetry(
        self,
        mode: _SecurityMode,
        test_client: XdsTestClient,
        test_server: XdsTestServer,
        secure_channel: bool = False,
        match_only_port: bool = False,
        *,
        retry_timeout: dt.timedelta = TD_CONFIG_MAX_WAIT,
        retry_wait: dt.timedelta = dt.timedelta(seconds=10),
    ):
        retryer = retryers.constant_retryer(
            wait_fixed=retry_wait,
            timeout=retry_timeout,
            log_level=logging.INFO,
            error_note=(
                f"Could not find correct security"
                f" before timeout {retry_timeout} (h:mm:ss)"
            ),
        )
        retryer(
            self.assertTestAppSecurity,
            mode,
            test_client,
            test_server,
            secure_channel=secure_channel,
            match_only_port=match_only_port,
        )
