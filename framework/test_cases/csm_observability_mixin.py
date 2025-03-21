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
"""
A mixin class for CSM Observability tests.

This mixin is shared between test environments that exports CSM
Observability metrics.
"""
from framework.test_cases import testcase_mixins

class CsmObservabilityMixin(testcase_mixins.CsmObservabilityTestCaseMixin):

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
