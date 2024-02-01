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
from typing import Callable, Iterable
import unittest.mock

from absl import flags
from absl.testing import absltest
from google.cloud import monitoring_v3
import yaml

from framework import xds_gamma_testcase
from framework import xds_k8s_testcase
from framework.helpers import skips
from framework.test_app.runners.k8s import gamma_server_runner
from framework.test_app.runners.k8s import k8s_xds_client_runner

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_Lang = skips.Lang

# Testing consts
TEST_RUN_SECS = 90
REQUEST_PAYLOAD_SIZE = 271828
RESPONSE_PAYLOAD_SIZE = 314159
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
KubernetesClientRunner = k8s_xds_client_runner.KubernetesClientRunner


# This class should represent one TimeSeries object from
# monitoring_v3.ListTimeSeriesResponse.
@dataclasses.dataclass(eq=False)
class MetricTimeSeries:
    # the metric name
    name: str
    # each time series has a set of metric labels
    metric_labels: dict[str, str]
    # each time series has a monitored resource
    resource_type: str
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
            metric_labels=dict(response.metric.labels),
            resource_type=response.resource.type,
            resource_labels=dict(response.resource.labels),
            points=list(response.points),
        )

    def pretty_print(self) -> str:
        metric = dataclasses.asdict(self)
        # too much noise to print all data points from a time series
        metric.pop("points")
        return yaml.dump(metric, sort_keys=True)


