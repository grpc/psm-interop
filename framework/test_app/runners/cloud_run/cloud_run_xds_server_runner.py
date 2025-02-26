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
Run xDS Test Server on Cloud Run.
"""
import dataclasses
import logging
from typing import List, Optional
import uuid

from absl import flags
from typing_extensions import override

from framework import xds_flags
from framework import xds_k8s_flags
from framework.infrastructure.gcp import cloud_run
from framework.test_app.runners.cloud_run import cloud_run_base_runner
from framework.test_app.server_app import XdsTestServer

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CloudRunDeploymentArgs:
    """Arguments for deploying a server to Cloud Run."""

    env_vars: dict = dataclasses.field(default_factory=dict)
    max_instances: int = 10  # Example: Maximum number of instances
    min_instances: int = 0  # Example: Minimum number of instances
    service_account_email: str = ""  # Email address of the service account
    timeout_seconds: int = 300  # Timeout for requests
    revision_suffix: Optional[str] = None

    def as_dict(self):
        return {
            "env_vars": self.env_vars,
            "max_instances": self.max_instances,
            "min_instances": self.min_instances,
            "service_account_email": self.service_account_email,
            "timeout_seconds": self.timeout_seconds,
        }


class CloudRunServerRunner(cloud_run_base_runner.CloudRunBaseRunner):
    """Manages xDS Test Servers running on Cloud Run."""

    def __init__(
        self,
        project: str,
        service_name: str,
        image_name: str,
        network: str,
        region: str,
    ):
        super().__init__(
            project,
            service_name,
            image_name,
            network=network,
            region=region,
        )
        # Mutable state associated with each run.
        self._reset_state()

    @override
    def _reset_state(self):
        super()._reset_state()
        self.service = None
        self.pods_to_servers = {}
        self.replica_count = 0

    @override
    def run(self, **kwargs) -> List[XdsTestServer]:
        """Deploys and manages the xDS Test Server on Cloud Run."""
        logger.info(self.service_name)
        logger.info(self.image_name)

        super().run(**kwargs)
        servers = [
            XdsTestServer(
                ip="0.0.0.0", rpc_port=0, hostname=self.current_revision
            )
        ]
        self.servers = servers  # Add servers to the list
        self._start_completed()
        return servers

    def get_service_url(self):
        return self.cloudrun_api_manager.get_service_url()

    @override
    def cleanup(self, *, force=False):
        try:
            if self.service:
                self.stop()
                self.service_name = None
        finally:
            self._stop()
