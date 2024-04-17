# Copyright 2023 gRPC authors.
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
Run xDS Test Client on Kubernetes using Gamma
"""
import datetime
import logging
from typing import Final, Optional

from typing_extensions import override

from framework.infrastructure import gcp
from framework.infrastructure import k8s
from framework.test_app.runners.k8s import k8s_base_runner
from framework.test_app.runners.k8s import k8s_xds_server_runner
from framework.test_app.server_app import XdsTestServer

logger = logging.getLogger(__name__)


ServerDeploymentArgs = k8s_xds_server_runner.ServerDeploymentArgs
KubernetesServerRunner = k8s_xds_server_runner.KubernetesServerRunner


class GammaServerRunner(KubernetesServerRunner):
    # Mutable state.
    route: Optional[k8s.GammaHttpRoute] = None
    frontend_service: Optional[k8s.V1Service] = None
    session_affinity_filter: Optional[k8s.GcpSessionAffinityFilter] = None
    session_affinity_policy: Optional[k8s.GcpSessionAffinityPolicy] = None
    backend_policy: Optional[k8s.GcpBackendPolicy] = None
    pod_monitoring: Optional[k8s.PodMonitoring] = None
    pod_monitoring_name: Optional[str] = None

    route_name: str
    frontend_service_name: str

    SESSION_AFFINITY_FILTER_NAME: Final[str] = "ssa-filter"
    SESSION_AFFINITY_POLICY_NAME: Final[str] = "ssa-policy"
    BACKEND_POLICY_NAME: Final[str] = "backend-policy"

    def __init__(
        self,
        k8s_namespace: k8s.KubernetesNamespace,
        frontend_service_name: str,
        *,
        deployment_name: str,
        image_name: str,
        td_bootstrap_image: str,
        network: str = "default",
        xds_server_uri: Optional[str] = None,
        gcp_api_manager: gcp.api.GcpApiManager,
        gcp_project: str,
        gcp_service_account: str,
        service_account_name: Optional[str] = None,
        service_name: Optional[str] = None,
        route_name: Optional[str] = None,
        neg_name: Optional[str] = None,
        deployment_template: str = "server.deployment.yaml",
        service_account_template: str = "service-account.yaml",
        service_template: str = "gamma/service.yaml",
        reuse_service: bool = False,
        reuse_namespace: bool = False,
        namespace_template: Optional[str] = None,
        debug_use_port_forwarding: bool = False,
        enable_workload_identity: bool = True,
        enable_csm_observability: bool = False,
        csm_workload_name: str = "",
        csm_canonical_service_name: str = "",
        deployment_args: Optional[ServerDeploymentArgs] = None,
    ):
        # pylint: disable=too-many-locals
        super().__init__(
            k8s_namespace,
            deployment_name=deployment_name,
            image_name=image_name,
            td_bootstrap_image=td_bootstrap_image,
            network=network,
            xds_server_uri=xds_server_uri,
            gcp_api_manager=gcp_api_manager,
            gcp_project=gcp_project,
            gcp_service_account=gcp_service_account,
            service_account_name=service_account_name,
            service_name=service_name,
            neg_name=neg_name,
            deployment_template=deployment_template,
            service_account_template=service_account_template,
            service_template=service_template,
            reuse_service=reuse_service,
            reuse_namespace=reuse_namespace,
            namespace_template=namespace_template,
            debug_use_port_forwarding=debug_use_port_forwarding,
            enable_workload_identity=enable_workload_identity,
            enable_csm_observability=enable_csm_observability,
            csm_workload_name=csm_workload_name,
            csm_canonical_service_name=csm_canonical_service_name,
            deployment_args=deployment_args,
        )

        self.frontend_service_name = frontend_service_name
        self.route_name = route_name or f"route-{deployment_name}"

    @override
    def run(  # pylint: disable=arguments-differ
        self,
        *,
        test_port: int = KubernetesServerRunner.DEFAULT_TEST_PORT,
        maintenance_port: Optional[int] = None,
        secure_mode: bool = False,
        replica_count: int = 1,
        log_to_stdout: bool = False,
        bootstrap_version: Optional[str] = None,
        route_template: str = "gamma/route_http.yaml",
        generate_mesh_id: bool = False,
    ) -> list[XdsTestServer]:
        if not maintenance_port:
            maintenance_port = self._get_default_maintenance_port(secure_mode)

        logger.info(
            (
                'Deploying GAMMA xDS test server "%s" to k8s namespace %s:'
                " test_port=%s maintenance_port=%s secure_mode=%s"
                " replica_count=%s"
            ),
            self.deployment_name,
            self.k8s_namespace.name,
            test_port,
            maintenance_port,
            False,
            replica_count,
        )
        k8s_base_runner.KubernetesBaseRunner.run(self)

        # TODO(sergiitk): move to the object config, and remove from args.
        self.log_to_stdout = log_to_stdout

        # Reuse existing if requested, create a new deployment when missing.
        # Useful for debugging to avoid NEG loosing relation to deleted service.
        if self.reuse_service:
            self.service = self._reuse_service(self.service_name)
        if not self.service:
            self.service = self._create_service(
                self.service_template,
                service_name=self.service_name,
                namespace_name=self.k8s_namespace.name,
                deployment_name=self.deployment_name,
                neg_name=self.gcp_neg_name,
                test_port=test_port,
            )

        # Create the parentref service
        self.frontend_service = self._create_service(
            "gamma/frontend_service.yaml",
            service_name=self.frontend_service_name,
            namespace_name=self.k8s_namespace.name,
        )

        # Create the route.
        self.route = self._create_gamma_route(
            route_template,
            route_name=self.route_name,
            service_name=self.service_name,
            namespace_name=self.k8s_namespace.name,
            test_port=test_port,
            frontend_service_name=self.frontend_service_name,
        )

        if self.enable_workload_identity:
            # Allow Kubernetes service account to use the GCP service account
            # identity.
            self._grant_workload_identity_user(
                gcp_iam=self.gcp_iam,
                gcp_service_account=self.gcp_service_account,
                service_account_name=self.service_account_name,
            )

            # Create service account
            self.service_account = self._create_service_account(
                self.service_account_template,
                service_account_name=self.service_account_name,
                namespace_name=self.k8s_namespace.name,
                gcp_service_account=self.gcp_service_account,
            )

        # Always create a new deployment
        self.deployment = self._create_deployment(
            self.deployment_template,
            deployment_name=self.deployment_name,
            image_name=self.image_name,
            namespace_name=self.k8s_namespace.name,
            service_account_name=self.service_account_name,
            td_bootstrap_image=self.td_bootstrap_image,
            xds_server_uri=self.xds_server_uri,
            network=self.network,
            replica_count=replica_count,
            test_port=test_port,
            maintenance_port=maintenance_port,
            secure_mode=secure_mode,
            bootstrap_version=bootstrap_version,
            enable_csm_observability=self.enable_csm_observability,
            generate_mesh_id=generate_mesh_id,
            csm_workload_name=self.csm_workload_name,
            csm_canonical_service_name=self.csm_canonical_service_name,
            **self.deployment_args.as_dict(),
        )

        # Create a PodMonitoring resource if CSM Observability is enabled
        # This is GMP (Google Managed Prometheus)
        if self.enable_csm_observability:
            self.pod_monitoring_name = f"{self.deployment_id}-gmp"
            self.pod_monitoring = self._create_pod_monitoring(
                "csm/pod-monitoring.yaml",
                namespace_name=self.k8s_namespace.name,
                deployment_id=self.deployment_id,
                pod_monitoring_name=self.pod_monitoring_name,
                pod_monitoring_port=self.DEFAULT_MONITORING_PORT,
            )

        servers = self._make_servers_for_deployment(
            replica_count,
            test_port=test_port,
            maintenance_port=maintenance_port,
            secure_mode=secure_mode,
        )

        # The controller will not populate the NEGs until there are
        # endpoint slices.
        # For this reason, we run this check after the servers were created,
        # and increased the default wait time (1m).
        self._wait_service_neg_status_annotation(
            self.service_name,
            test_port,
            timeout_sec=datetime.timedelta(minutes=4).total_seconds(),
        )

        return servers

    def create_session_affinity_policy(self, template: str, **template_vars):
        self.session_affinity_policy = self._create_session_affinity_policy(
            template,
            session_affinity_policy_name=self.SESSION_AFFINITY_POLICY_NAME,
            namespace_name=self.k8s_namespace.name,
            **template_vars,
        )

    def create_session_affinity_policy_route(self):
        self.create_session_affinity_policy(
            "gamma/session_affinity_policy_route.yaml",
            route_name=self.route_name,
        )

    def create_session_affinity_policy_service(self):
        self.create_session_affinity_policy(
            "gamma/session_affinity_policy_service.yaml",
            service_name=self.service_name,
        )

    def create_session_affinity_filter(self):
        self.session_affinity_filter = self._create_session_affinity_filter(
            "gamma/session_affinity_filter.yaml",
            session_affinity_filter_name=self.SESSION_AFFINITY_FILTER_NAME,
            namespace_name=self.k8s_namespace.name,
        )

    def create_backend_policy(
        self,
        *,
        draining_timeout: Optional[datetime.timedelta] = None,
    ):
        draining_timeout_sec: int = 0
        if draining_timeout:
            draining_timeout_sec = int(draining_timeout.total_seconds())

        self.backend_policy = self._create_backend_policy(
            "gamma/backend_policy.yaml",
            backend_policy_name=self.BACKEND_POLICY_NAME,
            namespace_name=self.k8s_namespace.name,
            service_name=self.service_name,
            draining_timeout_sec=draining_timeout_sec,
        )

    def _xds_test_server_for_pod(
        self,
        pod: k8s.V1Pod,
        *,
        test_port: int = KubernetesServerRunner.DEFAULT_TEST_PORT,
        maintenance_port: Optional[int] = None,
        secure_mode: bool = False,
        monitoring_port: Optional[int] = None,
    ) -> XdsTestServer:
        if self.enable_csm_observability:
            if self.debug_use_port_forwarding:
                pf = self._start_port_forwarding_pod(
                    pod, self.DEFAULT_MONITORING_PORT
                )
                monitoring_port = pf.local_port
            else:
                monitoring_port = self.DEFAULT_MONITORING_PORT

        return super()._xds_test_server_for_pod(
            pod=pod,
            test_port=test_port,
            maintenance_port=maintenance_port,
            secure_mode=secure_mode,
            monitoring_port=monitoring_port,
        )

    @override
    def cleanup(self, *, force=False, force_namespace=False):
        try:
            if self.route or force:
                self._delete_gamma_route(self.route_name)
                self.route = None

            if self.frontend_service or force:
                self._delete_service(self.frontend_service_name)
                self.frontend_service = None

            if (self.service and not self.reuse_service) or force:
                self._delete_service(self.service_name)
                self.service = None

            if self.deployment or force:
                self._delete_deployment(self.deployment_name)
                self.deployment = None

            if self.session_affinity_policy or force:
                self._delete_session_affinity_policy(
                    self.SESSION_AFFINITY_POLICY_NAME
                )
                self.session_affinity_policy = None

            if self.session_affinity_filter or force:
                self._delete_session_affinity_filter(
                    self.SESSION_AFFINITY_FILTER_NAME
                )
                self.session_affinity_filter = None

            if self.backend_policy or force:
                self._delete_backend_policy(self.BACKEND_POLICY_NAME)
                self.backend_policy = None

            if self.enable_workload_identity and (
                self.service_account or force
            ):
                self._revoke_workload_identity_user(
                    gcp_iam=self.gcp_iam,
                    gcp_service_account=self.gcp_service_account,
                    service_account_name=self.service_account_name,
                )
                self._delete_service_account(self.service_account_name)
                self.service_account = None

            # Pod monitoring name is only set when CSM observability is enabled.
            if self.pod_monitoring_name and (self.pod_monitoring or force):
                self._delete_pod_monitoring(self.pod_monitoring_name)
                self.pod_monitoring = None
                self.pod_monitoring_name = None

            self._cleanup_namespace(force=(force_namespace and force))
        finally:
            self._stop()
