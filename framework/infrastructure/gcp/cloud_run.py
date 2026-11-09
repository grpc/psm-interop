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
import datetime as dt
import functools
import logging
from typing import Any, Final

from google.rpc import code_pb2
import tenacity

from framework.infrastructure import gcp

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CloudRunService:
    service_name: str
    revision: str
    uri: str

    @classmethod
    def from_response(
        cls, name: str, response: dict[str, Any]
    ) -> "CloudRunService":
        return cls(
            service_name=name,
            uri=response["uri"],
            revision=response["latestCreatedRevision"],
        )


class CloudRunV2(gcp.api.GcpStandardCloudApiResource, metaclass=abc.ABCMeta):
    """Cloud Run API v2."""

    SERVICES: Final[str] = "services"
    _CREATE_SERVICE_ATTEMPTS: Final[int] = 3
    _CREATE_SERVICE_RETRY_WAIT: Final[dt.timedelta] = dt.timedelta(seconds=10)

    region: str

    def __init__(
        self, api_manager: gcp.api.GcpApiManager, project: str, region: str
    ):
        if not project:
            raise ValueError("Project ID cannot be empty or None.")
        if not region:
            raise ValueError("Region cannot be empty or None.")
        self.region = region
        super().__init__(api_manager.cloudrun(self.api_version), project)
        self._services_collection = self.api.projects().locations().services()

    @property
    def api_name(self) -> str:
        """Returns the API name for Cloud Run."""
        return "Cloud Run"

    @property
    def api_version(self) -> str:
        """Returns the API version for Cloud Run."""
        return "v2"

    @staticmethod
    def _is_deadline_exceeded_operation_error(
        exception: BaseException,
    ) -> bool:
        return (
            isinstance(exception, gcp.api.OperationError)
            and exception.error.code == code_pb2.DEADLINE_EXCEEDED
        )

    def _before_create_service_retry(
        self,
        service_name: str,
        retry_state: tenacity.RetryCallState,
    ) -> None:
        sleep_sec = (
            retry_state.next_action.sleep
            if retry_state.next_action is not None
            else 0
        )
        err = (
            retry_state.outcome.exception()
            if retry_state.outcome is not None
            else None
        )
        logger.warning(
            "Cloud Run create_service '%s' timed out with DEADLINE_EXCEEDED "
            "(attempt %d/%d); deleting failed service and retrying in %ss: %s",
            service_name,
            retry_state.attempt_number,
            self._CREATE_SERVICE_ATTEMPTS,
            sleep_sec,
            err,
        )
        self.delete_service(service_name)

    def create_service(self, service_name: str, body: dict) -> None:
        # Retry transient Cloud Run revision health-check / scheduling stalls
        # (Ref b/581058489). Delete any failed service left behind before
        # retrying to avoid ALREADY_EXISTS on the next attempt.
        retryer = tenacity.Retrying(
            retry=tenacity.retry_if_exception(
                self._is_deadline_exceeded_operation_error
            ),
            wait=tenacity.wait_fixed(
                self._CREATE_SERVICE_RETRY_WAIT.total_seconds()
            ),
            stop=tenacity.stop_after_attempt(self._CREATE_SERVICE_ATTEMPTS),
            before_sleep=functools.partial(
                self._before_create_service_retry, service_name
            ),
            reraise=True,
        )
        retryer(
            self._create_resource,
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
        self._delete_resource(
            self._services_collection,
            full_name=self.resource_full_name(
                service_name, self.SERVICES, self.region
            ),
        )
