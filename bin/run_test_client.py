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
Run test xds client.

Gamma example:
./run.sh bin/run_test_client.py --server_xds_host=psm-grpc-server \
    --server_xds_port=80 \
    --config_mesh=gketd-psm-grpc-server
"""


import logging
import signal

from absl import app
from absl import flags

from bin.lib import common
from framework import xds_flags
from framework import xds_k8s_flags
from framework.infrastructure import gcp
from framework.infrastructure import k8s

logger = logging.getLogger(__name__)
# Flags
_CMD = flags.DEFINE_enum(
    "cmd", default="run", enum_values=["run", "cleanup"], help="Command"
)
_MODE = flags.DEFINE_enum(
    "mode",
    default="default",
    enum_values=[
        "default",
        "secure",
        "gamma",
    ],
    help="Select client mode",
)
_QPS = flags.DEFINE_integer("qps", default=25, help="Queries per second")
_PRINT_RESPONSE = flags.DEFINE_bool(
    "print_response", default=False, help="Client prints responses"
)
_FOLLOW = flags.DEFINE_bool(
    "follow",
    default=False,
    help=(
        "Follow pod logs. Requires --collect_app_logs or"
        " --debug_use_port_forwarding"
    ),
)
_CONFIG_MESH = flags.DEFINE_string(
    "config_mesh",
    default=None,
    help="Optional. Supplied to bootstrap generator to indicate AppNet mesh.",
)
_REUSE_NAMESPACE = flags.DEFINE_bool(
    "reuse_namespace", default=True, help="Use existing namespace if exists"
)
_CLEANUP_NAMESPACE = flags.DEFINE_bool(
    "cleanup_namespace",
    default=False,
    help="Delete namespace during resource cleanup",
)
flags.adopt_module_key_flags(xds_flags)
flags.adopt_module_key_flags(xds_k8s_flags)
# Running outside of a test suite, so require explicit resource_suffix.
flags.mark_flag_as_required(xds_flags.RESOURCE_SUFFIX.name)


@flags.multi_flags_validator(
    (xds_flags.SERVER_XDS_PORT, _CMD, _MODE),
    message=(
        "Run outside of a test suite, must provide"
        " the exact port value (must be greater than 0)."
    ),
)
def _check_server_xds_port_flag(flags_dict):
    if flags_dict[_MODE.name] == "gamma" or flags_dict[_CMD.name] == "cleanup":
        return True
    return flags_dict[xds_flags.SERVER_XDS_PORT.name] > 0


def _make_sigint_handler(client_runner: common.KubernetesClientRunner):
    def sigint_handler(sig, frame):
        del sig, frame
        print("Caught Ctrl+C. Shutting down the logs")
        client_runner.stop_pod_dependencies(log_drain_sec=3)

    return sigint_handler


def _get_run_kwargs(mode: str, server_runner=None):
    run_kwargs = dict(
        qps=_QPS.value,
        print_response=_PRINT_RESPONSE.value,
        config_mesh=_CONFIG_MESH.value,
        log_to_stdout=_FOLLOW.value,
    )
    if mode == "secure":
        run_kwargs["secure_mode"] = True

    if mode == "gamma":
        run_kwargs["generate_mesh_id"] = True
        server_target = (
            f"xds:///{server_runner.frontend_service_name}"
            f".{server_runner.k8s_namespace.name}.svc.cluster.local"
            f":{server_runner.DEFAULT_TEST_PORT}"
        )
    else:
        server_target = f"xds:///{xds_flags.SERVER_XDS_HOST.value}"
        if xds_flags.SERVER_XDS_PORT.value != 80:
            server_target = f"{server_target}:{xds_flags.SERVER_XDS_PORT.value}"

    # Server target in Gamma/CSM generated differently.
    run_kwargs["server_target"] = server_target

    return run_kwargs


def main(argv):
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    # Must be called before KubernetesApiManager or GcpApiManager init.
    xds_flags.set_socket_default_timeout_from_flag()

    # Flags.
    mode: str = _MODE.value
    command: str = _CMD.value
    # Flags: log following and port forwarding.
    should_follow_logs = _FOLLOW.value and xds_flags.COLLECT_APP_LOGS.value
    should_port_forward = (
        should_follow_logs and xds_k8s_flags.DEBUG_USE_PORT_FORWARDING.value
    )
    enable_workload_identity: bool = (
        xds_k8s_flags.ENABLE_WORKLOAD_IDENTITY.value
    )

    # Setup.
    gcp_api_manager = gcp.api.GcpApiManager()
    k8s_api_manager = k8s.KubernetesApiManager(xds_k8s_flags.KUBE_CONTEXT.value)
    client_namespace = common.make_client_namespace(k8s_api_manager)
    client_runner = common.make_client_runner(
        client_namespace,
        gcp_api_manager,
        mode=mode,
        reuse_namespace=_REUSE_NAMESPACE.value,
        port_forwarding=should_port_forward,
        enable_workload_identity=enable_workload_identity,
    )

    if mode == "gamma":
        # Minimal server runner just so it's possible to generate the target
        server_runner = common.make_server_runner(
            common.make_server_namespace(k8s_api_manager),
            gcp_api_manager,
            mode=mode,
        )
    else:
        server_runner = None

    if command == "run":
        logger.info("Run client, mode=%s", mode)
        run_kwargs = _get_run_kwargs(mode=mode, server_runner=server_runner)
        client_runner.run(**run_kwargs)
        if should_follow_logs:
            print("Following pod logs. Press Ctrl+C top stop")
            signal.signal(signal.SIGINT, _make_sigint_handler(client_runner))
            signal.pause()

    elif command == "cleanup":
        logger.info("Cleanup client")
        client_runner.cleanup(
            force=True, force_namespace=_CLEANUP_NAMESPACE.value
        )


if __name__ == "__main__":
    app.run(main)
