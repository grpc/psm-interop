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
import logging
import time

from absl import flags
from absl.testing import absltest
from google.api_core import exceptions as gapi_errors
from google.cloud import monitoring_v3

from framework import xds_gamma_testcase
from framework import xds_k8s_testcase
from framework.helpers import skips

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
VALUE_NOT_CHECKED = object()  # Sentinel value
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
HISTOGRAM_CLIENT_METRICS = [
    METRIC_CLIENT_ATTEMPT_SENT,
    METRIC_CLIENT_ATTEMPT_RCVD,
    METRIC_CLIENT_ATTEMPT_DURATION,
]
HISTOGRAM_SERVER_METRICS = [
    METRIC_SERVER_CALL_DURATION,
    METRIC_SERVER_CALL_RCVD,
    METRIC_SERVER_CALL_SENT,
]
COUNTER_CLIENT_METRICS = [
    METRIC_CLIENT_ATTEMPT_STARTED,
]
COUNTER_SERVER_METRICS = [
    METRIC_SERVER_CALL_STARTED,
]
HISTOGRAM_METRICS = HISTOGRAM_CLIENT_METRICS + HISTOGRAM_SERVER_METRICS
COUNTER_METRICS = COUNTER_CLIENT_METRICS + COUNTER_SERVER_METRICS
CLIENT_METRICS = HISTOGRAM_CLIENT_METRICS + COUNTER_CLIENT_METRICS
SERVER_METRICS = HISTOGRAM_SERVER_METRICS + COUNTER_SERVER_METRICS
ALL_METRICS = HISTOGRAM_METRICS + COUNTER_METRICS


