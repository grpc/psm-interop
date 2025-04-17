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
from typing import Optional
from typing_extensions import override

from framework.infrastructure import gcp
from framework.test_app import server_app
from framework.test_app.runners.cloud_run import cloud_run_base_runner

logger = logging.getLogger(__name__)


class CloudRunServerRunner(cloud_run_base_runner.CloudRunBaseRunner):
    """Manages xDS Test Servers running on Cloud Run."""

    service: Optional[gcp.cloud_run.CloudRunV2] = None
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

        self._initalize_cloud_run_api_manager()

    @override
    def _reset_state(self):
        super()._reset_state()
        self.service = None
        self.current_revision = None
        self.pods_to_servers = {}
        self.replica_count = 0

    @override
    def run(self, **kwargs) -> list[server_app.XdsTestServer]:
        """Deploys and manages the xDS Test Server on Cloud Run."""
        logger.info(
            "Starting cloud run server with service %s and image %s",
            self.service_name,
            self.image_name,
        )

        super().run(**kwargs)

        self.current_revision = self.service.url
        servers = [
            server_app.XdsTestServer(
                ip="0.0.0.0", rpc_port=0, hostname=self.current_revision
            )
        ]
        self._start_completed()
        return servers

    def get_service(self):
        return self.cloud_run_api_manager.get_service(self.service_name)

    @override
    def cleanup(self, *, force=False):
        # TODO(emchandwani) : Collect service logs in a file.
        try:
            super().cleanup(force=force)
            self.service = None
            self.current_revision = None
        finally:
            self._stop()
