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
import datetime as dt
import logging
import os
import pathlib
import threading
from typing import Any, Callable, Optional, TextIO

from kubernetes import client
from kubernetes.watch import watch

logger = logging.getLogger(__name__)


class PodLogCollector(threading.Thread):
    """A thread that streams logs from the remote pod to a local file."""

    pod_name: str
    namespace_name: str
    stop_event: threading.Event
    drain_event: threading.Event
    log_path: pathlib.Path
    log_to_stdout: bool
    log_timestamps: bool
    error_backoff_sec: int
    _out_stream: Optional[TextIO]
    _watcher: Optional[watch.Watch]
    _read_pod_log_fn: Callable[..., Any]

    def __init__(
        self,
        *,
        pod_name: str,
        namespace_name: str,
        read_pod_log_fn: Callable[..., Any],
        stop_event: threading.Event,
        log_path: pathlib.Path,
        log_to_stdout: bool = False,
        log_timestamps: bool = False,
        error_backoff_sec: int = 5,
    ):
        self.pod_name = pod_name
        self.namespace_name = namespace_name
        self.stop_event = stop_event
        # Used to indicate log draining happened. Turned out to be not as useful
        # in cases when the logging happens rarely because the blocking happens
        # in the native code, which doesn't yield until the next log message.
        self.drain_event = threading.Event()
        self.log_path = log_path
        self.log_to_stdout = log_to_stdout
        self.log_timestamps = log_timestamps
        self.error_backoff_sec = error_backoff_sec
        self._read_pod_log_fn = read_pod_log_fn
        self._out_stream = None
        self._watcher = None
        super().__init__(name=f"pod-log-{pod_name}", daemon=True)

    def run(self):
        logger.info(
            "[ns/%s] Starting log collection thread %i for %s",
            self.namespace_name,
            self.ident,
            self.pod_name,
        )
        try:
            self._out_stream = open(
                self.log_path, "w", errors="ignore", encoding="utf-8"
            )
            while not self.stop_event.is_set():
                self._stream_log()
        finally:
            self._stop()

    def flush(self):
        """Flushes the log file buffer. May be called from the main thread."""
        if self._out_stream:
            self._out_stream.flush()
            os.fsync(self._out_stream.fileno())

    def _stop(self):
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        if self._out_stream is not None:
            self._write_with_ts(
                f"[ns/{self.namespace_name}] Finished log collection"
                f" for pod {self.pod_name}",
                force_flush=True,
            )
            self._out_stream.close()
            self._out_stream = None
        if not self.drain_event.is_set():
            logger.debug("Stopped: %s", self)
        self.drain_event.set()

    def _stream_log(self):
        try:
            self._restart_stream()
        except client.ApiException as e:
            self._write_with_ts(f"Exception fetching logs: {e}")
            self._write_with_ts(
                (
                    f"Restarting log fetching in {self.error_backoff_sec} sec. "
                    "Will attempt to read from the beginning, but log "
                    "truncation may occur."
                ),
                force_flush=True,
            )
        finally:
            # Instead of time.sleep(), we're waiting on the stop event
            # in case it gets set earlier.
            self.stop_event.wait(timeout=self.error_backoff_sec)

    def _restart_stream(self):
        if self._watcher is None:
            # Only write on the first stream start to indicate when we
            # started attempting to establish the watch.
            self._write_with_ts(
                f"[ns/{self.namespace_name}] Starting pod logs watcher"
                f" for {self.pod_name}"
            )

        self._watcher = watch.Watch()
        for msg in self._watcher.stream(
            self._read_pod_log_fn,
            name=self.pod_name,
            namespace=self.namespace_name,
            timestamps=self.log_timestamps,
            follow=True,
        ):
            self._write(msg)
            # Every message check if a stop is requested.
            if self.stop_event.is_set():
                self._stop()
                return

    def _write(self, msg: str, force_flush: bool = False):
        self._out_stream.write(msg)
        self._out_stream.write("\n")
        if force_flush:
            self.flush()
        if self.log_to_stdout:
            logger.info(msg)

    def _write_with_ts(self, msg: str, force_flush: bool = False):
        ts = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        self._write(f"{ts} {msg}", force_flush)

    def __str__(self):
        return (
            f"PodLogCollector(ident='{self.ident}',"
            f" namespace='{self.namespace_name}',"
            f" pod_name='{self.pod_name}',"
            f" log_path='{self.log_path}')"
        )
