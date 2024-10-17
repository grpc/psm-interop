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
Configure Traffic Director for different GRPC Proxyless.

This is intended as a debugging / local development helper and not executed
as a part of interop test suites.

Typical usage examples:

    # Regular proxyless setup.
    ./run.sh ./bin/run_td_setup.py

    # Additional commands: cleanup, backend management, etc.
    ./run.sh ./bin/run_td_setup.py --cmd=cleanup
    ./run.sh ./bin/run_td_setup.py --cmd=backends-add

    # PSM security setup options: mtls, tls, etc.
    ./run.sh ./bin/run_td_setup.py --mode=secure --security=mtls
    ./run.sh ./bin/run_td_setup.py --mode=secure --security=mtls --cmd=cleanup

    # AppNet mode.
    ./run.sh ./bin/run_td_setup.py --mode=app_net
    ./run.sh ./bin/run_td_setup.py --mode=app_net --cmd=backends-add
    ./run.sh ./bin/run_td_setup.py --mode=app_net --cmd=cleanup

    # Gamma mode - does nothing, just for compatibility with other bin scripts.
    ./run.sh ./bin/run_td_setup.py --mode=gamma

    # More information and usage options.
    ./run.sh ./bin/run_td_setup.py --help
    ./run.sh ./bin/run_td_setup.py --helpfull
