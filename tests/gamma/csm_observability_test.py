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
from framework import xds_k8s_flags
from framework import xds_k8s_testcase
from framework.helpers import skips

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient
_Lang = skips.Lang

# Testing consts
_TEST_RUN_SECS = 90
_REQUEST_PAYLOAD_SIZE = 271828
_RESPONSE_PAYLOAD_SIZE = 314159
_GRPC_METHOD_NAME = "grpc.testing.TestService/UnaryCall"
_CSM_WORKLOAD_NAME_SERVER = "csm_workload_name_from_server"
_CSM_WORKLOAD_NAME_CLIENT = "csm_workload_name_from_client"
_CSM_CANONICAL_SERVICE_NAME_SERVER = "csm_canonical_service_name_from_server"
_CSM_CANONICAL_SERVICE_NAME_CLIENT = "csm_canonical_service_name_from_client"
_METRIC_CLIENT_ATTEMPT_SENT = "prometheus.googleapis.com/grpc_client_attempt_sent_total_compressed_message_size_bytes/histogram"
_METRIC_CLIENT_ATTEMPT_RCVD = "prometheus.googleapis.com/grpc_client_attempt_rcvd_total_compressed_message_size_bytes/histogram"
_METRIC_CLIENT_ATTEMPT_DURATION = (
    "prometheus.googleapis.com/grpc_client_attempt_duration_seconds/histogram"
)
_METRIC_CLIENT_ATTEMPT_STARTED = (
    "prometheus.googleapis.com/grpc_client_attempt_started_total/counter"
)
_METRIC_SERVER_CALL_RCVD = "prometheus.googleapis.com/grpc_server_call_rcvd_total_compressed_message_size_bytes/histogram"
_METRIC_SERVER_CALL_SENT = "prometheus.googleapis.com/grpc_server_call_sent_total_compressed_message_size_bytes/histogram"
_METRIC_SERVER_CALL_DURATION = (
    "prometheus.googleapis.com/grpc_server_call_duration_seconds/histogram"
)
_METRIC_SERVER_CALL_STARTED = (
    "prometheus.googleapis.com/grpc_server_call_started_total/counter"
)
_HISTOGRAM_CLIENT_METRICS = [
    _METRIC_CLIENT_ATTEMPT_SENT,
    _METRIC_CLIENT_ATTEMPT_RCVD,
    _METRIC_CLIENT_ATTEMPT_DURATION,
]
_HISTOGRAM_SERVER_METRICS = [
    _METRIC_SERVER_CALL_DURATION,
    _METRIC_SERVER_CALL_RCVD,
    _METRIC_SERVER_CALL_SENT,
]
_COUNTER_CLIENT_METRICS = [
    _METRIC_CLIENT_ATTEMPT_STARTED,
]
_COUNTER_SERVER_METRICS = [
    _METRIC_SERVER_CALL_STARTED,
]
_HISTOGRAM_METRICS = _HISTOGRAM_CLIENT_METRICS + _HISTOGRAM_SERVER_METRICS
_COUNTER_METRICS = _COUNTER_CLIENT_METRICS + _COUNTER_SERVER_METRICS
_ALL_METRICS = _HISTOGRAM_METRICS + _COUNTER_METRICS


