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
"""
Run xDS Test Client on Cloud Run.
"""
import logging
from typing import Final, Optional

from typing_extensions import override

from framework.infrastructure import gcp
from framework.test_app import client_app
from framework.test_app.runners.cloud_run import cloud_run_base_runner

logger = logging.getLogger(__name__)

DEFAULT_PORT: Final[int] = 443
WORKLOAD_IDENTITY_POOL: Final[str] = "psm-interop-cloudrun-wip-cr"
NAMESPACE: Final[str] = "psm-interop-cloudrun-wip-cr"
MANAGED_IDENTITY: Final[str] = "psm-interop-cloudrun-wip-cr-mwid"


class CloudRunClientRunner(cloud_run_base_runner.CloudRunBaseRunner):
    """Manages xDS Test Clients running on Cloud Run."""

    mesh_name: str
    server_target: str
    stats_port: int

    gcp_iam: Optional[gcp.iam.IamV1] = None
    service: Optional[gcp.cloud_run.CloudRunService] = None
    current_revision: Optional[str] = None

    def __init__(
        self,
        project: str,
        project_number: str,
        service_name: str,
        image_name: str,
        network: str,
        region: str,
        gcp_api_manager: gcp.api.GcpApiManager,
        stats_port: int = 8079,
    ):
        super().__init__(
            project,
            service_name,
            image_name,
            network=network,
            region=region,
            gcp_api_manager=gcp_api_manager,
        )
        self.stats_port = stats_port

        self.project_number = project_number
        self.gcp_iam = gcp.iam.IamV1(gcp_api_manager, project)
        self._initalize_cloud_run()

        # Mutable state associated with each run.
        self._reset_state()

    @override
    def _reset_state(self):
        super()._reset_state()
        self.service = None
        self.current_revision = None

    @override
    def run(  # pylint: disable=arguments-differ
        self,
        *,
        server_target: str,
        mesh_name: str,
        enable_spiffe: bool = False,
    ) -> client_app.XdsTestClient:
        """Deploys and manages the xDS Test Client on Cloud Run."""
        super().run()

        if enable_spiffe:
            self.add_attestation_policy(self.service_name)
        logger.info(
            "Starting cloud run client with service %s and image %s and server target %s",
            self.service_name,
            self.image_name,
            server_target,
        )
        self.service = self.deploy_service(
            service_name=self.service_name,
            image_name=self.image_name,
            mesh_name=mesh_name,
            server_target=server_target,
            enable_spiffe=enable_spiffe,
            stats_port=self.stats_port,
        )
        self.current_revision = self.service.revision
        client = self._make_client_from_service(server_target, self.service)
        self._start_completed()
        return client

    def reuse_from_service(
        self,
        *,
        server_target: str,
    ) -> client_app.XdsTestClient:
        if not self.service:
            self.service = self.cloud_run.get_service(self.service_name)

        return self._make_client_from_service(server_target, self.service)

    @classmethod
    def _make_client_from_service(
        cls,
        server_target: str,
        service: gcp.cloud_run.CloudRunService,
    ) -> client_app.XdsTestClient:
        service_hostname = service.uri.removeprefix("https://")
        return client_app.XdsTestClient(
            ip="0.0.0.0",
            rpc_port=DEFAULT_PORT,
            rpc_host=service_hostname,
            server_target=server_target,
            hostname=service_hostname,
        )

    def add_attestation_policy(self, service_name: str):
        body = {
            "attestationRule": {
                "googleCloudResource": (
                    f"//run.googleapis.com/projects/"
                    f"{self.project_number}/name/locations/{self.region}/services"
                    f"/{service_name}"
                )
            }
        }
        self.gcp_iam.add_attestation_rule(
            WORKLOAD_IDENTITY_POOL,
            NAMESPACE,
            MANAGED_IDENTITY,
            body,
        )

    def deploy_service(
        self,
        *,
        service_name: str,
        image_name: str,
        mesh_name: str,
        server_target: str,
        enable_spiffe: bool = False,
        stats_port: int,
    ) -> gcp.cloud_run.CloudRunService:
        if not service_name:
            raise ValueError("service_name cannot be empty or None")
        if not image_name:
            raise ValueError("image_name cannot be empty or None")

        service_body = {
            "launch_stage": "alpha",
            "template": {
                "containers": [
                    {
                        "image": image_name,
                        "ports": [
                            {
                                "containerPort": stats_port,
                                "name": "h2c",
                            }
                        ],
                        "args": [
                            f"--server={server_target}",
                            "--secure_mode=true",
                            f"--stats_port={stats_port}",
                        ],
                        "env": [
                            {
                                "name": "GRPC_EXPERIMENTAL_XDS_AUTHORITY_REWRITE",
                                "value": "true",
                            },
                            {
                                "name": "GRPC_EXPERIMENTAL_XDS_SYSTEM_ROOT_CERTS",
                                "value": "true",
                            },
                            {
                                "name": "GRPC_EXPERIMENTAL_XDS_GCP_AUTHENTICATION_FILTER",
                                "value": "true",
                            },
                        ],
                    }
                ],
                "service_mesh": {
                    "mesh": mesh_name,
                    "dataplaneMode": "PROXYLESS_GRPC",
                },
            },
        }
        logger.info("Deploying Cloud Run service '%s'", service_name)
        if enable_spiffe:
            service_body["template"]["containers"][0]["env"].extend(
                [
                    # TODO: Remove this when environment variable is changed in JAVA.
                    {
                        "name": "GRPC_EXPERIMENTAL_SPIFFE_TRUST_BUNDLE_MAP",
                        "value": "true",
                    },
                    {
                        "name": "GRPC_EXPERIMENTAL_XDS_MTLS_SPIFFE",
                        "value": "true",
                    },
                ]
            )
            service_body["template"]["workload_certificates"] = {
                "enableWorkloadCertificate": "true"
            }
            service_body["template"]["identity"] = (
                f"//{WORKLOAD_IDENTITY_POOL}.global.{self.project_number}."
                f"workload.id.goog/ns/{NAMESPACE}/sa/{MANAGED_IDENTITY}"
            )
            service_body["template"]["vpc_access"] = {
                "network_interfaces": {"network": "default-vpc"}
            }
        self.cloud_run.create_service(service_name, service_body)
        return self.cloud_run.get_service(service_name)

    @override
    def cleanup(self, *, force=False):
        # TODO(emchandwani) : Collect service logs in a file.
        try:
            super().cleanup(force=force)
            self._reset_state()
        finally:
            self._stop()
