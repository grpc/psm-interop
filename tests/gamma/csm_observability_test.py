# Copyright 2023 gRPC authors.
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
import dataclasses
import logging
import time
from typing import Any, Callable, Iterable, TextIO
import unittest.mock

from absl import flags
from absl.testing import absltest
from google.api_core import exceptions as gapi_errors
from google.api_core import retry as gapi_retries
from google.cloud import monitoring_v3
import requests
from requests.exceptions import RequestException
import yaml

from framework import xds_gamma_testcase
from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.test_app.runners.k8s import gamma_server_runner
from framework.test_app.runners.k8s import k8s_base_runner
from framework.test_app.runners.k8s import k8s_xds_client_runner

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_Lang = skips.Lang

# Testing consts
TEST_RUN_SECS = 90
CLIENT_QPS = 1
REQUEST_PAYLOAD_SIZE = 27182
RESPONSE_PAYLOAD_SIZE = 31415
GRPC_METHOD_NAME = "grpc.testing.TestService/UnaryCall"
CSM_WORKLOAD_NAME_SERVER = "csm_workload_name_from_server"
CSM_WORKLOAD_NAME_CLIENT = "csm_workload_name_from_client"
CSM_CANONICAL_SERVICE_NAME_SERVER = "csm_canonical_service_name_from_server"
CSM_CANONICAL_SERVICE_NAME_CLIENT = "csm_canonical_service_name_from_client"
PROMETHEUS_HOST = "prometheus.googleapis.com"
METRIC_CLIENT_ATTEMPT_SENT = (
    f"{PROMETHEUS_HOST}/"
    "grpc_client_attempt_sent_total_compressed_message_size_bytes/histogram"
)
METRIC_CLIENT_ATTEMPT_RCVD = (
    f"{PROMETHEUS_HOST}/"
    "grpc_client_attempt_rcvd_total_compressed_message_size_bytes/histogram"
)
METRIC_CLIENT_ATTEMPT_DURATION = (
    f"{PROMETHEUS_HOST}/grpc_client_attempt_duration_seconds/histogram"
)
METRIC_CLIENT_ATTEMPT_STARTED = (
    f"{PROMETHEUS_HOST}/grpc_client_attempt_started_total/counter"
)
METRIC_SERVER_CALL_RCVD = (
    f"{PROMETHEUS_HOST}/"
    "grpc_server_call_rcvd_total_compressed_message_size_bytes/histogram"
)
METRIC_SERVER_CALL_SENT = (
    f"{PROMETHEUS_HOST}/"
    "grpc_server_call_sent_total_compressed_message_size_bytes/histogram"
)
METRIC_SERVER_CALL_DURATION = (
    f"{PROMETHEUS_HOST}/grpc_server_call_duration_seconds/histogram"
)
METRIC_SERVER_CALL_STARTED = (
    f"{PROMETHEUS_HOST}/grpc_server_call_started_total/counter"
)
HISTOGRAM_CLIENT_METRICS = (
    METRIC_CLIENT_ATTEMPT_SENT,
    METRIC_CLIENT_ATTEMPT_RCVD,
    METRIC_CLIENT_ATTEMPT_DURATION,
)
HISTOGRAM_SERVER_METRICS = (
    METRIC_SERVER_CALL_DURATION,
    METRIC_SERVER_CALL_RCVD,
    METRIC_SERVER_CALL_SENT,
)
COUNTER_CLIENT_METRICS = (METRIC_CLIENT_ATTEMPT_STARTED,)
COUNTER_SERVER_METRICS = (METRIC_SERVER_CALL_STARTED,)
HISTOGRAM_METRICS = HISTOGRAM_CLIENT_METRICS + HISTOGRAM_SERVER_METRICS
COUNTER_METRICS = COUNTER_CLIENT_METRICS + COUNTER_SERVER_METRICS
CLIENT_METRICS = HISTOGRAM_CLIENT_METRICS + COUNTER_CLIENT_METRICS
SERVER_METRICS = HISTOGRAM_SERVER_METRICS + COUNTER_SERVER_METRICS
ALL_METRICS = HISTOGRAM_METRICS + COUNTER_METRICS

GammaServerRunner = gamma_server_runner.GammaServerRunner
ClientDeploymentArgs = k8s_xds_client_runner.ClientDeploymentArgs
KubernetesClientRunner = k8s_xds_client_runner.KubernetesClientRunner
BuildQueryFn = Callable[[str, str], str]
ANY = unittest.mock.ANY


