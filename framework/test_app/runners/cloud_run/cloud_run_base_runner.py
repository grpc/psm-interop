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
Common functionality for running xDS Test Client and Server on CloudRun.
"""
from abc import ABCMeta
import collections
import dataclasses
import datetime as dt
import logging
from typing import Optional

import framework
from framework.infrastructure import gcp
from framework.infrastructure.gcp import cloud_run
from framework.test_app.runners import base_runner

logger = logging.getLogger(__name__)

_HighlighterYaml = framework.helpers.highlighter.HighlighterYaml
_helper_datetime = framework.helpers.datetime
_datetime = dt.datetime
_timedelta = dt.timedelta


@dataclasses.dataclass(frozen=True)
class RunHistory:
    revision_id: str
    time_start_requested: _datetime
    time_start_completed: Optional[_datetime]
    time_stopped: _datetime


class CloudRunBaseRunner(base_runner.BaseRunner, metaclass=ABCMeta):
    """Runs xDS Test Client/Server on Cloud Run."""

    project: str
    service_name: str
    image_name: str
    network: Optional[str] = None
    tag: str = "latest"
    region: str = "us-central1"
    gcp_api_manager: gcp.api.GcpApiManager
    current_revision: Optional[str] = None
    cloud_run_resource: Optional[cloud_run.GcpResource] = None

    run_history: collections.deque[RunHistory]

    time_start_requested: Optional[dt.datetime] = None
    time_start_completed: Optional[dt.datetime] = None
    time_stopped: Optional[dt.datetime] = None

    def __init__(
        self,
        project: str,
        service_name: str,
        image_name: str,
        region: str,
        gcp_api_manager: gcp.api.GcpApiManager,
        network: Optional[str] = None,
    ) -> None:
        super().__init__()

        self.project = project
        self.service_name = service_name
        self.image_name = image_name
        self.network = network
        self.region = region
        self.current_revision = None
        self.gcp_ui_url = gcp_api_manager.gcp_ui_url
        self.gcp_api_manager = gcp_api_manager

        # Persistent across many runs.
        self.run_history = collections.deque()

        # Mutable state associated with each run.
        self._reset_state()

        # Highlighter.
        self._highlighter = _HighlighterYaml()

        self._initalize_cloud_run_api_manager()

    def _initalize_cloud_run_api_manager(self):
        """Initializes the CloudRunApiManager."""
        self.cloud_run_api_manager = cloud_run.CloudRunApiManager(
            project=self.project,
            region=self.region,
            api_manager=self.gcp_api_manager,
        )

    def run(self, **kwargs):
        if self.time_start_requested and not self.time_stopped:
            if self.time_start_completed:
                raise RuntimeError(
                    f"Service {self.service_name}: has already been started "
                    f"at {self.time_start_completed.isoformat()}"
                )
            raise RuntimeError(
                f"Service {self.service_name}: start has already been "
                f"requested at {self.time_start_requested.isoformat()}"
            )

        self._reset_state()
        self.time_start_requested = _datetime.now()
        self.cloud_run_resource = self.cloud_run_api_manager.deploy_service(
            self.service_name,
            self.image_name,
        )
        self.current_revision = self.cloud_run_resource.url

    def _start_completed(self):
        self.time_start_completed = _datetime.now()

    def _stop(self):
        self.time_stopped = _datetime.now()
        if self.time_start_requested:
            run_history = RunHistory(
                revision_id=self.current_revision,
                time_start_requested=self.time_start_requested,
                time_start_completed=self.time_start_completed,
                time_stopped=self.time_stopped,
            )
            self.run_history.append(run_history)

    @classmethod
    def _logs_explorer_link(
        cls,
        *,
        service_name: str,
        gcp_project: str,
        gcp_ui_url: str,
        location: str,
        start_time: Optional[_datetime] = None,
        end_time: Optional[_datetime] = None,
        cursor_time: Optional[_datetime] = None,
    ):
        """Output the link to test server/client logs in GCP Logs Explorer."""
        if not start_time:
            start_time = _datetime.now()
        if not end_time:
            end_time = start_time + _timedelta(minutes=30)

        logs_start = _helper_datetime.iso8601_utc_time(start_time)
        logs_end = _helper_datetime.iso8601_utc_time(end_time)
        request = {"timeRange": f"{logs_start}/{logs_end}"}
        if cursor_time:
            request["cursorTimestamp"] = _helper_datetime.iso8601_utc_time(
                cursor_time
            )
        query = {
            "resource.type": "cloud_run_revision",
            "resource.labels.project_id": gcp_project,
            "resource.labels.service_name": service_name,
            "resource.labels.location": location,
        }

        link = cls._logs_explorer_link_from_params(
            gcp_ui_url=gcp_ui_url,
            gcp_project=gcp_project,
            query=query,
            request=request,
        )
        link_to = service_name
        # A whitespace at the end to indicate the end of the url.
        logger.info("GCP Logs Explorer link to %s:\n%s ", link_to, link)

    def logs_explorer_run_history_links(self):
        """Prints a separate GCP Logs Explorer link for each run *completed* by
        the runner.

        This excludes the current run, if it hasn't been completed.
        """
        if not self.run_history:
            logger.info("No completed deployments of %s", self.service_name)
            return
        for run in self.run_history:
            self._logs_explorer_link(
                service_name=self.service_name,
                gcp_project=self.project,
                gcp_ui_url=self.gcp_ui_url,
                location=self.region,
                start_time=run.time_start_requested,
                cursor_time=run.time_start_completed,
                end_time=run.time_stopped,
            )

    def stop(self):
        """Deletes Cloud Run Service"""
        logger.info("Deleting Cloud Run service: %s", self.service_name)
        self.cloud_run_api_manager.delete_service(self.service_name)
