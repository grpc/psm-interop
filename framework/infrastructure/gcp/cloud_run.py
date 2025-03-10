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
import logging

from googleapiclient import discovery

from framework.infrastructure import gcp

logger = logging.getLogger(__name__)

DEFAULT_TEST_PORT = 8080
DEFAULT_TIMEOUT = 600


class CloudRunApiManager(
    gcp.api.GcpStandardCloudApiResource, metaclass=abc.ABCMeta
):
    project: str
    region: str
    _parent: str
    service: discovery.Resource
    api_manager: gcp.api.GcpApiManager
    discovery_url = "https://run.googleapis.com/$discovery/rest?"

    def __init__(self, project: str, region: str):
        if not project:
            raise ValueError("Project ID cannot be empty or None.")
        if not region:
            raise ValueError("Region cannot be empty or None.")
        self.api_manager = gcp.api.GcpApiManager(
            v2_discovery_uri="https://run.googleapis.com/$discovery/rest?"
        )
        self.project = project
        self.region = region
        service: discovery.Resource = self.api_manager.cloudrun("v2")
        self.service = service
        self._parent = f"projects/{self.project}/locations/{self.region}"
        super().__init__(self.service, project)

    @property
    def api_name(self) -> str:
        """Returns the API name for Cloud Run."""
        return "run"

    @property
    def api_version(self) -> str:
        """Returns the API version for Cloud Run."""
        return "v2"

    def create_cloud_run_resource(
        self, service: discovery.Resource, service_name: str, body: dict
    ):
        return self._create_resource(
            collection=service.projects().locations().services(),
            body=body,
            serviceId=service_name,
            location=self.region,
        )

    def get_cloud_run_resource(
        self, service: discovery.Resource, service_name: str
    ):
        return self._get_resource(
            collection=service.projects().locations().services(),
            full_name=self.resource_full_name(
                service_name, "services", self.region
            ),
        )

    def get_service_uri(self, service_name: str) -> str:
        response = self.get_cloud_run_resource(self.service, service_name)
        return response.get("uri")

    def delete_cloud_run_resource(
        self, service: discovery.Resource, service_name: str
    ):
        return self._delete_resource(
            collection=service.projects().locations().services(),
            full_name=self.resource_full_name(
                service_name, "services", self.region
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

        try:
            service_body = {}
            service_body = {
                "launch_stage": "alpha",
                "template": {
                    "containers": [
                        {
                            "image": image_name,
                            "ports": [
                                {"containerPort": test_port, "name": "h2c"}
                            ],
                        }
                    ],
                },
            }

            self.create_cloud_run_resource(
                self.service, service_name, service_body
            )
            logger.info("Deploying Cloud Run service '%s'", service_name)
            return self.get_service_uri(service_name)

        except Exception as e:  # noqa pylint: disable=broad-except
            logger.exception("Error deploying Cloud Run service: %s", e)

    def delete_service(self, service_name: str):
        try:
            self.delete_cloud_run_resource(self.service, service_name)
        except Exception as e:
            logger.exception("Error deleting service: %s", e)
            raise