@dataclasses.dataclass(eq=False)
class MetricTimeSeries:
    """
    This class represents one TimeSeries object
    from monitoring_v3.ListTimeSeriesResponse.
    """

    # the metric name
    name: str
    # each time series has a monitored resource
    resource_type: str
    # each time series has a set of metric labels
    metric_labels: dict[str, str]
    # each time series has a set of monitored resource labels
    resource_labels: dict[str, str]
    # each time series has a set of data points
    points: list[monitoring_v3.types.Point]

    @classmethod
    def from_response(
        cls,
        name: str,
        response: monitoring_v3.types.TimeSeries,
    ) -> "MetricTimeSeries":
        return cls(
            name=name,
            resource_type=response.resource.type,
            metric_labels=dict(sorted(response.metric.labels.items())),
            resource_labels=dict(sorted(response.resource.labels.items())),
            points=list(response.points),
        )

    def pretty_print(self) -> str:
        metric = dataclasses.asdict(self)
        # too much noise to print all data points from a time series
        metric.pop("points")
        return yaml.dump(metric, sort_keys=False)


# This class is purely for debugging purposes. We want to log what we see
# from the Prometheus endpoint before being sent to Cloud Monitoring.
# Once we determined the root cause of b/323596669 we can remove this
# class.
class PrometheusLogger:
    def __init__(
        self, k8s_runner: k8s_base_runner.KubernetesBaseRunner, pod_name: str
    ):
        logfile_name = (
            f"{k8s_runner.k8s_namespace.name}_{pod_name}_prometheus.log"
        )
        log_path = k8s_runner.logs_subdir / logfile_name
        self.log_stream: TextIO = open(
            log_path, "w", errors="ignore", encoding="utf-8"
        )

    def write(self, line):
        self.log_stream.write(line)
        self.log_stream.write("\n")
        self.log_stream.flush()

    def close(self):
        self.log_stream.close()


