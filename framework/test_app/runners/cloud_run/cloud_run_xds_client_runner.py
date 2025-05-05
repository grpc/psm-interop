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

DEFAULT_CLIENT_TEST_PORT: Final[int] = 50052  # This is the stats service port.
DEFAULT_PORT: Final[int] = 443


class CloudRunClientRunner(cloud_run_base_runner.CloudRunBaseRunner):
    """Manages xDS Test Clients running on Cloud Run."""

    mesh_name: str
    server_target: str

    service: Optional[gcp.cloud_run.CloudRunService] = None
    current_revision: Optional[str] = None

    def __init__(
        self,
        project: str,
        service_name: str,
        image_name: str,
        network: str,
        region: str,
        gcp_api_manager: gcp.api.GcpApiManager,
    ):
        super().__init__(
            project,
            service_name,
            image_name,
            network=network,
            region=region,
            gcp_api_manager=gcp_api_manager,
        )

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
        self, *, server_target: str, mesh_name: str
    ) -> client_app.XdsTestClient:
        """Deploys and manages the xDS Test Client on Cloud Run."""
        logger.info(
            "Starting cloud run client with service %s and image %s and server target %s",
            self.service_name,
            self.image_name,
            server_target,
        )

        super().run()
        self.service = self.deploy_service(
            service_name=self.service_name,
            image_name=self.image_name,
            mesh_name=mesh_name,
            server_target=server_target,
        )
        self.current_revision = self.service.uri
        client_uri = self.service.uri.removeprefix("https://")
        client = client_app.XdsTestClient(
            ip="0.0.0.0",
            rpc_port=DEFAULT_PORT,
            rpc_host=client_uri,
            server_target=server_target,
            hostname=self.current_revision,
            maintenance_port=DEFAULT_PORT,
        )
        self._start_completed()
        return client

    def deploy_service(
        self,
        service_name: str,
        image_name: str,
        mesh_name: str,
        server_target: str,
        *,
        test_port: int = DEFAULT_CLIENT_TEST_PORT,
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
                                "containerPort": test_port,
                                "name": "h2c",
                            }
                        ],
                        "args": [
                            f"--server={server_target}",
                            "--secure_mode=true",
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
