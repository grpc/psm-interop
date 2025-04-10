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
import dataclasses
import logging
from typing import Final, Optional

from typing_extensions import override

from framework.infrastructure import gcp
from framework.test_app.client_app import XdsTestClient
from framework.test_app.runners.cloud_run import cloud_run_base_runner

logger = logging.getLogger(__name__)

DEFAULT_PORT: Final[int] = 443


@dataclasses.dataclass(frozen=True)
class CloudRunDeploymentArgs:
    """Arguments for deploying a server to Cloud Run."""

    env_vars: dict = dataclasses.field(default_factory=dict)
    max_instances: int = 10  # Example: Maximum number of instances
    min_instances: int = 0  # Example: Minimum number of instances
    service_account_email: str = ""  # Email address of the service account
    timeout_seconds: int = 300  # Timeout for requests
    revision_suffix: Optional[str] = None
    mesh_name: Optional[str] = None
    server_target: Optional[str] = None

    def as_dict(self):
        return {
            "env_vars": self.env_vars,
            "max_instances": self.max_instances,
            "min_instances": self.min_instances,
            "service_account_email": self.service_account_email,
            "timeout_seconds": self.timeout_seconds,
            "mesh_name": self.mesh_name,
            "server_target": self.server_target,
        }


class CloudRunClientRunner(cloud_run_base_runner.CloudRunBaseRunner):
    """Manages xDS Test Clients running on Cloud Run."""

    def __init__(
        self,
        project: str,
        service_name: str,
        image_name: str,
        network: str,
        region: str,
        gcp_api_manager: gcp.api.GcpApiManager,
        mesh_name: str,
        server_target: str,
        is_client: bool = True,
    ):
        super().__init__(
            project,
            service_name,
            image_name,
            network=network,
            region=region,
            gcp_ui_url=gcp_api_manager.gcp_ui_url,
            mesh_name=mesh_name,
            server_target=server_target,
            is_client=is_client,
        )
        # Mutable state associated with each run.
        self._reset_state()

    @override
    def _reset_state(self):
        super()._reset_state()
        self.service = None
        self.replica_count = 0

    @override
    def run(self, **kwargs) -> XdsTestClient:
        """Deploys and manages the xDS Test Client on Cloud Run."""
        logger.info(
            "Starting cloud run Client with service %s and image %s",
            self.service_name,
            self.image_name,
        )

        super().run(**kwargs)
        client_url = self.current_revision[0].removeprefix("https://")
        client = XdsTestClient(
            ip=client_url,
            rpc_port=DEFAULT_PORT,
            rpc_host=client_url,
            server_target=self.server_target,
            hostname=self.current_revision,
            maintenance_port=DEFAULT_PORT,
        )
        self._start_completed()
        return client

    def get_service_url(self):
        return self.cloud_run_api_manager.get_service_uri(self.service_name)

    @override
    def cleanup(self, *, force=False):
        try:
            self.stop()
        finally:
            self._stop()
