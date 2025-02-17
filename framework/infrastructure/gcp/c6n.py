# Copyright 2024 gRPC authors.
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

#emchandwani : check
import abc
import logging

from google.cloud import run_v2

from framework.infrastructure.gcp.api import GcpProjectApiResource

logger = logging.getLogger(__name__)


class CloudRunApiManager(GcpProjectApiResource, metaclass=abc.ABCMeta):
    DEFAULT_TEST_PORT = 8080
    project: str
    region: str
    _parent: str
    _client: run_v2.ServicesClient
    _service: run_v2.Service

    def __init__(self, project: str, region: str):
        if not project:
            raise ValueError("Project ID cannot be empty or None.")
        if not region:
            raise ValueError("Region cannot be empty or None.")

        self.project = project
        self.region = region
        client_options = {"api_endpoint": f"{self.region}-run.googleapis.com"}
        self._client = run_v2.ServicesClient(client_options=client_options)
        self._parent = f"projects/{self.project}/locations/{self.region}"
        self._service = None

    def deploy_service(self, service_name: str, image_name: str, *,test_port: int = DEFAULT_TEST_PORT):
        if not service_name:
            raise ValueError("service_name cannot be empty or None")
        if not image_name:
            raise ValueError("image_name cannot be empty or None")
        service_name = service_name[:49]

        service = run_v2.Service(
            template=run_v2.RevisionTemplate(
                containers=[
                    run_v2.Container(
                        image=image_name,
                        ports=[
                            run_v2.ContainerPort(
                                name="http1",
                                # emchandwani : change to self.server_xds_port
                                container_port=test_port,
                            ),
                        ],
                    )
                ]
            ),
        )

        request = run_v2.CreateServiceRequest(
            parent=self._parent, service=service, service_id=service_name
        )

        try:
            operation = self._client.create_service(request=request)
            # operation=GcpProjectApiResource.wait_for_operation(operation_request=request,test_success_fn=logger.info("done"))
            self._service = operation.result(timeout=600)
            logger.info("Deployed service: %s", self._service.uri)
            return self._service.uri
        except Exception as e:
            logger.exception("Error deploying service: %s", e)
            raise

    def get_service_url(self):
        if self._service is None:
            raise RuntimeError("Cloud Run service not deployed yet.")
        return self._service.uri

    def delete_service(self, service_name: str):
        try:
            request = run_v2.DeleteServiceRequest(
                name=f"{self._parent}/services/{service_name}"
            )
            operation = self._client.delete_service(request=request)
            operation.result(timeout=300)
            logger.info("Deleted service: %s", service_name)
        except Exception as e:
            logger.exception("Error deleting service: %s", e)
            raise
