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
import datetime as dt
import logging
from typing import List, Optional

from typing_extensions import override

from framework.infrastructure import gcp
from framework.infrastructure import k8s
from framework.rpc import grpc
from framework.test_app.runners.k8s import k8s_base_runner
from framework.test_app.server_app import XdsTestServer

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ServerDeploymentArgs:
    pre_stop_hook: bool = False
    termination_grace_period: dt.timedelta = dt.timedelta()
    enable_csm_observability: bool = False
    csm_workload_name: str = ""
    csm_canonical_service_name: str = ""
    enable_dualstack: bool = False
    enable_rlqs: bool = False

    def as_dict(self):
        return {
            "pre_stop_hook": self.pre_stop_hook,
            "termination_grace_period_seconds": int(
                self.termination_grace_period.total_seconds()
            ),
            "enable_csm_observability": self.enable_csm_observability,
            "csm_workload_name": self.csm_workload_name,
            "csm_canonical_service_name": self.csm_canonical_service_name,
            "enable_dualstack": self.enable_dualstack,
            "enable_rlqs": self.enable_rlqs,
        }


class KubernetesServerRunner(k8s_base_runner.KubernetesBaseRunner):
    DEFAULT_TEST_PORT = 8080
    DEFAULT_MAINTENANCE_PORT = 8080
    DEFAULT_SECURE_MODE_MAINTENANCE_PORT = 8081

    # Required fields.
    deployment_template: str
    service_name: str
    service_template: str
    reuse_service: bool
    enable_workload_identity: bool
    debug_use_port_forwarding: bool
    gcp_neg_name: str
    td_bootstrap_image: str
    xds_server_uri: str
    network: str

    # Server Deployment args
    deployment_args: ServerDeploymentArgs

    # Optional fields.
    service_account_name: Optional[str] = None
    service_account_template: Optional[str] = None
    gcp_iam: Optional[gcp.iam.IamV1] = None

    # Below is mutable state associated with the current run.
    service: Optional[k8s.V1Service] = None
    replica_count: int = 0
    pod_monitoring: Optional[k8s.PodMonitoring] = None
    pod_monitoring_name: Optional[str] = None

    # A map from pod names to the server app.
    pods_to_servers: dict[str, XdsTestServer]

    def __init__(  # pylint: disable=too-many-locals
        self,
        k8s_namespace: k8s.KubernetesNamespace,
        *,
        deployment_name: str,
        image_name: str,
        td_bootstrap_image: str,
        app_label: str = "",
        network: str = "default",
        xds_server_uri: Optional[str] = None,
        gcp_api_manager: gcp.api.GcpApiManager,
        gcp_project: str,
        gcp_service_account: str,
        service_account_name: Optional[str] = None,
        service_name: Optional[str] = None,
        neg_name: Optional[str] = None,
        deployment_template: str = "server.deployment.yaml",
        service_account_template: str = "service-account.yaml",
        service_template: str = "server.service.yaml",
        reuse_service: bool = False,
        reuse_namespace: bool = False,
        namespace_template: Optional[str] = None,
        debug_use_port_forwarding: bool = False,
        enable_workload_identity: bool = True,
        deployment_args: Optional[ServerDeploymentArgs] = None,
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
        self.deployment_template = deployment_template
        self.service_name = service_name or deployment_name
        self.service_template = service_template
        self.reuse_service = reuse_service
        self.enable_workload_identity = enable_workload_identity
        self.debug_use_port_forwarding = debug_use_port_forwarding

        # Server deployment arguments.
        if not deployment_args:
            deployment_args = ServerDeploymentArgs()
        self.deployment_args = deployment_args

        # GCP Network Endpoint Group.
        self.gcp_neg_name = neg_name or (
            f"{self.k8s_namespace.name}-{self.service_name}"
        )

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

        # Mutable state associated with each run.
        self._reset_state()

    @override
    def _reset_state(self):
        super()._reset_state()
        self.service = None
        self.pods_to_servers = {}
        self.replica_count = 0

    def run(  # pylint: disable=arguments-differ,too-many-branches
        self,
        *,
        test_port: int = DEFAULT_TEST_PORT,
        maintenance_port: Optional[int] = None,
        secure_mode: bool = False,
        address_type: str = "",
        replica_count: int = 1,
        log_to_stdout: bool = False,
        bootstrap_version: Optional[str] = None,
        config_mesh: Optional[str] = None,
    ) -> List[XdsTestServer]:
        if not maintenance_port:
            maintenance_port = self._get_default_maintenance_port(secure_mode)

        # Implementation detail: in secure mode, maintenance ("backchannel")
        # port must be different from the test port so communication with
        # maintenance services can be reached independently of the security
        # configuration under test.
        if secure_mode and maintenance_port == test_port:
            raise ValueError(
                "port and maintenance_port must be different "
                "when running test server in secure mode"
            )
        # To avoid bugs with comparing wrong types.
        if not (
            isinstance(test_port, int) and isinstance(maintenance_port, int)
        ):
            raise TypeError("Port numbers must be integer")

        if secure_mode and not self.enable_workload_identity:
            raise ValueError("Secure mode requires Workload Identity enabled.")

        logger.info(
            (
                'Deploying xDS test server "%s" to k8s namespace %s:'
                " test_port=%s maintenance_port=%s secure_mode=%s"
                " replica_count=%s"
            ),
            self.deployment_name,
            self.k8s_namespace.name,
            test_port,
            maintenance_port,
            secure_mode,
            replica_count,
        )
        super().run()

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
                app_label=self.app_label,
                neg_name=self.gcp_neg_name,
                test_port=test_port,
                enable_dualstack=self.deployment_args.enable_dualstack,
            )
        self._wait_service_neg_status_annotation(self.service_name, test_port)

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
            replica_count=replica_count,
            test_port=test_port,
            maintenance_port=maintenance_port,
            secure_mode=secure_mode,
            address_type=address_type,
            bootstrap_version=bootstrap_version,
            config_mesh=config_mesh,
            **self.deployment_args.as_dict(),
        )

        self._setup_csm_observability()

        return self._make_servers_for_deployment(
            replica_count,
            test_port=test_port,
            maintenance_port=maintenance_port,
            secure_mode=secure_mode,
        )

    def _setup_csm_observability(self) -> None:
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

    def _make_servers_for_deployment(
        self,
        replica_count,
        *,
        test_port: int,
        maintenance_port: int,
        secure_mode: bool = False,
    ) -> List[XdsTestServer]:
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
        # TODO(sergiitk): move to super()._start_completed
        self.replica_count = replica_count

        servers: List[XdsTestServer] = []
        for pod in self.pods_started.values():
            servers.append(
                self._xds_test_server_for_pod(
                    pod,
                    test_port=test_port,
                    maintenance_port=maintenance_port,
                    secure_mode=secure_mode,
                )
            )
        return servers

    def _get_default_maintenance_port(self, secure_mode: bool) -> int:
        if not secure_mode:
            maintenance_port = self.DEFAULT_MAINTENANCE_PORT
        else:
            maintenance_port = self.DEFAULT_SECURE_MODE_MAINTENANCE_PORT
        return maintenance_port

    def _xds_test_server_for_pod(
        self,
        pod: k8s.V1Pod,
        *,
        test_port: int = DEFAULT_TEST_PORT,
        maintenance_port: Optional[int] = None,
        secure_mode: bool = False,
        monitoring_port: Optional[int] = None,
    ) -> XdsTestServer:
        if maintenance_port is None:
            maintenance_port = self._get_default_maintenance_port(secure_mode)

        if self.debug_use_port_forwarding:
            pf = self._start_port_forwarding_pod(pod, maintenance_port)
            rpc_port, rpc_host = pf.local_port, pf.local_address
            if self.should_collect_logs_prometheus:
                pf = self._start_port_forwarding_pod(
                    pod, self.DEFAULT_POD_MONITORING_PORT
                )
                monitoring_port = pf.local_port
        else:
            rpc_port, rpc_host = maintenance_port, None
            if self.should_collect_logs_prometheus:
                monitoring_port = self.DEFAULT_POD_MONITORING_PORT

        server = XdsTestServer(
            ip=pod.status.pod_ip,
            rpc_port=test_port,
            hostname=pod.metadata.name,
            maintenance_port=rpc_port,
            secure_mode=secure_mode,
            rpc_host=rpc_host,
            monitoring_port=monitoring_port,
        )
        self.pods_to_servers[pod.metadata.name] = server
        return server

    @property
    def should_collect_logs_prometheus(self):
        return (
            self.deployment_args.enable_csm_observability
            and self.should_collect_logs
        )

    @override
    def stop_pod_dependencies(self, *, log_drain_sec: int = 0):
        # Call pre-stop hook release if exists.
        if (
            self.deployment_args.pre_stop_hook
            and self.pods_to_servers
            and self.k8s_namespace
        ):
            self._log(
                "Releasing pre-stop hook on known server pods of deployment %s,"
                " deployment_id=%s",
                self.deployment_name,
                self.deployment_id,
            )
            # Handle "stopping" pods separately, as they may already be deleted.
            for pod_name in self.pods_stopping:
                try:
                    # Shorter timeout.
                    self.pods_to_servers[pod_name].release_prestop_hook(
                        timeout=dt.timedelta(seconds=5),
                    )
                except grpc.RpcError:
                    self._log(
                        "Pre-stop hook release not executed on pod marked"
                        " for deletion: %s (this is normal)",
                        pod_name,
                    )
                except KeyError:
                    logger.warning(
                        "Failed release pre-stop hook: server app not found"
                        " for pod %s",
                        pod_name,
                    )

            # Handle "started" pods.
            for pod_name in self.pods_started:
                try:
                    self.pods_to_servers[pod_name].release_prestop_hook()
                except (KeyError, grpc.RpcError) as err:
                    logger.warning(
                        "Prestop hook release to %s failed: %r", pod_name, err
                    )

        super().stop_pod_dependencies(log_drain_sec=log_drain_sec)

    # pylint: disable=arguments-differ
    def cleanup(self, *, force=False, force_namespace=False):
        # TODO(sergiitk): rename to stop().
        try:
            if self.deployment or force:
                self._delete_deployment(self.deployment_name)
                self.deployment = None
            if (self.service and not self.reuse_service) or force:
                self._delete_service(self.service_name)
                self.service = None
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

    # pylint: enable=arguments-differ

    @classmethod
    def make_namespace_name(
        cls, resource_prefix: str, resource_suffix: str, name: str = "server"
    ) -> str:
        """A helper to make consistent XdsTestServer kubernetes namespace name
        for given resource prefix and suffix.

        Note: the idea is to intentionally produce different namespace name for
        the test server, and the test client, as that closely mimics real-world
        deployments.
        """
        return cls._make_namespace_name(resource_prefix, resource_suffix, name)
