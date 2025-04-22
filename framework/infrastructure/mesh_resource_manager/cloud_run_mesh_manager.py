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
import logging
from typing import Final, Optional

from framework.infrastructure import gcp
import framework.infrastructure.traffic_director as td_base

# Type aliases
GcpResource = gcp.compute.ComputeV1.GcpResource

logger = logging.getLogger(__name__)


class CloudRunMeshManager(td_base.TrafficDirectorAppNetManager):
    MESH_NAME: Final[str] = "grpc-mesh"
    NEG_NAME: Final[str] = "grpc-neg"

    def __init__(
        self,
        gcp_api_manager: gcp.api.GcpApiManager,
        project: str,
        region: str,
        *,
        resource_prefix: str,
        resource_suffix: Optional[str] = None,
        network: str = "default",
        compute_api_version: str = "v1",
        enable_dualstack: bool = False,
    ):
        super().__init__(
            gcp_api_manager,
            project,
            resource_prefix=resource_prefix,
            resource_suffix=resource_suffix,
            network=network,
            compute_api_version=compute_api_version,
            enable_dualstack=enable_dualstack,
        )

        # Settings
        self.region = region

        # Managed resources
        self.neg: Optional[GcpResource] = None

    def backend_service_add_cloudrun_backends(
        self,
        *,
        capacity_scaler: float = 1.0,
    ):
        if not self.neg:
            raise ValueError(
                f"Needed to create serverless NEG {self.NEG_NAME} first"
            )
        self.backends = [self.neg]

        new_backends = []
        for backend in self.backends:
            new_backend = {
                "group": backend.url,
                "capacityScaler": capacity_scaler,
            }

            new_backends.append(new_backend)

        logging.info(
            "Adding Cloud Run backends to Backend Service %s: %r",
            self.backend_service.name,
            self.backends,
        )

        self.compute.backend_service_patch_backends_with_body(
            self.backend_service,
            new_backends,
        )

    def create_neg_serverless(self, service_name: str) -> GcpResource:
        name = self.make_resource_name(self.NEG_NAME)
        logger.info("Creating serverless NEG %s", name)
        neg = self.compute.create_neg_serverless(
            name, self.region, service_name
        )
        logger.info("Loading NEG %s", neg)
        self.neg = self.compute.get_neg_serverless(name, self.region)
        return neg

    def delete_neg_serverless(self, force=False):
        if force:
            name = self.make_resource_name(self.NEG_NAME)
        elif self.neg:
            name = self.neg.name
        else:
            return
        logger.info("Deleting serverless NEG %s", name)
        self.compute.delete_neg_serverless(name, self.region)
        self.neg = None

    def cleanup(self, *, force=False):
        super().cleanup(force=force)
        self.delete_neg_serverless(force=force)
