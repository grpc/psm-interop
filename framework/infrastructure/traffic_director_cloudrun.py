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
from framework.infrastructure.gcp.network_security import NetworkSecurityV1Beta1
from framework.infrastructure.gcp.network_services import NetworkServicesV1Beta1
from framework.infrastructure.traffic_director import TrafficDirectorAppNetManager
from framework.infrastructure import gcp

# _NetworkSecurityV1Beta1 = gcp.network_security.NetworkSecurityV1Beta1
ServerTlsPolicy = gcp.network_security.ServerTlsPolicy
ClientTlsPolicy = gcp.network_security.ClientTlsPolicy
AuthorizationPolicy = gcp.network_security.AuthorizationPolicy

# Network Services
# _NetworkServicesV1Beta1 = gcp.network_services.NetworkServicesV1Beta1
EndpointPolicy = gcp.network_services.EndpointPolicy
GrpcRoute = gcp.network_services.GrpcRoute
HttpRoute = gcp.network_services.HttpRoute
Mesh = gcp.network_services.Mesh

class TrafficDirectorCloudRunManager(TrafficDirectorAppNetManager):
    MESH_NAME = "grpc-mesh"
    SERVER_TLS_POLICY_NAME = "server-tls-policy"
    CLIENT_TLS_POLICY_NAME = "client-tls-policy"
    AUTHZ_POLICY_NAME = "authz-policy"
    ENDPOINT_POLICY = "endpoint-policy"
    CERTIFICATE_PROVIDER_INSTANCE = "google_cloud_private_spiffe"

    # netsec: _NetworkSecurityV1Beta1
    # netsvc: _NetworkServicesV1Beta1

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

        # API
        # self.netsvc = _NetworkServicesV1Beta1(gcp_api_manager, project)
        # self.netsvc = _NetworkSecurityV1Beta1(gcp_api_manager, project)

        # Managed resources
        # TODO(gnossen) PTAL at the pylint error
        self.grpc_route: Optional[GrpcRoute] = None
        self.http_route: Optional[HttpRoute] = None
        self.mesh: Optional[Mesh] = None

        # Managed resources
        self.server_tls_policy: Optional[ServerTlsPolicy] = None
        self.client_tls_policy: Optional[ClientTlsPolicy] = None
        self.authz_policy: Optional[AuthorizationPolicy] = None
        self.endpoint_policy: Optional[EndpointPolicy] = None

    def backend_service_add_backends(
        self,
        backends: Sequence[str],
        region: Optional[str] = None,
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

        # self.backends.update(new_backends)
        backend_service = self.backend_service

        logging.info(
            "Adding backends to Backend Service %s: %r",
            backend_service.name,
            new_backends,
        )

        self.compute.backend_service_patch_backends(
            backend_service,
            backends,
            is_cloudrun=True,
        )