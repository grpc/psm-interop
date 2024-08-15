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

import datetime
import logging
import math
import pathlib
import threading
import time

import grpc
from grpc_channelz.v1 import channelz_pb2
import mako.template

from docker import client
from docker import errors
from docker import types
from framework.rpc.grpc_channelz import ChannelzServiceClient
from protos.grpc.testing import messages_pb2
from protos.grpc.testing import test_pb2_grpc
from protos.grpc.testing.xdsconfig import xdsconfig_pb2
from protos.grpc.testing.xdsconfig import xdsconfig_pb2_grpc

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
    def __init__(
        self,
        base: pathlib.Path,
        primary_port: int,
        fallback_port: int,
        host_name: str,
    ):
        self.primary_port = primary_port
        self.fallback_port = fallback_port
        self.mount_dir = _make_working_dir(base)
        # Use Mako
        template = mako.template.Template(filename=BOOTSTRAP_JSON_TEMPLATE)
        file = template.render(
            servers=[
                f"{host_name}:{primary_port}",
                f"{host_name}:{fallback_port}",
            ]
        )
        destination = self.mount_dir / "bootstrap.json"
        with open(destination, "w", encoding="utf-8") as f:
            f.write(file)
            logger.debug("Generated bootstrap file at %s", destination)


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
    ):
        self.docker_client = client.DockerClient.from_env()
        self.node_id = node_id
        self.bootstrap = bootstrap


def _Sanitize(l: str) -> str:
    if l.find("\0") < 0:
        return l
    return l.replace("\0", "�")


def Configure(config, image: str, name: str):
    config["detach"] = True
    config["environment"] = {
        "GRPC_EXPERIMENTAL_XDS_FALLBACK": "true",
        "GRPC_TRACE": "xds_client",
        "GRPC_VERBOSITY": "info",
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
        **config: types.ContainerConfig,
    ):
        self.name = name
        self.config = Configure(config, image, name)
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
        except errors.NotFound:
            # It is ok, container was auto removed
            logger.debug(
                "Container %s was autoremoved, most likely because the app crashed",
                self.name,
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
        initial_resources: xdsconfig_pb2.SetResourcesRequest,
        image: str,
    ):
        super().__init__(
            manager=manager,
            name=name,
            port=port,
            image=image,
            ports={DEFAULT_CONTROL_PLANE_PORT: port},
            command=["--nodeid", manager.node_id],
        )
        self.initial_resources = initial_resources

    def __enter__(self):
        if not super().__enter__():
            return None
        self.update_resources(self.initial_resources)
        return self

    def stop_on_resource_request(self, resource_type: str, resource_name: str):
        stub = xdsconfig_pb2_grpc.XdsConfigControlServiceStub(self.channel())
        res = stub.StopOnRequest(
            xdsconfig_pb2.StopOnRequestRequest(
                type_url=resource_type, name=resource_name
            )
        )
        return res

    def update_resources(self, resources: xdsconfig_pb2.SetResourcesRequest):
        stub = xdsconfig_pb2_grpc.XdsConfigControlServiceStub(self.channel())
        return stub.SetResources(resources)


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
            command=[
                "--server",
                url,
                # "--print_response",
                # "true",
                # "--verbose",
                "--stats_port",
                str(port),
            ],
            ports={str(port): port},
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
                num_rpcs=num_rpcs, timeout_sec=math.ceil(num_rpcs * 10)
            )
        )
        return res

    def expect_channel_status(
        self,
        port: int,
        expected_status: channelz_pb2.ChannelConnectivityState,
        timeout: datetime.timedelta,
        poll_interval: datetime.timedelta,
    ) -> channelz_pb2.ChannelConnectivityState:
        deadline = datetime.datetime.now() + timeout
        channelz = ChannelzServiceClient(self.channel())
        status = None
        while datetime.datetime.now() < deadline:
            status = None
            for ch in channelz.list_channels():
                if ch.data.target.endswith(str(port)):
                    status = ch.data.state.state
                    break
            if status == expected_status:
                return status
            time.sleep(poll_interval.microseconds * 0.000001)
        return status
