# Copyright 2020 gRPC authors.
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
Run test xds server.

Typical usage examples:

    # Help.
    ./run.sh ./bin/run_test_server.py --help

    # Run modes.
    ./run.sh ./bin/run_test_server.py --mode=app_net
    ./run.sh ./bin/run_test_server.py --mode=secure

    # Gamma run mode: uses HTTPRoute by default.
    ./run.sh ./bin/run_test_server.py --mode=gamma

    # Gamma run mode: use GRPCRoute.
    ./run.sh ./bin/run_test_server.py --mode=gamma --gamma_route_kind=grpc

    # Running multipler server replicas.
    ./run.sh ./bin/run_test_server.py --server_replica_count=3

    # Cleanup: make sure to set the same mode used to create.
    ./run.sh ./bin/run_test_server.py --mode=gamma --cmd=cleanup
"""
import logging
import signal

from absl import app
from absl import flags

from bin.lib import common
from framework import xds_flags
from framework import xds_k8s_flags
from framework.infrastructure import k8s

logger = logging.getLogger(__name__)
# Flags
_CMD = flags.DEFINE_enum(
    "cmd", default="run", enum_values=["run", "cleanup"], help="Command"
)
_REUSE_NAMESPACE = flags.DEFINE_bool(
    "reuse_namespace", default=True, help="Use existing namespace if exists"
)
_REUSE_SERVICE = flags.DEFINE_bool(
    "reuse_service", default=False, help="Use existing service if exists"
)
_FOLLOW = flags.DEFINE_bool(
    "follow", default=False, help="Follow pod logs. Requires --collect_app_logs"
)
_CLEANUP_NAMESPACE = flags.DEFINE_bool(
    "cleanup_namespace",
    default=False,
    help="Delete namespace during resource cleanup",
)
flags.adopt_module_key_flags(xds_flags)
flags.adopt_module_key_flags(xds_k8s_flags)
flags.adopt_module_key_flags(common)


def _make_sigint_handler(server_runner: common.KubernetesServerRunner):
    def sigint_handler(sig, frame):
        del sig, frame
        print("Caught Ctrl+C. Shutting down the logs")
        server_runner.stop_pod_dependencies(log_drain_sec=3)

    return sigint_handler


def _get_run_kwargs(mode: str):
    run_kwargs = dict(
        test_port=xds_flags.SERVER_PORT.value,
        replica_count=common.SERVER_REPLICA_COUNT.value,
        maintenance_port=xds_flags.SERVER_MAINTENANCE_PORT.value,
        log_to_stdout=_FOLLOW.value,
    )
    if mode == "secure":
        run_kwargs["secure_mode"] = True
    elif mode == "gamma":
        run_kwargs["generate_mesh_id"] = True
        if common.ROUTE_KIND_GAMMA.value is k8s.RouteKind.HTTP:
            run_kwargs["route_template"] = "gamma/route_http.yaml"
        elif common.ROUTE_KIND_GAMMA.value is k8s.RouteKind.GRPC:
            run_kwargs["route_template"] = "gamma/route_grpc.yaml"

    return run_kwargs


def main(argv):
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    # Must be called before KubernetesApiManager or GcpApiManager init.
    xds_flags.set_socket_default_timeout_from_flag()

    # Flags.
    command: str = _CMD.value
    mode: str = common.MODE.value
    # Flags: log following and port forwarding.
    should_follow_logs = _FOLLOW.value and xds_flags.COLLECT_APP_LOGS.value
    should_port_forward = (
        should_follow_logs and xds_k8s_flags.DEBUG_USE_PORT_FORWARDING.value
    )
    enable_workload_identity: bool = (
        xds_k8s_flags.ENABLE_WORKLOAD_IDENTITY.value
    )

    # Setup.
    server_runner = common.make_server_runner(
        common.make_server_namespace(),
        mode=mode,
        reuse_namespace=_REUSE_NAMESPACE.value,
        reuse_service=_REUSE_SERVICE.value,
        port_forwarding=should_port_forward,
        enable_workload_identity=enable_workload_identity,
    )

    if command == "run":
        logger.info("Run server, mode=%s", mode)
        run_kwargs = _get_run_kwargs(mode=mode)
        server_runner.run(**run_kwargs)
        if should_follow_logs:
            print("Following pod logs. Press Ctrl+C top stop")
            signal.signal(signal.SIGINT, _make_sigint_handler(server_runner))
            signal.pause()

    elif command == "cleanup":
        logger.info("Cleanup server")
        server_runner.cleanup(
            force=True, force_namespace=_CLEANUP_NAMESPACE.value
        )


if __name__ == "__main__":
    app.run(main)