class CsmObservabilityTest(xds_gamma_testcase.GammaXdsKubernetesTestCase):
    metric_client: monitoring_v3.MetricServiceClient

    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang == _Lang.CPP:
            return config.version_gte("v1.62.x")
        elif config.client_lang in _Lang.GO | _Lang.JAVA | _Lang.PYTHON:
            return config.version_gte("v1.65.x")
        return False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metric_client = cls.gcp_api_manager.monitoring_metric_service("v3")

    # These parameters are more pertaining to the test itself, not to
    # each run().
    def initKubernetesClientRunner(self, **kwargs) -> KubernetesClientRunner:
        return super().initKubernetesClientRunner(
            deployment_args=ClientDeploymentArgs(
                enable_csm_observability=True,
                csm_workload_name=CSM_WORKLOAD_NAME_CLIENT,
                csm_canonical_service_name=CSM_CANONICAL_SERVICE_NAME_CLIENT,
            )
        )

    # These parameters are more pertaining to the test itself, not to
    # each run().
    def initKubernetesServerRunner(self, **kwargs) -> GammaServerRunner:
        return super().initKubernetesServerRunner(
            deployment_args=gamma_server_runner.ServerDeploymentArgs(
                enable_csm_observability=True,
                csm_workload_name=CSM_WORKLOAD_NAME_SERVER,
                csm_canonical_service_name=CSM_CANONICAL_SERVICE_NAME_SERVER,
            )
        )

    def test_csm_observability(self):
        # TODO(sergiitk): [GAMMA] Consider moving out custom gamma
        #   resource creation out of self.startTestServers()
        with self.subTest("1_run_test_server"):
            start_secs = int(time.time())
            test_server: _XdsTestServer = self.startTestServers()[0]

        with self.subTest("2_start_test_client"):
            test_client: _XdsTestClient = self.startTestClient(
                test_server,
                qps=CLIENT_QPS,
                request_payload_size=REQUEST_PAYLOAD_SIZE,
                response_payload_size=RESPONSE_PAYLOAD_SIZE,
            )

        with self.subTest("3_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

        with self.subTest("4_export_prometheus_metrics_data"):
            logger.info(
                "Letting test client run for %d seconds to produce metric data",
                TEST_RUN_SECS,
            )
            if self.server_runner.should_collect_logs_prometheus:
                self._sleep_and_ping_prometheus_endpoint(
                    test_server, test_client
                )
            else:
                time.sleep(TEST_RUN_SECS)

        with self.subTest("5_query_cloud_monitoring_metrics"):
            end_secs = int(time.time())
            interval = monitoring_v3.TimeInterval(
                start_time={"seconds": start_secs},
                end_time={"seconds": end_secs},
            )
            server_histogram_results = self.query_metrics(
                HISTOGRAM_SERVER_METRICS,
                self.build_histogram_query,
                self.server_namespace,
                self.client_namespace,
                interval,
            )
            client_histogram_results = self.query_metrics(
                HISTOGRAM_CLIENT_METRICS,
                self.build_histogram_query,
                self.client_namespace,
                self.server_namespace,
                interval,
            )
            server_counter_results = self.query_metrics(
                COUNTER_SERVER_METRICS,
                self.build_counter_query,
                self.server_namespace,
                self.client_namespace,
                interval,
            )
            client_counter_results = self.query_metrics(
                COUNTER_CLIENT_METRICS,
                self.build_counter_query,
                self.client_namespace,
                self.server_namespace,
                interval,
            )
            all_results = {
                **server_histogram_results,
                **client_histogram_results,
                **server_counter_results,
                **client_counter_results,
            }
            self.assertNotEmpty(all_results, msg="No query metrics results")

        with self.subTest("6_check_metrics_time_series"):
            for metric in ALL_METRICS:
                # Every metric needs to exist in the query results
                self.assertIn(metric, all_results)

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("7_check_metrics_labels_histogram_client"):
            expected_metric_labels = {
                "csm_mesh_id": ANY,
                "csm_remote_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_SERVER,
                "csm_remote_workload_cluster_name": ANY,
                "csm_remote_workload_location": ANY,
                "csm_remote_workload_name": CSM_WORKLOAD_NAME_SERVER,
                "csm_remote_workload_namespace_name": self.server_namespace,
                "csm_remote_workload_project_id": self.project,
                "csm_remote_workload_type": "gcp_kubernetes_engine",
                "csm_service_name": self.server_runner.service_name,
                "csm_service_namespace_name": self.server_namespace,
                "csm_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_CLIENT,
                "grpc_method": GRPC_METHOD_NAME,
                "grpc_status": "OK",
                "grpc_target": ANY,
                "otel_scope_name": ANY,
                "otel_scope_version": ANY,
                "pod": test_client.hostname,
            }
            self.filter_label_matcher_based_on_lang(
                self.lang_spec.client_lang, expected_metric_labels
            )
            for metric in HISTOGRAM_CLIENT_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("8_check_metrics_labels_histogram_server"):
            expected_metric_labels = {
                "csm_mesh_id": ANY,
                "csm_remote_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_CLIENT,
                "csm_remote_workload_cluster_name": ANY,
                "csm_remote_workload_location": ANY,
                "csm_remote_workload_name": CSM_WORKLOAD_NAME_CLIENT,
                "csm_remote_workload_namespace_name": self.client_namespace,
                "csm_remote_workload_project_id": self.project,
                "csm_remote_workload_type": "gcp_kubernetes_engine",
                "csm_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_SERVER,
                "grpc_method": GRPC_METHOD_NAME,
                "grpc_status": "OK",
                "otel_scope_name": ANY,
                "otel_scope_version": ANY,
                "pod": test_server.hostname,
            }
            self.filter_label_matcher_based_on_lang(
                self.lang_spec.server_lang, expected_metric_labels
            )
            for metric in HISTOGRAM_SERVER_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("9_check_metrics_labels_counter_client"):
            expected_metric_labels = {
                "grpc_method": GRPC_METHOD_NAME,
                "grpc_target": ANY,
                "otel_scope_name": ANY,
                "otel_scope_version": ANY,
                "pod": test_client.hostname,
            }
            self.filter_label_matcher_based_on_lang(
                self.lang_spec.client_lang, expected_metric_labels
            )
            for metric in COUNTER_CLIENT_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("10_check_metrics_labels_counter_server"):
            expected_metric_labels = {
                "grpc_method": GRPC_METHOD_NAME,
                "otel_scope_name": ANY,
                "otel_scope_version": ANY,
                "pod": test_server.hostname,
            }
            self.filter_label_matcher_based_on_lang(
                self.lang_spec.server_lang, expected_metric_labels
            )
            for metric in COUNTER_SERVER_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the right set of monitored resource
        # label keys and values
        with self.subTest("11_check_client_resource_labels_client"):
            # all metrics should have the same set of monitored resource labels
            # keys, which come from the GMP job
            expected_resource_labels = {
                "cluster": ANY,
                "instance": ANY,
                "job": self.client_runner.pod_monitoring_name,
                "location": ANY,
                "namespace": self.client_namespace,
                "project_id": self.project,
            }
            for metric in CLIENT_METRICS:
                metric_time_series = all_results[metric]
                self.assertEqual(
                    "prometheus_target", metric_time_series.resource_type
                )

                actual_resource_labels = metric_time_series.resource_labels
                self.assertDictEqual(
                    expected_resource_labels, actual_resource_labels
                )

        # Testing whether each metric has the right set of monitored resource
        # label keys and values
        with self.subTest("12_check_server_resource_labels_server"):
            # all metrics should have the same set of monitored resource labels
            # keys, which come from the GMP job
            expected_resource_labels = {
                "cluster": ANY,
                "instance": ANY,
                "job": self.server_runner.pod_monitoring_name,
                "location": ANY,
                "namespace": self.server_namespace,
                "project_id": self.project,
            }
            for metric in SERVER_METRICS:
                metric_time_series = all_results[metric]
                self.assertEqual(
                    "prometheus_target", metric_time_series.resource_type
                )

                actual_resource_labels = metric_time_series.resource_labels
                self.assertDictEqual(
                    expected_resource_labels, actual_resource_labels
                )

        # This tests whether each of the "bytes sent" histogram type metric
        # should have at least 1 data point whose mean should converge to be
        # close to the number of bytes being sent by the RPCs.
        with self.subTest("13_check_bytes_sent_vs_data_points"):
            for metric in (METRIC_CLIENT_ATTEMPT_SENT, METRIC_SERVER_CALL_RCVD):
                self.assertAtLeastOnePointWithinRange(
                    all_results[metric].points, REQUEST_PAYLOAD_SIZE
                )

            for metric in (METRIC_CLIENT_ATTEMPT_RCVD, METRIC_SERVER_CALL_SENT):
                self.assertAtLeastOnePointWithinRange(
                    all_results[metric].points, RESPONSE_PAYLOAD_SIZE
                )

    @classmethod
    def build_histogram_query(
        cls, metric_type: str, namespace: str, remote_namespace: str
    ) -> str:
        #
        # The list_time_series API requires us to query one metric
        # at a time.
        #
        # The 'grpc_status = "OK"' and
        # 'csm_remote_workload_namespace_name = "remote_namespace"' filter
        # condition is needed because some time series data points were logged
        # when the grpc_status was "UNAVAILABLE" when the client/server were
        # establishing connections.
        #
        # The 'grpc_method' filter condition is needed because the
        # server metrics are also serving on the Channelz requests.
        #
        # The 'resource.labels.namespace' filter condition allows us to
        # filter metrics just for the current test run.
        return (
            f'metric.type = "{metric_type}" AND '
            'metric.labels.grpc_status = "OK" AND '
            f'metric.labels.grpc_method = "{GRPC_METHOD_NAME}" AND '
            f'metric.labels.csm_remote_workload_namespace_name = "{remote_namespace}" AND '
            f'resource.labels.namespace = "{namespace}"'
        )

    @classmethod
    def build_counter_query(
        cls, metric_type: str, namespace: str, _remote_namespace: str
    ) -> str:
        # For these num rpcs started counter metrics, they do not have the
        # 'grpc_status' label, nor do they have remote namespace.
        return (
            f'metric.type = "{metric_type}" AND '
            f'metric.labels.grpc_method = "{GRPC_METHOD_NAME}" AND '
            f'resource.labels.namespace = "{namespace}"'
        )

    @classmethod
    def filter_label_matcher_based_on_lang(
        cls, language: _Lang, label_matcher: dict[str, Any]
    ) -> None:
        """
        Filter label_matcher based on language.
        """
        if language == _Lang.PYTHON:
            # TODO(xuanwn): Remove this once https://github.com/open-telemetry/opentelemetry-python/issues/3072 is fixed.
            label_matcher.pop("otel_scope_version", None)
            label_matcher.pop("otel_scope_name", None)

    def query_metrics(
        self,
        metric_names: Iterable[str],
        build_query_fn: BuildQueryFn,
        namespace: str,
        remote_namespace: str,
        interval: monitoring_v3.TimeInterval,
    ) -> dict[str, MetricTimeSeries]:
        """
        A helper function to make the cloud monitoring API call to query
        metrics created by this test run.
        """
        # Based on default retry settings for list_time_series method:
        # https://github.com/googleapis/google-cloud-python/blob/google-cloud-monitoring-v2.18.0/packages/google-cloud-monitoring/google/cloud/monitoring_v3/services/metric_service/transports/base.py#L210-L218
        # Modified: predicate extended to retry on a wider range of error types.
        retry_settings = gapi_retries.Retry(
            initial=0.1,
            maximum=30.0,
            multiplier=1.3,
            predicate=gapi_retries.if_exception_type(
                # Retry on 5xx, not just 503 ServiceUnavailable. This also
                # covers gRPC Unknown, DataLoss, and DeadlineExceeded statuses.
                # 501 MethodNotImplemented not excluded because most likely
                # reason we'd see this error is server misconfiguration, so we
                # want to give it a chance to recovering this situation too.
                gapi_errors.ServerError,
                # Retry on 429/ResourceExhausted: recoverable rate limiting.
                gapi_errors.TooManyRequests,
            ),
            deadline=90.0,
        )
        results = {}
        for metric in metric_names:
            logger.info("Requesting list_time_series for metric %s", metric)
            response = self.metric_client.list_time_series(
                name=f"projects/{self.project}",
                filter=build_query_fn(metric, namespace, remote_namespace),
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                retry=retry_settings,
            )
            time_series = list(response)

            self.assertLen(
                time_series,
                1,
                msg=f"Query for {metric} should return exactly 1 time series."
                f" Found {len(time_series)}.",
            )

            metric_time_series = MetricTimeSeries.from_response(
                metric, time_series[0]
            )
            logger.info(
                "Metric %s:\n%s", metric, metric_time_series.pretty_print()
            )
            results[metric] = metric_time_series
        return results

    def assertAtLeastOnePointWithinRange(
        self,
        points: list[monitoring_v3.types.Point],
        ref_bytes: int,
        tolerance: float = 0.05,
    ):
        """
        A helper function to check whether at least one of the "points" whose
        mean should be within X% of ref_bytes.
        """
        for point in points:
            if (
                ref_bytes * (1 - tolerance)
                < point.value.distribution_value.mean
                < ref_bytes * (1 + tolerance)
            ):
                return
        self.fail(
            f"No data point with {ref_bytes}±{tolerance*100}% bytes found"
        )

    def _sleep_and_ping_prometheus_endpoint(
        self, test_server: _XdsTestServer, test_client: _XdsTestClient
    ):
        server_prometheus_logger = PrometheusLogger(
            self.server_runner, test_server.hostname
        )
        client_prometheus_logger = PrometheusLogger(
            self.client_runner, test_client.hostname
        )
        try:
            for i in range(0, TEST_RUN_SECS // 10):
                time.sleep(10)
                curr_secs = int(time.time())
                server_prometheus_logger.write(
                    f"Prometheus endpoint content at {curr_secs}"
                )
                server_prometheus_logger.write(
                    self._ping_prometheus_endpoint(
                        test_server.rpc_host,
                        test_server.monitoring_port,
                    )
                )
                client_prometheus_logger.write(
                    f"Prometheus endpoint content at {curr_secs}"
                )
                client_prometheus_logger.write(
                    self._ping_prometheus_endpoint(
                        test_client.rpc_host,
                        test_client.monitoring_port,
                    )
                )
        finally:
            server_prometheus_logger.close()
            client_prometheus_logger.close()

    @classmethod
    def _ping_prometheus_endpoint(
        cls, monitoring_host: str, monitoring_port: int
    ) -> str:
        """
        A helper function to ping the pod's Prometheus endpoint to get what GMP
        sees from the OTel exporter before passing metrics to Cloud Monitoring.
        """
        try:
            prometheus_log = requests.get(
                f"http://{monitoring_host}:{monitoring_port}/metrics"
            )
            return "\n".join(prometheus_log.text.splitlines())
        except RequestException as e:
            logger.warning("Http request to Prometheus endpoint failed: %r", e)
            # It's OK the caller will receive nothing in case of an exception.
            # Caller can continue.
            return ""


if __name__ == "__main__":
    absltest.main()
