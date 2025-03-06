# Copyright 2022 gRPC authors.
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
from framework.infrastructure import c6n
from framework.test_app.runners import base_runner

logger = logging.getLogger(__name__)

_RunnerError = base_runner.RunnerError
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
    region: Optional[str] = None
    _current_revision: Optional[str] = None
    gcp_project: Optional[str] = None
    gcp_ui_url: Optional[str] = None

    run_history: collections.deque[RunHistory]

    time_start_requested: Optional[dt.datetime] = None
    time_start_completed: Optional[dt.datetime] = None
    time_stopped: Optional[dt.datetime] = None

    def __init__(
        self,
        project: str,
        service_name: str,
        image_name: str,
        network: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        super().__init__()

        self.project = project
        self.service_name = service_name
        self.image_name = image_name
        self.network = network
        self.region = region
        self.current_revision = None
        self.gcp_project = None
        self.gcp_ui_url = None
        self.run_history = collections.deque()

        # Persistent across many runs.
        self.run_history = collections.deque()

        # Mutable state associated with each run.
        self._reset_state()

        # Highlighter.
        self._highlighter = _HighlighterYaml()

        self._initalize_cloudrun_api_manager()

    def _initalize_cloudrun_api_manager(self):
        """Initializes the CloudRunApiManager."""
        self.cloudrun_api_manager = c6n.CloudRunApiManager(
            project=self.project, region=self.region
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
        self.time_start_requested = dt.datetime.now(tz=dt.timezone.utc)
        self._current_revision = self.cloudrun_api_manager.deploy_service(
            self.service_name, self.image_name
        )

    def _start_completed(self):
        self.time_start_completed = dt.datetime.now(tz=dt.timezone.utc)

    def _stop(self):
        self.time_stopped = dt.datetime.now(tz=dt.timezone.utc)
        if self.time_start_requested:
            run_history = RunHistory(
                revision_id=self._current_revision,
                time_start_requested=self.time_start_requested,
                time_start_completed=self.time_start_completed,
                time_stopped=self.time_stopped,
            )
            self.run_history.append(run_history)

    @classmethod
    def _get_workload_identity_member_name(
        cls, project, namespace_name, service_account_name
    ):
        """
        Returns workload identity member name used to authenticate Kubernetes
        service accounts.

        https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity
        """
        return (
            f"serviceAccount:{project}.svc.id.goog"
            f"[{namespace_name}/{service_account_name}]"
        )
