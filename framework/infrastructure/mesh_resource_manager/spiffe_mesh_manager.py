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
from typing import Optional

from framework.infrastructure import gcp
import framework.infrastructure.traffic_director as td_base


# Network Services
GrpcRoute = gcp.network_services.GrpcRoute
Mesh = gcp.network_services.Mesh

logger = logging.getLogger(__name__)


class SpiffeMeshManager(td_base.TrafficDirectorSecureManager):
    GRPC_ROUTE_NAME = "grpc-route"
    MESH_NAME = "mesh"
    netsvc: gcp.network_services.NetworkServicesV1

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
        # API
        self.netsvc = gcp.network_services.NetworkServicesV1(
            gcp_api_manager, project
        )

    def create_mesh(self) -> Mesh:
        name = self.make_resource_name(self.MESH_NAME)
        logger.info("Creating Mesh %s", name)
        body = {}
        self.netsvc.create_mesh(name, body)
        self.mesh = self.netsvc.get_mesh(name)
        logger.debug("Loaded Mesh: %s", self.mesh)
        return self.mesh

    def delete_mesh(self, force=False):
        if force:
            name = self.make_resource_name(self.MESH_NAME)
        elif self.mesh:
            name = self.mesh.name
        else:
            return
        logger.info("Deleting Mesh %s", name)
        self.netsvc.delete_mesh(name)
        self.mesh = None

    def create_grpc_route(self, src_host: str, src_port: int) -> GrpcRoute:
        host = f"{src_host}:{src_port}"
        service_name = self.netsvc.resource_full_name(
            self.backend_service.name, "backendServices"
        )
        body = {
            "meshes": [self.mesh.url],
            "hostnames": host,
            "rules": [
                {"action": {"destinations": [{"serviceName": service_name}]}}
            ],
        }
        name = self.make_resource_name(self.GRPC_ROUTE_NAME)
        logger.info("Creating GrpcRoute %s", name)
        self.netsvc.create_grpc_route(name, body)
        self.grpc_route = self.netsvc.get_grpc_route(name)
        logger.debug("Loaded GrpcRoute: %s", self.grpc_route)
        return self.grpc_route

    def delete_grpc_route(self, force=False):
        if force:
            name = self.make_resource_name(self.GRPC_ROUTE_NAME)
        elif self.grpc_route:
            name = self.grpc_route.name
        else:
            return
        logger.info("Deleting GrpcRoute %s", name)
        self.netsvc.delete_grpc_route(name)
        self.grpc_route = None

    def cleanup(self, *, force=False):
        self.delete_grpc_route(force=force)
        self.delete_mesh(force=force)
        super().cleanup(force=force)
