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
from typing import Final, Optional, Sequence

from framework.infrastructure import gcp
import framework.infrastructure.traffic_director as td_base

# Type aliases
_ComputeV1 = gcp.compute.ComputeV1
GcpResource = _ComputeV1.GcpResource
GrpcRoute = gcp.network_services.GrpcRoute
Mesh = gcp.network_services.Mesh

logger = logging.getLogger(__name__)


class TrafficDirectorCloudRunManager(td_base.TrafficDirectorAppNetManager):
    MESH_NAME: Final[str] = "grpc-mesh"
    NEG_NAME: Final[str] = "grpc-neg"

    def __init__(
        self,
        gcp_api_manager: gcp.api.GcpApiManager,
        project: str,
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

        # Managed resources
        self.grpc_route: Optional[GrpcRoute] = None
        self.mesh: Optional[Mesh] = None
        self.neg: Optional[GcpResource] = None

    def backend_service_add_backends(
        self,
        backends: Sequence[str],
        balancing_mode: str = "CONNECTION",
        max_rate_per_endpoint: Optional[int] = None,
        capacity_scaler: float = 1.0,
        *,
        circuit_breakers: Optional[dict[str, int]] = None,
    ):
        new_backends = []
        for backend in backends:
            new_backend = {
                "group": backend,
                "balancingMode": balancing_mode,
                "maxRatePerEndpoint": max_rate_per_endpoint,
                "capacityScaler": capacity_scaler,
            }

            if circuit_breakers is not None:
                new_backend["circuitBreakers"] = circuit_breakers

            new_backends.append(new_backend)

        logging.info(
            "Adding backends to Backend Service %s: %r",
            self.backend_service.name,
            new_backends,
        )

        self.compute.backend_service_patch_backends(
            self.backend_service,
            backends,
            is_cloud_run=True,
        )

    def create_serverless_neg(self, region: str, service_name: str):
        name = self.make_resource_name(self.NEG_NAME)
        logger.info("Creating serverless NEG %s", name)
        neg = self.compute.create_serverless_neg(name, region, service_name)
        logger.info("Loading NEG %s", neg)
        self.neg = self.compute.get_serverless_network_endpoint_group(
            name, region
        )
        return neg

    def delete_serverless_neg(self, region: str, force=False):
        if force:
            name = self.make_resource_name(self.NEG_NAME)
        elif self.mesh:
            name = self.neg.name
        else:
            return
        logger.info("Deleting serverless NEG %s", name)
        self.compute.delete_serverless_neg(name, region)
        self.neg = None

    def cleanup(
        self, *, region, force=False
    ):  # pylint: disable=arguments-differ
        self.delete_serverless_neg(region=region, force=force)
        super().cleanup(force=force)