"""
import logging

from absl import app
from absl import flags

from bin.lib import common
from framework import xds_flags
from framework import xds_k8s_flags
from framework.helpers import rand
from framework.infrastructure import k8s
from framework.infrastructure import traffic_director
from framework.test_app.runners.k8s import k8s_xds_server_runner

logger = logging.getLogger(__name__)
# Flags
_CMD = flags.DEFINE_enum(
    "cmd",
    default="create",
    enum_values=[
        "cycle",
        "create",
        "cleanup",
        "backends-add",
        "backends-cleanup",
        "unused-xds-port",
    ],
    help="Command",
)
flags.adopt_module_key_flags(xds_flags)
flags.adopt_module_key_flags(xds_k8s_flags)

# Flag validations.


@flags.multi_flags_validator(
    (xds_flags.SERVER_XDS_PORT, _CMD),
    "Run outside of a test suite, must provide the exact port value (must be "
    "greater than 0).",
)
def _check_server_xds_port_flag(flag_values):
    if flag_values[_CMD.name] not in ("create", "cycle"):
        return True
    return flag_values[xds_flags.SERVER_XDS_PORT.name] > 0


# Type aliases
_KubernetesServerRunner = k8s_xds_server_runner.KubernetesServerRunner


def _setup_td_secure(
    security_mode,
    *,
    td: traffic_director.TrafficDirectorSecureManager,
    server_name,
    server_namespace,
    server_port,
    server_maintenance_port,
    server_xds_host,
    server_xds_port,
):
    if security_mode == "mtls":
        logger.info("Setting up mtls")
        td.setup_for_grpc(
            server_xds_host,
            server_xds_port,
            health_check_port=server_maintenance_port,
        )
        td.setup_server_security(
            server_namespace=server_namespace,
            server_name=server_name,
            server_port=server_port,
            tls=True,
            mtls=True,
        )
        td.setup_client_security(
            server_namespace=server_namespace,
            server_name=server_name,
            tls=True,
            mtls=True,
        )
    elif security_mode == "tls":
        logger.info("Setting up tls")
        td.setup_for_grpc(
            server_xds_host,
            server_xds_port,
            health_check_port=server_maintenance_port,
        )
        td.setup_server_security(
            server_namespace=server_namespace,
            server_name=server_name,
            server_port=server_port,
            tls=True,
            mtls=False,
        )
        td.setup_client_security(
            server_namespace=server_namespace,
            server_name=server_name,
            tls=True,
            mtls=False,
        )
    elif security_mode == "plaintext":
        logger.info("Setting up plaintext")
        td.setup_for_grpc(
            server_xds_host,
            server_xds_port,
            health_check_port=server_maintenance_port,
        )
        td.setup_server_security(
            server_namespace=server_namespace,
            server_name=server_name,
            server_port=server_port,
            tls=False,
            mtls=False,
        )
        td.setup_client_security(
            server_namespace=server_namespace,
            server_name=server_name,
            tls=False,
            mtls=False,
        )
    elif security_mode == "mtls_error":
        # Error case: server expects client mTLS cert,
        # but client configured only for TLS
        logger.info("Setting up mtls_error")
        td.setup_for_grpc(
            server_xds_host,
            server_xds_port,
            health_check_port=server_maintenance_port,
        )
        td.setup_server_security(
            server_namespace=server_namespace,
            server_name=server_name,
            server_port=server_port,
            tls=True,
            mtls=True,
        )
        td.setup_client_security(
            server_namespace=server_namespace,
            server_name=server_name,
            tls=True,
            mtls=False,
        )
    elif security_mode == "server_authz_error":
        # Error case: client does not authorize server
        # because of mismatched SAN name.
        logger.info("Setting up mtls_error")
        td.setup_for_grpc(
            server_xds_host,
            server_xds_port,
            health_check_port=server_maintenance_port,
        )
        # Regular TLS setup, but with client policy configured using
        # intentionality incorrect server_namespace.
        td.setup_server_security(
            server_namespace=server_namespace,
            server_name=server_name,
            server_port=server_port,
            tls=True,
            mtls=False,
        )
        td.setup_client_security(
            server_namespace=f"incorrect-namespace-{rand.rand_string()}",
            server_name=server_name,
            tls=True,
            mtls=False,
        )


def _setup_td_appnet(
    *,
    td: traffic_director.TrafficDirectorAppNetManager,
    server_xds_host,
    server_xds_port,
):
    td.create_health_check()
    td.create_backend_service()
    td.create_mesh()
    td.create_grpc_route(server_xds_host, server_xds_port)


def _cmd_backends_add(td, server_name, server_namespace, server_port):
    logger.info("Adding backends")
    k8s_api_manager = k8s.KubernetesApiManager(xds_k8s_flags.KUBE_CONTEXT.value)
    k8s_namespace = k8s.KubernetesNamespace(k8s_api_manager, server_namespace)
    neg_name, neg_zones = k8s_namespace.parse_service_neg_status(
        server_name, server_port
    )
    td.load_backend_service()
    td.backend_service_add_neg_backends(neg_name, neg_zones)
    td.wait_for_backends_healthy_status(
        replica_count=common.SERVER_REPLICA_COUNT.value
    )


def _cmd_backends_cleanup(td):
    logger.info("Adding cleaning up the backends")
    td.load_backend_service()
    td.backend_service_remove_all_backends()


def _cmd_unused_port(td):
    try:
        unused_xds_port = td.find_unused_forwarding_rule_port()
        logger.info("Found unused forwarding rule port: %s", unused_xds_port)
    except Exception:  # noqa pylint: disable=broad-except
        logger.exception("Couldn't find unused forwarding rule port")


def main(
    argv,
):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    # Must be called before KubernetesApiManager or GcpApiManager init.
    xds_flags.set_socket_default_timeout_from_flag()

    # Flags.
    command = _CMD.value
    security_mode = common.SECURITY.value
    if security_mode:
        flags.set_default(common.MODE, "secure")

    mode = common.MODE.value

    # Short circuit for gamma node.
    if mode == "gamma":
        logger.info("Traffic Director not needed for gamma setup")
        return

    # Resource names.
    resource_prefix: str = xds_flags.RESOURCE_PREFIX.value
    resource_suffix: str = xds_flags.RESOURCE_SUFFIX.value

    # Test server
    server_name = xds_flags.SERVER_NAME.value
    server_port = xds_flags.SERVER_PORT.value
    server_maintenance_port = xds_flags.SERVER_MAINTENANCE_PORT.value
    server_xds_host = xds_flags.SERVER_XDS_HOST.value
    server_xds_port = xds_flags.SERVER_XDS_PORT.value
    server_namespace_name = _KubernetesServerRunner.make_namespace_name(
        resource_prefix, resource_suffix
    )

    td_attrs = common.td_attrs()
    if mode == "app_net":
        td = traffic_director.TrafficDirectorAppNetManager(**td_attrs)
    elif mode == "secure":
        td = traffic_director.TrafficDirectorSecureManager(**td_attrs)
        if server_maintenance_port is None:
            server_maintenance_port = (
                _KubernetesServerRunner.DEFAULT_SECURE_MODE_MAINTENANCE_PORT
            )
    else:
        td = traffic_director.TrafficDirectorManager(**td_attrs)

    if command in ("create", "cycle"):
        logger.info("Setting up Traffic Director, mode=%s", mode)
        try:
            if mode == "app_net":
                _setup_td_appnet(
                    td=td,
                    server_xds_host=server_xds_host,
                    server_xds_port=server_xds_port,
                )
            elif mode == "secure":
                _setup_td_secure(
                    security_mode,
                    td=td,
                    server_name=server_name,
                    server_namespace=server_namespace_name,
                    server_port=server_port,
                    server_maintenance_port=server_maintenance_port,
                    server_xds_host=server_xds_host,
                    server_xds_port=server_xds_port,
                )
            else:
                td.setup_for_grpc(
                    server_xds_host,
                    server_xds_port,
                    health_check_port=server_maintenance_port,
                )

            logger.info("Done!")
        except Exception:  # noqa pylint: disable=broad-except
            logger.exception("Got error during creation")

    if command in ("cleanup", "cycle"):
        logger.info("Cleaning up")
        td.cleanup(force=True)
        return

    if command == "backends-add":
        _cmd_backends_add(td, server_name, server_namespace_name, server_port)
    elif command == "backends-cleanup":
        _cmd_backends_cleanup(td)
    elif command == "unused-xds-port":
        _cmd_unused_port(td)


if __name__ == "__main__":
    app.run(main)