class CsmObservabilityTest(xds_gamma_testcase.GammaXdsKubernetesTestCase):
    metric_client: monitoring_v3.MetricServiceClient

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

    # A helper function to make the cloud monitoring API call to query metrics
    # created by this test run.
    def _query_metrics(self, metric_names, filter_str, interval):
        results = {}
        for metric in metric_names:
            response = self.metric_client.list_time_series(
                name=f"projects/{self.project}",
                filter=filter_str
                % {
                    "metric": metric,
                    "grpc_method": _GRPC_METHOD_NAME,
                },
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )
            time_series = []
            logger.debug("Metric: %s", metric)
            for series in response:
                logger.debug(" Metric Labels:")
                for label_key, label_value in series.metric.labels.items():
                    logger.debug(
                        "  %(label_key)s: %(label_value)s",
                        {
                            "label_key": label_key,
                            "label_value": label_value,
                        },
                    )
                logger.debug(" Resource Labels:")
                for label_key, label_value in series.resource.labels.items():
                    logger.debug(
                        "  %(label_key)s: %(label_value)s",
                        {
                            "label_key": label_key,
                            "label_value": label_value,
                        },
                    )
                time_series.append(series)
            results[metric] = time_series
        return results

    # A helper function to check whether at least one of the "points" whose
    # mean should be within 5% of ref_bytes.
    def _at_least_one_point_within_range(self, points, ref_bytes) -> bool:
        for point in points:
            if point.value.distribution_value.mean > (
                ref_bytes * 0.95
            ) and point.value.distribution_value.mean < (ref_bytes * 1.05):
                return True
        return False

    def _is_server_metric(self, metric_name) -> bool:
        return "server" in metric_name

    def _is_client_metric(self, metric_name) -> bool:
        return "client" in metric_name

    def test_csm_observability(self):
        # TODO(sergiitk): [GAMMA] Consider moving out custom gamma
        #   resource creation out of self.startTestServers()
        with self.subTest("1_run_test_server"):
            start_secs = int(time.time())
            test_server: _XdsTestServer = self.startTestServers(
                enable_csm_observability=True,
                csm_workload_name=_CSM_WORKLOAD_NAME_SERVER,
                csm_canonical_service_name=_CSM_CANONICAL_SERVICE_NAME_SERVER,
            )[0]

        with self.subTest("2_start_test_client"):
            test_client: _XdsTestClient = self.startTestClient(
                test_server,
                enable_csm_observability=True,
                request_payload_size=_REQUEST_PAYLOAD_SIZE,
                response_payload_size=_RESPONSE_PAYLOAD_SIZE,
                csm_workload_name=_CSM_WORKLOAD_NAME_CLIENT,
                csm_canonical_service_name=_CSM_CANONICAL_SERVICE_NAME_CLIENT,
            )
            logger.info(
                "Letting test client run for %d seconds", _TEST_RUN_SECS
            )
            time.sleep(_TEST_RUN_SECS)

        with self.subTest("3_test_server_received_rpcs_from_test_client"):
            self.assertSuccessfulRpcs(test_client)

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
            filter_str = (
                'metric.type = "%(metric)s" AND '
                'metric.labels.grpc_status = "OK" AND '
                'metric.labels.grpc_method = "%(grpc_method)s"'
            )
            histogram_results = self._query_metrics(
                _HISTOGRAM_METRICS, filter_str, interval
            )

            # The num rpcs started counter metrics do not have the
            # 'grpc_status' label
            filter_str = (
                'metric.type = "%(metric)s" AND '
                'metric.labels.grpc_method = "%(grpc_method)s"'
            )
            counter_results = self._query_metrics(
                _COUNTER_METRICS, filter_str, interval
            )

            all_results = {**histogram_results, **counter_results}

        with self.subTest("5_check_metrics_time_series"):
            for metric, time_series in all_results.items():
                # There should be exactly 1 time series per metric with these
                # label value combinations that we are expecting.
                self.assertEqual(1, len(time_series))

        # Testing whether each metric has the right set of metric label keys
        with self.subTest("6_check_metrics_label_keys"):
            for metric in _HISTOGRAM_CLIENT_METRICS:
                metric_labels = all_results[metric][0].metric.labels
                for label_key in [
                    "csm_mesh_id",
                    "csm_remote_workload_canonical_service",
                    "csm_remote_workload_cluster_name",
                    "csm_remote_workload_location",
                    "csm_remote_workload_name",
                    "csm_remote_workload_namespace_name",
                    "csm_remote_workload_project_id",
                    "csm_remote_workload_type",
                    "csm_service_name",
                    "csm_service_namespace_name",
                    "csm_workload_canonical_service",
                    "grpc_method",
                    "grpc_status",
                    "grpc_target",
                ]:
                    self.assertIn(label_key, metric_labels)

            for metric in _HISTOGRAM_SERVER_METRICS:
                metric_labels = all_results[metric][0].metric.labels
                for label_key in [
                    "csm_mesh_id",
                    "csm_remote_workload_canonical_service",
                    "csm_remote_workload_cluster_name",
                    "csm_remote_workload_location",
                    "csm_remote_workload_name",
                    "csm_remote_workload_namespace_name",
                    "csm_remote_workload_project_id",
                    "csm_remote_workload_type",
                    "csm_workload_canonical_service",
                    "grpc_method",
                    "grpc_status",
                ]:
                    self.assertIn(label_key, metric_labels)

            for metric in _COUNTER_CLIENT_METRICS:
                metric_labels = all_results[metric][0].metric.labels
                for label_key in [
                    "grpc_method",
                    "grpc_target",
                ]:
                    self.assertIn(label_key, metric_labels)

            for metric in _COUNTER_SERVER_METRICS:
                metric_labels = all_results[metric][0].metric.labels
                for label_key in [
                    "grpc_method",
                ]:
                    self.assertIn(label_key, metric_labels)

        # Testing whether each metric has the right set of monitored resource
        # label keys
        with self.subTest("7_check_resource_label_keys"):
            # all metrics should have the same set of monitored resource labels
            # which come from the GMP job
            for metric in _ALL_METRICS:
                resource_labels = all_results[metric][0].resource.labels
                for label_key in [
                    "cluster",
                    "instance",
                    "job",
                    "location",
                    "namespace",
                    "project_id",
                ]:
                    self.assertIn(label_key, resource_labels)

        # Testing the values of metric labels
        with self.subTest("8_check_metrics_label_values"):
            for metric, time_series in all_results.items():
                series = time_series[0]
                self.assertEqual(metric, series.metric.type)
                for label_key, label_value in series.metric.labels.items():
                    if label_key == "pod" and self._is_client_metric(metric):
                        # client pod name
                        self.assertEqual(test_client.hostname, label_value)
                    elif label_key == "pod" and self._is_server_metric(metric):
                        # server pod name
                        self.assertEqual(test_server.hostname, label_value)
                    elif label_key == "grpc_method":
                        self.assertEqual(_GRPC_METHOD_NAME, label_value)
                    elif label_key == "grpc_target":
                        # server namespace should be somewhere in the xds
                        # server target
                        self.assertIn(self.server_namespace, label_value)
                    elif (
                        label_key == "csm_remote_workload_canonical_service"
                        and self._is_client_metric(metric)
                    ):
                        # the remote workload canonical service name
                        # for the client is the one we set in the server
                        self.assertEqual(
                            _CSM_CANONICAL_SERVICE_NAME_SERVER, label_value
                        )
                    elif (
                        label_key == "csm_remote_workload_canonical_service"
                        and self._is_server_metric(metric)
                    ):
                        # the remote workload canonical service name
                        # for the server is the one we set in the client
                        self.assertEqual(
                            _CSM_CANONICAL_SERVICE_NAME_CLIENT, label_value
                        )
                    elif label_key == "csm_remote_workload_cluster_name":
                        # the cluster name should be somewhere in the
                        # kube context
                        self.assertIn(
                            label_value, xds_k8s_flags.KUBE_CONTEXT.value
                        )
                    elif label_key == "csm_remote_workload_location":
                        # the location should be somewhere in the kube context
                        self.assertIn(
                            label_value, xds_k8s_flags.KUBE_CONTEXT.value
                        )
                    elif (
                        label_key == "csm_remote_workload_name"
                        and self._is_client_metric(metric)
                    ):
                        # the remote workload name for the client is the name
                        # we set on the server
                        self.assertEqual(_CSM_WORKLOAD_NAME_SERVER, label_value)
                    elif (
                        label_key == "csm_remote_workload_name"
                        and self._is_server_metric(metric)
                    ):
                        # the remote workload name for the server is the name
                        # we set on the client
                        self.assertEqual(_CSM_WORKLOAD_NAME_CLIENT, label_value)
                    elif (
                        label_key == "csm_remote_workload_namespace_name"
                        and self._is_client_metric(metric)
                    ):
                        # the server namespace name
                        self.assertEqual(self.server_namespace, label_value)
                    elif (
                        label_key == "csm_remote_workload_namespace_name"
                        and self._is_server_metric(metric)
                    ):
                        # the client namespace name
                        self.assertEqual(self.client_namespace, label_value)
                    elif label_key == "csm_remote_workload_project_id":
                        self.assertEqual(self.project, label_value)
                    elif label_key == "csm_remote_workload_type":
                        # a hardcoded value
                        self.assertEqual("gcp_kubernetes_engine", label_value)
                    elif label_key == "csm_service_name":
                        # the service name
                        self.assertEqual(
                            self.server_runner.service_name, label_value
                        )
                    elif label_key == "csm_service_namespace_name":
                        # the server namespace name
                        self.assertEqual(self.server_namespace, label_value)
                    elif (
                        label_key == "csm_workload_canonical_service"
                        and self._is_client_metric(metric)
                    ):
                        # the client workload canonical service name
                        self.assertEqual(
                            _CSM_CANONICAL_SERVICE_NAME_CLIENT, label_value
                        )
                    elif (
                        label_key == "csm_workload_canonical_service"
                        and self._is_server_metric(metric)
                    ):
                        # the server workload canonical service name
                        self.assertEqual(
                            _CSM_CANONICAL_SERVICE_NAME_SERVER, label_value
                        )

        # Testing the values of monitored resource labels
        with self.subTest("9_check_resource_label_values"):
            for metric, time_series in all_results.items():
                series = time_series[0]
                self.assertEqual("prometheus_target", series.resource.type)
                for label_key, label_value in series.resource.labels.items():
                    if label_key == "project_id":
                        self.assertEqual(self.project, label_value)
                    elif label_key == "namespace" and self._is_client_metric(
                        metric
                    ):
                        # client namespace
                        self.assertEqual(self.client_namespace, label_value)
                    elif label_key == "namespace" and self._is_server_metric(
                        metric
                    ):
                        # server namespace
                        self.assertEqual(self.server_namespace, label_value)
                    elif label_key == "job" and self._is_client_metric(metric):
                        # the "job" label on the monitored resource refers to
                        # the GMP PodMonitoring resource name
                        self.assertEqual(
                            self.client_runner.pod_monitoring_name,
                            label_value,
                        )
                    elif label_key == "job" and self._is_server_metric(metric):
                        # the "job" label on the monitored resource refers to
                        # the GMP PodMonitoring resource name
                        self.assertEqual(
                            self.server_runner.pod_monitoring_name,
                            label_value,
                        )
                    elif label_key == "cluster":
                        self.assertIn(
                            label_value, xds_k8s_flags.KUBE_CONTEXT.value
                        )
                    elif label_key == "instance" and self._is_client_metric(
                        metric
                    ):
                        # the "instance" label on the monitored resource refers
                        # to the GKE pod and port
                        self.assertIn(test_client.hostname, label_value)
                    elif label_key == "instance" and self._is_server_metric(
                        metric
                    ):
                        # the "instance" label on the monitored resource refers
                        # to the GKE pod and port
                        self.assertIn(test_server.hostname, label_value)

        # This tests whether each of the "byes sent" histogram type metric
        # should have at least 1 data point whose mean should converge to be
        # close to the number of bytes being sent by the RPCs.
        with self.subTest("10_check_bytes_sent_vs_data_points"):
            for metric in [
                _METRIC_CLIENT_ATTEMPT_SENT,
                _METRIC_SERVER_CALL_RCVD,
            ]:
                self.assertTrue(
                    self._at_least_one_point_within_range(
                        all_results[metric][0].points, _REQUEST_PAYLOAD_SIZE
                    )
                )

            for metric in [
                _METRIC_CLIENT_ATTEMPT_RCVD,
                _METRIC_SERVER_CALL_SENT,
            ]:
                self.assertTrue(
                    self._at_least_one_point_within_range(
                        all_results[metric][0].points, _RESPONSE_PAYLOAD_SIZE
                    )
                )


if __name__ == "__main__":
    absltest.main()
