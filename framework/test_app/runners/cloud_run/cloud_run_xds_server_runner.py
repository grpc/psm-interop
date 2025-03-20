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
import logging
from typing import List

from typing_extensions import override

from framework.infrastructure import gcp
from framework.test_app.runners.cloud_run import cloud_run_base_runner
from framework.test_app.server_app import XdsTestServer

logger = logging.getLogger(__name__)


class CloudRunServerRunner(cloud_run_base_runner.CloudRunBaseRunner):
    """Manages xDS Test Servers running on Cloud Run."""

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
            gcp_ui_url=gcp_api_manager.gcp_ui_url,
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
        logger.info(
            "Starting cloud run server with service %s and image %s",
            self.service_name,
            self.image_name,
        )

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
        return self.cloud_run_api_manager.get_service_uri(self.service_name)

    @override
    def cleanup(self, *, force=False):
        try:
            self.stop()
        finally:
            self._stop()
