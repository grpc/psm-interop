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
import abc
import dataclasses
import logging
from typing import Any, Final

from framework.infrastructure import gcp

logger = logging.getLogger(__name__)

# Type aliases
GcpResource = gcp.compute.ComputeV1.GcpResource

DEFAULT_TEST_PORT: Final[int] = 8080
DISCOVERY_URI: Final[str] = "https://run.googleapis.com/$discovery/rest?"


@dataclasses.dataclass(frozen=True)
class CloudRunService:
    service_name: str
    url: str

    @classmethod
    def from_response(
        cls, name: str, response: dict[str, Any]
    ) -> "CloudRunService":
        return cls(
            service_name=name,
            url=response["urls"],
        )


class CloudRunApiManager(
    gcp.api.GcpStandardCloudApiResource, metaclass=abc.ABCMeta
):
    region: str

    def __init__(
        self, project: str, region: str, api_manager: gcp.api.GcpApiManager
    ):
        if not project:
            raise ValueError("Project ID cannot be empty or None.")
        if not region:
            raise ValueError("Region cannot be empty or None.")
        # api_manager = gcp.api.GcpApiManager(v2_discovery_uri=DISCOVERY_URI)
        self.region = region
        super().__init__(api_manager.cloudrun(self.api_version), project)
        self._services_collection = self.api.projects().locations().services()

    SERVICES = "services"

    @property
    def api_name(self) -> str:
        """Returns the API name for Cloud Run."""
        return "Cloud Run"

    @property
    def api_version(self) -> str:
        """Returns the API version for Cloud Run."""
        return "v2"

    def create_service(self, service_name: str, body: dict) -> GcpResource:
        return self._create_resource(
            collection=self._services_collection,
            location=self.region,
            serviceId=service_name,
            body=body,
        )

    def get_service(self, service_name: str) -> CloudRunService:
        result = self._get_resource(
            collection=self._services_collection,
            full_name=self.resource_full_name(
                service_name, self.SERVICES, self.region
            ),
        )
        return CloudRunService.from_response(
            self.resource_full_name(service_name, self.SERVICES, self.region),
            result,
        )

    def delete_service(self, service_name: str):
        # pylint: disable=no-member
        self._delete_resource(
            self._services_collection,
            full_name=self.resource_full_name(
                service_name, self.SERVICES, self.region
            ),
        )

    def deploy_service(
        self,
        service_name: str,
        image_name: str,
        *,
        test_port: int = DEFAULT_TEST_PORT,
    ):
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
                        "ports": [{"containerPort": test_port, "name": "h2c"}],
                    }
                ],
            },
        }

        logger.info("Deploying Cloud Run service '%s'", service_name)
        self.create_service(service_name, service_body)
        return self.get_service(service_name)