class CsmObservabilityTest(xds_gamma_testcase.GammaXdsKubernetesTestCase):
    metric_client: monitoring_v3.MetricServiceClient
    test_server: _XdsTestServer
    test_client: _XdsTestClient

    @staticmethod
    def is_supported(config: skips.TestConfig) -> bool:
        if config.client_lang == _Lang.CPP and config.server_lang == _Lang.CPP:
            # CSM Observability Test is only supported for CPP for now.
            return config.version_gte("v1.61.x")
        return False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metric_client = cls.gcp_api_manager.monitoring_metric_service("v3")

    # query filter string to be passed to the Monitoring API
    def build_filter_str(self, filter_str_template, replacements):
        return filter_str_template % replacements

    # A helper function to make the cloud monitoring API call to query metrics
    # created by this test run.
    def query_metrics(self, metric_names, filter_str_template, interval):
        results = {}
        metric_log_lines = []
        for metric in metric_names:
            response = self.metric_client.list_time_series(
                name=f"projects/{self.project}",
                filter=self.build_filter_str(
                    filter_str_template,
                    {
                        "metric": metric,
                        "grpc_method": GRPC_METHOD_NAME,
                    },
                ),
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )
            time_series = []
            metric_log_lines.append(f"Metric: {metric}")
            for series in response:
                metric_log_lines.append(" Metric Labels:")
                for label_key, label_value in series.metric.labels.items():
                    metric_log_lines.append(f"  {label_key}: {label_value}")
                metric_log_lines.append(" Resource Labels:")
                for label_key, label_value in series.resource.labels.items():
                    metric_log_lines.append(f"  {label_key}: {label_value}")
                time_series.append(series)
            results[metric] = time_series
        logger.info("\n".join(metric_log_lines))
        return results

    # A helper function to check whether at least one of the "points" whose
    # mean should be within 5% of ref_bytes.
    def assertAtLeastOnePointWithinRange(self, points, ref_bytes):
        for point in points:
            if point.value.distribution_value.mean > (
                ref_bytes * 0.95
            ) and point.value.distribution_value.mean < (ref_bytes * 1.05):
                return
        self.fail(f"No data point with ~{ref_bytes} bytes found")

    # 1. actual_labels must have all the keys in expected_labels
    #
    # 2. actual_labels must have all the correct key: value pairs
    #    in expected_labels unless the expected value is
    #    VALUE_NOT_CHECKED
    #
    # 3. actual_labels may have extra keys that we don't care about
    def assertExpectedLabels(self, expected_labels, actual_labels):
        # Test whether actual_labels contains all the keys that we are
        # expected
        for label_key in expected_labels.keys():
            self.assertIn(label_key, actual_labels)

        # Test the label values against the expected dict
        # Ignore those keys that were marked VALUE_NOT_CHECKED
        self.assertDictEqual(
            {
                key: value
                for key, value in expected_labels.items()
                if value != VALUE_NOT_CHECKED
            },
            {
                key: actual_labels[key]
                for key, value in expected_labels.items()
                if value != VALUE_NOT_CHECKED
            },
        )

    def test_csm_observability(self):
        # TODO(sergiitk): [GAMMA] Consider moving out custom gamma
        #   resource creation out of self.startTestServers()
        with self.subTest("1_run_test_server"):
            start_secs = int(time.time())
            self.test_server = self.startTestServers(
                enable_csm_observability=True,
                csm_workload_name=CSM_WORKLOAD_NAME_SERVER,
                csm_canonical_service_name=CSM_CANONICAL_SERVICE_NAME_SERVER,
            )[0]

        with self.subTest("2_start_test_client"):
            self.test_client = self.startTestClient(
                self.test_server,
                enable_csm_observability=True,
                request_payload_size=REQUEST_PAYLOAD_SIZE,
                response_payload_size=RESPONSE_PAYLOAD_SIZE,
                csm_workload_name=CSM_WORKLOAD_NAME_CLIENT,
                csm_canonical_service_name=CSM_CANONICAL_SERVICE_NAME_CLIENT,
            )
            logger.info("Letting test client run for %d seconds", TEST_RUN_SECS)
            time.sleep(TEST_RUN_SECS)

        with self.subTest("3_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(self.test_client)

        with self.subTest("4_query_monitoring_metric_client"):
            end_secs = int(time.time())
            interval = monitoring_v3.TimeInterval(
                {
                    "end_time": {"seconds": end_secs, "nanos": 0},
                    "start_time": {"seconds": start_secs, "nanos": 0},
                }
            )
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
            filter_str_template = (
                'metric.type = "%(metric)s" AND '
                'metric.labels.grpc_status = "OK" AND '
                'metric.labels.grpc_method = "%(grpc_method)s"'
            )
            histogram_results = self.query_metrics(
                HISTOGRAM_METRICS, filter_str_template, interval
            )

            # The num rpcs started counter metrics do not have the
            # 'grpc_status' label
            filter_str_template = (
                'metric.type = "%(metric)s" AND '
                'metric.labels.grpc_method = "%(grpc_method)s"'
            )
            counter_results = self.query_metrics(
                COUNTER_METRICS, filter_str_template, interval
            )

            all_results = {**histogram_results, **counter_results}
            self.assertNotEmpty(all_results, msg="No query metrics results")

        with self.subTest("5_check_metrics_time_series"):
            for metric in ALL_METRICS:
                # Every metric needs to exist in the query results
                self.assertIn(metric, all_results)

                time_series = all_results[metric]
                # There should be exactly 1 time series per metric with these
                # label value combinations that we are expecting.
                self.assertEqual(1, len(time_series))

        # Testing whether each metric has the correct set of metric keys and
        # values
        with self.subTest("6_check_metrics_labels"):
            for metric in ALL_METRICS:
                if metric in HISTOGRAM_CLIENT_METRICS:
                    expected_metric_labels = {
                        "csm_mesh_id": VALUE_NOT_CHECKED,
                        "csm_remote_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_SERVER,
                        "csm_remote_workload_cluster_name": VALUE_NOT_CHECKED,
                        "csm_remote_workload_location": VALUE_NOT_CHECKED,
                        "csm_remote_workload_name": CSM_WORKLOAD_NAME_SERVER,
                        "csm_remote_workload_namespace_name": self.server_namespace,
                        "csm_remote_workload_project_id": self.project,
                        "csm_remote_workload_type": "gcp_kubernetes_engine",
                        "csm_service_name": self.server_runner.service_name,
                        "csm_service_namespace_name": self.server_namespace,
                        "csm_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_CLIENT,
                        "grpc_method": GRPC_METHOD_NAME,
                        "grpc_status": VALUE_NOT_CHECKED,
                        "grpc_target": VALUE_NOT_CHECKED,
                        "pod": self.test_client.hostname,
                    }
                elif metric in HISTOGRAM_SERVER_METRICS:
                    expected_metric_labels = {
                        "csm_mesh_id": VALUE_NOT_CHECKED,
                        "csm_remote_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_CLIENT,
                        "csm_remote_workload_cluster_name": VALUE_NOT_CHECKED,
                        "csm_remote_workload_location": VALUE_NOT_CHECKED,
                        "csm_remote_workload_name": CSM_WORKLOAD_NAME_CLIENT,
                        "csm_remote_workload_namespace_name": self.client_namespace,
                        "csm_remote_workload_project_id": self.project,
                        "csm_remote_workload_type": "gcp_kubernetes_engine",
                        "csm_workload_canonical_service": CSM_CANONICAL_SERVICE_NAME_SERVER,
                        "grpc_method": GRPC_METHOD_NAME,
                        "grpc_status": VALUE_NOT_CHECKED,
                        "pod": self.test_server.hostname,
                    }
                elif metric in COUNTER_CLIENT_METRICS:
                    expected_metric_labels = {
                        "grpc_method": GRPC_METHOD_NAME,
                        "grpc_target": VALUE_NOT_CHECKED,
                        "pod": self.test_client.hostname,
                    }
                elif metric in COUNTER_SERVER_METRICS:
                    expected_metric_labels = {
                        "grpc_method": GRPC_METHOD_NAME,
                        "pod": self.test_server.hostname,
                    }
                else:
                    self.fail(f"Unknown metric: {metric}")

                actual_metric_labels = all_results[metric][0].metric.labels
                self.assertExpectedLabels(
                    expected_metric_labels, actual_metric_labels
                )

        # Testing whether each metric has the right set of monitored resource
        # label keys and values
        with self.subTest("7_check_resource_labels"):
            for metric in ALL_METRICS:
                time_series = all_results[metric][0]
                self.assertEqual("prometheus_target", time_series.resource.type)

                # all metrics should have the same set of monitored resource labels
                # keys, which come from the GMP job
                if metric in CLIENT_METRICS:
                    expected_resource_labels = {
                        "cluster": VALUE_NOT_CHECKED,
                        "instance": VALUE_NOT_CHECKED,
                        "job": self.client_runner.pod_monitoring_name,
                        "location": VALUE_NOT_CHECKED,
                        "namespace": self.client_namespace,
                        "project_id": self.project,
                    }
                elif metric in SERVER_METRICS:
                    expected_resource_labels = {
                        "cluster": VALUE_NOT_CHECKED,
                        "instance": VALUE_NOT_CHECKED,
                        "job": self.server_runner.pod_monitoring_name,
                        "location": VALUE_NOT_CHECKED,
                        "namespace": self.server_namespace,
                        "project_id": self.project,
                    }
                else:
                    self.fail(f"Unexpected metric: {metric}")

                actual_resource_labels = time_series.resource.labels
                self.assertExpectedLabels(
                    expected_resource_labels, actual_resource_labels
                )

        # This tests whether each of the "byes sent" histogram type metric
        # should have at least 1 data point whose mean should converge to be
        # close to the number of bytes being sent by the RPCs.
        with self.subTest("8_check_bytes_sent_vs_data_points"):
            for metric in [
                METRIC_CLIENT_ATTEMPT_SENT,
                METRIC_SERVER_CALL_RCVD,
            ]:
                self.assertAtLeastOnePointWithinRange(
                    all_results[metric][0].points, REQUEST_PAYLOAD_SIZE
                )

            for metric in [
                METRIC_CLIENT_ATTEMPT_RCVD,
                METRIC_SERVER_CALL_SENT,
            ]:
                self.assertAtLeastOnePointWithinRange(
                    all_results[metric][0].points, RESPONSE_PAYLOAD_SIZE
                )


if __name__ == "__main__":
    absltest.main()
