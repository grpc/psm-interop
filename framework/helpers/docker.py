from collections import defaultdict
import datetime
import logging
import math
import pathlib
import queue
import threading

from docker.client import DockerClient
from docker.errors import NotFound
from docker.types import ContainerConfig
import grpc
import mako.template

from protos.grpc.testing import messages_pb2
from protos.grpc.testing import test_pb2_grpc
from protos.grpc.testing.xdsconfig.control_pb2 import StopOnRequestRequest
from protos.grpc.testing.xdsconfig.control_pb2 import UpsertResourcesRequest
from protos.grpc.testing.xdsconfig.service_pb2_grpc import (
    XdsConfigControlServiceStub,
)

# bootstrap.json template
BOOTSTRAP_JSON_TEMPLATE = "templates/bootstrap.json"
DEFAULT_CONTROL_PLANE_PORT = 3333
DEFAULT_GRPC_CLIENT_PORT = 50052

logger = logging.getLogger(__name__)


def _make_working_dir(base: pathlib.Path) -> str:
    # Date time to string
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    mount_dir = base / f"testrun_{run_id}"
    if not mount_dir.exists():
        logger.debug("Creating %s", mount_dir)
        mount_dir.mkdir(parents=True)
    return mount_dir.absolute()


class Bootstrap:

    def __init__(self, base: pathlib.Path, ports: list[int], host_name: str):
        self.ports = ports
        self.mount_dir = _make_working_dir(base)
        # Use Mako
        template = mako.template.Template(filename=BOOTSTRAP_JSON_TEMPLATE)
        file = template.render(
            servers=[f"{host_name}:{port}" for port in self.ports]
        )
        destination = self.mount_dir / "bootstrap.json"
        with open(destination, "w", encoding="utf-8") as f:
            f.write(file)
            logger.debug("Generated bootstrap file at %s", destination)

    def xds_config_server_port(self, server_id: int):
        return self.ports[server_id]


class ChildProcessEvent:

    def __init__(self, source: str, data: str):
        self.source = source
        self.data = data

    def __str__(self) -> str:
        return f"{self.data}, {self.source}"

    def __repr__(self) -> str:
        return f"data={self.data}, source={self.source}"


class ProcessManager:
    def __init__(
        self,
        bootstrap: Bootstrap,
        node_id: str,
        verbosity="info",
    ):
        self.docker_client = DockerClient.from_env()
        self.node_id = node_id
        self.outputs = defaultdict(list)
        self.queue = queue.Queue()
        self.bootstrap = bootstrap
        self.verbosity = verbosity

    def next_event(self, timeout: int) -> ChildProcessEvent:
        event: ChildProcessEvent = self.queue.get(timeout=timeout)
        source = event.source
        message = event.data
        self.outputs[source].append(message)
        return event

    def expect_output(
        self, process_name: str, expected_message: str, timeout_s: int
    ) -> bool:
        """
        Checks if the specified message appears in the output of a given process within a timeout.

        Returns:
            True if the expected message is found in the process's output within the timeout, False otherwise.

        Behavior:
            - If the process has already produced output, it checks there first.
            - Otherwise, it waits for new events from the process, up to the specified timeout.
            - If an event from the process contains the expected message, it returns True.
            - If the timeout is reached without finding the message, it returns False.
        """
        logger.debug(
            'Waiting for message "%s" from %s', expected_message, process_name
        )
        if any(
            m
            for m in self.outputs[process_name]
            if m.find(expected_message) >= 0
        ):
            return True
        deadline = datetime.datetime.now() + datetime.timedelta(
            seconds=timeout_s
        )
        while datetime.datetime.now() <= deadline:
            event = self.next_event(timeout_s)
            if (
                event.source == process_name
                and event.data.find(expected_message) >= 0
            ):
                return True
        return False

    def on_message(self, source: str, message: str):
        self.queue.put(ChildProcessEvent(source, message))


def _Sanitize(l: str) -> str:
    if l.find("\0") < 0:
        return l
    return l.replace("\0", "�")


def Configure(config, image: str, name: str, verbosity: str):
    config["detach"] = True
    config["environment"] = {
        "GRPC_EXPERIMENTAL_XDS_FALLBACK": "true",
        "GRPC_TRACE": "xds_client",
        "GRPC_VERBOSITY": verbosity,
        "GRPC_XDS_BOOTSTRAP": "/grpc/bootstrap.json",
    }
    config["extra_hosts"] = {"host.docker.internal": "host-gateway"}
    config["image"] = image
    config["hostname"] = name
    config["remove"] = True
    return config


