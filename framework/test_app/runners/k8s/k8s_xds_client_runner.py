# Copyright 2022 gRPC authors.
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
Run xDS Test Client on Kubernetes.
"""
import dataclasses
import logging
from typing import Optional

from framework.infrastructure import gcp
from framework.infrastructure import k8s
from framework.test_app import client_app
from framework.test_app.runners.k8s import k8s_base_runner

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ClientDeploymentArgs:
    enable_csm_observability: bool = False
    csm_workload_name: str = ""
    csm_canonical_service_name: str = ""
    enable_dualstack: bool = False
    is_trusted_xds_server_experimental: bool = False
    enable_xds_federation: bool = True

    def as_dict(self):
        return dataclasses.asdict(self)


class KubernetesClientRunner(k8s_base_runner.KubernetesBaseRunner):
    # Required fields.
    xds_server_uri: str
    stats_port: int
    deployment_template: str
    enable_workload_identity: bool
    debug_use_port_forwarding: bool
    td_bootstrap_image: str
    network: str

    # Client Deployment args
    deployment_args: ClientDeploymentArgs

    # Optional fields.
    service_account_name: Optional[str] = None
    service_account_template: Optional[str] = None
    gcp_iam: Optional[gcp.iam.IamV1] = None
    pod_monitoring: Optional[k8s.PodMonitoring] = None
    pod_monitoring_name: Optional[str] = None

    def __init__(  # pylint: disable=too-many-locals
        self,
        k8s_namespace: k8s.KubernetesNamespace,
        *,
        deployment_name: str,
        image_name: str,
        td_bootstrap_image: str,
        app_label: str = "",
        network="default",
        xds_server_uri: Optional[str] = None,
        gcp_api_manager: gcp.api.GcpApiManager,
        gcp_project: str,
        gcp_service_account: str,
        service_account_name: Optional[str] = None,
        stats_port: int = 8079,
        deployment_template: str = "client.deployment.yaml",
        service_account_template: str = "service-account.yaml",
        reuse_namespace: bool = False,
        namespace_template: Optional[str] = None,
        debug_use_port_forwarding: bool = False,
        enable_workload_identity: bool = True,
        deployment_args: Optional[ClientDeploymentArgs] = None,
    ):
        super().__init__(
            k8s_namespace,
            deployment_name=deployment_name,
            image_name=image_name,
            gcp_project=gcp_project,
            app_label=app_label,
            gcp_service_account=gcp_service_account,
            gcp_ui_url=gcp_api_manager.gcp_ui_url,
            namespace_template=namespace_template,
            reuse_namespace=reuse_namespace,
        )

        # Settings
        self.stats_port = stats_port
        self.deployment_template = deployment_template
        self.enable_workload_identity = enable_workload_identity
        self.debug_use_port_forwarding = debug_use_port_forwarding

        # Client deployment arguments.
        if not deployment_args:
            deployment_args = ClientDeploymentArgs()
        self.deployment_args = deployment_args

        # Used by the TD bootstrap generator.
        self.td_bootstrap_image = td_bootstrap_image
        self.network = network
        self.xds_server_uri = xds_server_uri

        # Workload identity settings:
        if self.enable_workload_identity:
            # Kubernetes service account.
            self.service_account_name = service_account_name or deployment_name
            self.service_account_template = service_account_template
            # GCP IAM API used to grant allow workload service accounts
            # permission to use GCP service account identity.
            self.gcp_iam = gcp.iam.IamV1(gcp_api_manager, gcp_project)

    def run(  # pylint: disable=arguments-differ
        self,
        *,
        server_target,
        rpc="UnaryCall",
        qps=25,
        metadata="",
        secure_mode=False,
        config_mesh=None,
        generate_mesh_id=False,
        print_response=False,
        log_to_stdout: bool = False,
        request_payload_size: int = 0,
        response_payload_size: int = 0,
    ) -> client_app.XdsTestClient:
        logger.info(
            (
                'Deploying xDS test client "%s" to k8s namespace %s: '
                "server_target=%s rpc=%s qps=%s metadata=%r secure_mode=%s "
                "print_response=%s"
            ),
            self.deployment_name,
            self.k8s_namespace.name,
            server_target,
            rpc,
            qps,
            metadata,
            secure_mode,
            print_response,
        )
        super().run()

        # TODO(sergiitk): move to the object config, and remove from args.
        self.log_to_stdout = log_to_stdout

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
            app_label=self.app_label,
            service_account_name=self.service_account_name,
            td_bootstrap_image=self.td_bootstrap_image,
            xds_server_uri=self.xds_server_uri,
            network=self.network,
            stats_port=self.stats_port,
            server_target=server_target,
            rpc=rpc,
            qps=qps,
            request_payload_size=request_payload_size,
            response_payload_size=response_payload_size,
            metadata=metadata,
            secure_mode=secure_mode,
            config_mesh=config_mesh,
            generate_mesh_id=generate_mesh_id,
            print_response=print_response,
            **self.deployment_args.as_dict(),
        )

        # Create a PodMonitoring resource if CSM Observability is enabled
        # This is GMP (Google Managed Prometheus)
        if self.deployment_args.enable_csm_observability:
            self.pod_monitoring_name = f"{self.deployment_id}-gmp"
            self.pod_monitoring = self._create_pod_monitoring(
                "csm/pod-monitoring.yaml",
                namespace_name=self.k8s_namespace.name,
                deployment_id=self.deployment_id,
                pod_monitoring_name=self.pod_monitoring_name,
                pod_monitoring_port=self.DEFAULT_POD_MONITORING_PORT,
            )

        # We don't support for multiple client replicas at the moment.
        return self._make_clients_for_deployment(server_target=server_target)[0]

    def _make_clients_for_deployment(
        self, replica_count: int = 1, *, server_target: str
    ) -> list[client_app.XdsTestClient]:
        pod_names = self._wait_deployment_pod_count(
            self.deployment, replica_count
        )

        for pod_name in pod_names:
            pod = self._wait_pod_started(pod_name)
            self._pod_started_logic(pod)

        # Verify the deployment reports all pods started as well.
        self._wait_deployment_with_available_replicas(
            self.deployment_name, replica_count
        )
        self._start_completed()

        return [
            self._xds_test_client_for_pod(pod, server_target=server_target)
            for pod in self.pods_started.values()
        ]

    def _xds_test_client_for_pod(
        self, pod: k8s.V1Pod, *, server_target: str
    ) -> client_app.XdsTestClient:
        monitoring_port = None
        if self.debug_use_port_forwarding:
            pf = self._start_port_forwarding_pod(pod, self.stats_port)
            rpc_port, rpc_host = pf.local_port, pf.local_address
            if self.deployment_args.enable_csm_observability:
                pf = self._start_port_forwarding_pod(
                    pod, self.DEFAULT_POD_MONITORING_PORT
                )
                monitoring_port = pf.local_port
        else:
            rpc_port, rpc_host = self.stats_port, None
            if self.deployment_args.enable_csm_observability:
                monitoring_port = self.DEFAULT_POD_MONITORING_PORT

        return client_app.XdsTestClient(
            ip=pod.status.pod_ip,
            rpc_port=rpc_port,
            server_target=server_target,
            hostname=pod.metadata.name,
            rpc_host=rpc_host,
            monitoring_port=monitoring_port,
        )

    # pylint: disable=arguments-differ
    def cleanup(self, *, force=False, force_namespace=False):
        # TODO(sergiitk): rename to stop().
        try:
            if self.deployment or force:
                self._delete_deployment(self.deployment_name)
                self.deployment = None
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
            self._cleanup_namespace(force=force_namespace and force)
        finally:
            self._stop()

    # pylint: enable=arguments-differ

    @classmethod
    def make_namespace_name(
        cls, resource_prefix: str, resource_suffix: str, name: str = "client"
    ) -> str:
        """A helper to make consistent XdsTestClient kubernetes namespace name
        for given resource prefix and suffix.

        Note: the idea is to intentionally produce different namespace name for
        the test server, and the test client, as that closely mimics real-world
        deployments.
        """
        return cls._make_namespace_name(resource_prefix, resource_suffix, name)
