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
from typing import Optional, Sequence

from framework.infrastructure import gcp
from framework.infrastructure.traffic_director import (
    TrafficDirectorAppNetManager,
)

GrpcRoute = gcp.network_services.GrpcRoute
Mesh = gcp.network_services.Mesh


class TrafficDirectorCloudRunManager(TrafficDirectorAppNetManager):
    MESH_NAME = "grpc-mesh"

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