class DockerProcess:
    def __init__(
        self,
        image: str,
        name: str,
        manager: ProcessManager,
        **config: ContainerConfig,
    ):
        self.name = name
        self.config = Configure(
            config, image=image, name=name, verbosity=manager.verbosity
        )
        self.container = None
        self.manager = manager
        self.thread = None

    def __enter__(self):
        self.container = self.manager.docker_client.containers.run(
            **self.config
        )
        self.thread = threading.Thread(
            target=lambda process: process.log_reader_loop(),
            args=(self,),
            daemon=True,
        )
        self.thread.start()
        return self

    def exit(self):
        try:
            self.container.stop()
            self.container.wait()
        except NotFound:
            # It is ok, container was auto removed
            logger.debug(
                f"Container {self.name} was autoremoved, most likely because "
                "the app crashed"
            )
        finally:
            self.thread.join()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit()

    def log_reader_loop(self):
        # We only process full strings that end in '\n'. Incomplete strings are
        # stored in the prefix.
        prefix = ""
        for log in self.container.logs(stream=True):
            s = str(prefix + log.decode("utf-8"))
            prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
            for l in s[: s.rfind("\n")].splitlines():
                message = _Sanitize(l)
                logger.info("[%s] %s", self.name, message)
                self.manager.on_message(self.name, message)


class GrpcProcess:

    def __init__(
        self,
        manager: ProcessManager,
        name: str,
        port: int,
        ports: dict[int, int],
        image: str,
        command: list[str],
        volumes=None,
    ):
        self.docker_process = DockerProcess(
            image,
            name,
            manager,
            command=" ".join(command),
            hostname=name,
            ports=ports,
            volumes=volumes or {},
        )
        self.manager = manager
        self.port = port
        self.grpc_channel: grpc.Channel = None

    def __enter__(self):
        self.docker_process.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.grpc_channel:
            self.grpc_channel.close()
        self.docker_process.exit()

    def expect_message_in_output(
        self, expected_message: str, timeout_s: int = 5
    ) -> bool:
        return self.manager.expect_output(
            self.docker_process.name, expected_message, timeout_s
        )

    def channel(self) -> grpc.Channel:
        if self.grpc_channel is None:
            self.grpc_channel = grpc.insecure_channel(f"localhost:{self.port}")
        return self.grpc_channel


class ControlPlane(GrpcProcess):
    def __init__(
        self,
        manager: ProcessManager,
        name: str,
        port: int,
        upstream: str,
        image: str,
    ):
        super().__init__(
            manager=manager,
            name=name,
            port=port,
            image=image,
            ports={DEFAULT_CONTROL_PLANE_PORT: port},
            command=["--upstream", str(upstream), "--node", manager.node_id],
        )

    def stop_on_resource_request(self, resource_type: str, resource_name: str):
        stub = XdsConfigControlServiceStub(self.channel())
        res = stub.StopOnRequest(
            StopOnRequestRequest(
                resource_type=resource_type, resource_name=resource_name
            )
        )
        return res

    def update_resources(
        self, cluster: str, upstream_port: int, upstream_host="localhost"
    ):
        stub = XdsConfigControlServiceStub(self.channel())
        return stub.UpsertResources(
            UpsertResourcesRequest(
                cluster=cluster,
                upstream_host=upstream_host,
                upstream_port=upstream_port,
            )
        )


class Client(GrpcProcess):
    def __init__(
        self,
        manager: ProcessManager,
        port: int,
        name: str,
        url: str,
        image: str,
    ):
        super().__init__(
            manager=manager,
            port=port,
            image=image,
            name=name,
            command=[f"--server={url}", "--print_response"],
            ports={DEFAULT_GRPC_CLIENT_PORT: port},
            volumes={
                manager.bootstrap.mount_dir: {
                    "bind": "/grpc",
                    "mode": "ro",
                }
            },
        )

    def get_stats(self, num_rpcs: int):
        logger.debug("Sending %d requests", num_rpcs)
        stub = test_pb2_grpc.LoadBalancerStatsServiceStub(self.channel())
        res = stub.GetClientStats(
            messages_pb2.LoadBalancerStatsRequest(
                num_rpcs=num_rpcs, timeout_sec=math.ceil(num_rpcs * 1.5)
            )
        )
        return res