class CsmObservabilityTest(xds_gamma_testcase.GammaXdsKubernetesTestCase):
    metric_client: monitoring_v3.MetricServiceClient

    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang == _Lang.CPP and config.server_lang == _Lang.CPP:
            # CSM Observability Test is only supported for CPP for now.
            return config.version_gte("v1.62.x")
        return False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metric_client = cls.gcp_api_manager.monitoring_metric_service("v3")

    # These parameters are more pertaining to the test itself, not to
    # each run().
    def initKubernetesClientRunner(self, **kwargs) -> KubernetesClientRunner:
        return super().initKubernetesClientRunner(
            csm_workload_name=CSM_WORKLOAD_NAME_CLIENT,
            csm_canonical_service_name=CSM_CANONICAL_SERVICE_NAME_CLIENT,
        )

    # These parameters are more pertaining to the test itself, not to
    # each run().
    def initKubernetesServerRunner(self, **kwargs) -> GammaServerRunner:
        return super().initKubernetesServerRunner(
            csm_workload_name=CSM_WORKLOAD_NAME_SERVER,
            csm_canonical_service_name=CSM_CANONICAL_SERVICE_NAME_SERVER,
        )

    BuildQueryFn = Callable[[str], str]

    @classmethod
    def build_histogram_query(cls, metric_type: str) -> str:
        #
        # The list_time_series API requires us to query one metric
        # at a time.
        #
        # The 'grpc_status = "OK"' filter condition is needed because
        # some time series data points were logged when the grpc_status
        # was "UNAVAILABLE" when the client/server were establishing
        # connections.
        #
        # The 'grpc_method' filter condition is needed because the
        # server metrics are also serving on the Channelz requests.
        #
        return (
            f'metric.type = "{metric_type}" AND '
            'metric.labels.grpc_status = "OK" AND '
            f'metric.labels.grpc_method = "{GRPC_METHOD_NAME}"'
        )

    @classmethod
    def build_counter_query(cls, metric_type: str) -> str:
        # For these num rpcs started counter metrics, they do not have the
        # 'grpc_status' label
        return (
            f'metric.type = "{metric_type}" AND '
            f'metric.labels.grpc_method = "{GRPC_METHOD_NAME}"'
        )

    # A helper function to make the cloud monitoring API call to query metrics
    # created by this test run.
    def query_metrics(
        self,
        metric_names: Iterable[str],
        build_query_fn: BuildQueryFn,
        interval: monitoring_v3.TimeInterval,
    ) -> dict[str, MetricTimeSeries]:
        results = {}
        for metric in metric_names:
            response = self.metric_client.list_time_series(
                name=f"projects/{self.project}",
                filter=build_query_fn(metric),
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )
            time_series = list(response)
            if len(time_series) > 1:
                self.fail(
                    f"Query for {metric} should return exactly 1 time series. "
                    f"Found {len(time_series)}."
                )
            metric_time_series = MetricTimeSeries.from_response(
                metric, time_series[0]
            )
            logger.info(metric_time_series.pretty_print())
            results[metric] = metric_time_series
        return results

    # A helper function to check whether at least one of the "points" whose
    # mean should be within 5% of ref_bytes.
    def assertAtLeastOnePointWithinRange(
        self,
        points: list[monitoring_v3.types.Point],
        ref_bytes: int,
        tolerance: float = 0.05,
    ):
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

    def test_csm_observability(self):
        # TODO(sergiitk): [GAMMA] Consider moving out custom gamma
        #   resource creation out of self.startTestServers()
        with self.subTest("1_run_test_server"):
            start_secs = int(time.time())
            test_server: _XdsTestServer = self.startTestServers(
                enable_csm_observability=True,
            )[0]

        with self.subTest("2_start_test_client"):
            test_client: _XdsTestClient = self.startTestClient(
                test_server,
                enable_csm_observability=True,
                request_payload_size=REQUEST_PAYLOAD_SIZE,
                response_payload_size=RESPONSE_PAYLOAD_SIZE,
            )
            logger.info("Letting test client run for %d seconds", TEST_RUN_SECS)
            time.sleep(TEST_RUN_SECS)

        with self.subTest("3_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

        with self.subTest("4_query_cloud_monitoring_metrics"):
            end_secs = int(time.time())
            interval = monitoring_v3.TimeInterval(
                {
                    "end_time": {"seconds": end_secs, "nanos": 0},
                    "start_time": {"seconds": start_secs, "nanos": 0},
                }
            )
            histogram_results = self.query_metrics(
                HISTOGRAM_METRICS, self.build_histogram_query, interval
            )
            counter_results = self.query_metrics(
                COUNTER_METRICS, self.build_counter_query, interval
            )
            all_results = {**histogram_results, **counter_results}
            self.assertNotEmpty(all_results, msg="No query metrics results")

        with self.subTest("5_check_metrics_time_series"):
            for metric in ALL_METRICS:
                # Every metric needs to exist in the query results
                self.assertIn(metric, all_results)

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("6_check_metrics_labels_histogram_client"):
            expected_metric_labels = {
                "csm_mesh_id": unittest.mock.ANY,
                "csm_remote_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_SERVER,
                "csm_remote_workload_cluster_name": unittest.mock.ANY,
                "csm_remote_workload_location": unittest.mock.ANY,
                "csm_remote_workload_name": CSM_WORKLOAD_NAME_SERVER,
                "csm_remote_workload_namespace_name": self.server_namespace,
                "csm_remote_workload_project_id": self.project,
                "csm_remote_workload_type": "gcp_kubernetes_engine",
                "csm_service_name": self.server_runner.service_name,
                "csm_service_namespace_name": self.server_namespace,
                "csm_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_CLIENT,
                "grpc_method": GRPC_METHOD_NAME,
                "grpc_status": "OK",
                "grpc_target": unittest.mock.ANY,
                "otel_scope_name": unittest.mock.ANY,
                "otel_scope_version": unittest.mock.ANY,
                "pod": test_client.hostname,
            }
            for metric in HISTOGRAM_CLIENT_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("7_check_metrics_labels_histogram_server"):
            expected_metric_labels = {
                "csm_mesh_id": unittest.mock.ANY,
                "csm_remote_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_CLIENT,
                "csm_remote_workload_cluster_name": unittest.mock.ANY,
                "csm_remote_workload_location": unittest.mock.ANY,
                "csm_remote_workload_name": CSM_WORKLOAD_NAME_CLIENT,
                "csm_remote_workload_namespace_name": self.client_namespace,
                "csm_remote_workload_project_id": self.project,
                "csm_remote_workload_type": "gcp_kubernetes_engine",
                "csm_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_SERVER,
                "grpc_method": GRPC_METHOD_NAME,
                "grpc_status": "OK",
                "otel_scope_name": unittest.mock.ANY,
                "otel_scope_version": unittest.mock.ANY,
                "pod": test_server.hostname,
            }
            for metric in HISTOGRAM_SERVER_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("8_check_metrics_labels_counter_client"):
            expected_metric_labels = {
                "grpc_method": GRPC_METHOD_NAME,
                "grpc_target": unittest.mock.ANY,
                "otel_scope_name": unittest.mock.ANY,
                "otel_scope_version": unittest.mock.ANY,
                "pod": test_client.hostname,
            }
            for metric in COUNTER_CLIENT_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("9_check_metrics_labels_counter_server"):
            expected_metric_labels = {
                "grpc_method": GRPC_METHOD_NAME,
                "otel_scope_name": unittest.mock.ANY,
                "otel_scope_version": unittest.mock.ANY,
                "pod": test_server.hostname,
            }
            for metric in COUNTER_SERVER_METRICS:
                actual_metric_labels = all_results[metric].metric_labels
                self.assertDictEqual(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the right set of monitored resource
        # label keys and values
        with self.subTest("10_check_client_resource_labels_client"):
            # all metrics should have the same set of monitored resource labels
            # keys, which come from the GMP job
            expected_resource_labels = {
                "cluster": unittest.mock.ANY,
                "instance": unittest.mock.ANY,
                "job": self.client_runner.pod_monitoring_name,
                "location": unittest.mock.ANY,
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
        with self.subTest("11_check_server_resource_labels_server"):
            # all metrics should have the same set of monitored resource labels
            # keys, which come from the GMP job
            expected_resource_labels = {
                "cluster": unittest.mock.ANY,
                "instance": unittest.mock.ANY,
                "job": self.server_runner.pod_monitoring_name,
                "location": unittest.mock.ANY,
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

        # This tests whether each of the "byes sent" histogram type metric
        # should have at least 1 data point whose mean should converge to be
        # close to the number of bytes being sent by the RPCs.
        with self.subTest("12_check_bytes_sent_vs_data_points"):
            for metric in (
                METRIC_CLIENT_ATTEMPT_SENT,
                METRIC_SERVER_CALL_RCVD,
            ):
                self.assertAtLeastOnePointWithinRange(
                    all_results[metric].points, REQUEST_PAYLOAD_SIZE
                )

            for metric in (
                METRIC_CLIENT_ATTEMPT_RCVD,
                METRIC_SERVER_CALL_SENT,
            ):
                self.assertAtLeastOnePointWithinRange(
                    all_results[metric].points, RESPONSE_PAYLOAD_SIZE
                )


if __name__ == "__main__":
    absltest.main()
