from datetime import datetime, timedelta
from math import ceil
from pathlib import Path, PosixPath
from queue import Queue
from threading import Thread
from typing import List

from absl import logging

from docker import DockerClient
import docker
import grpc

from protos.grpc.testing import messages_pb2
from protos.grpc.testing import test_pb2_grpc
from protos.grpc.testing.xdsconfig import (
    control_pb2,
    service_pb2_grpc,
)
from mako.template import Template


class Bootstrap:

    def __init__(self, base: str, ports: List[int], host_name: str):
        self.__base = PosixPath(base)
        self.__host_name = host_name
        self.__ports = ports
        self.__mount_dir: Path = None
        self._MakeWorkingDir(self.__base)
        # Use Mako
        template = Template(filename="templates/bootstrap.mako")
        file = template.render(
            servers=[f"{self.__host_name}:{port}" for port in self.__ports]
        )
        destination = self.mount_dir() / "bootstrap.json"
        with open(destination, "w") as f:
            f.write(file)
            logging.debug(f"Generated bootstrap file at %s", destination)

    def _MakeWorkingDir(self, base: str):
        for i in range(100):
            # Date time to string
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            id = f"_{i}" if i > 0 else ""
            self.__mount_dir = base / f"testrun_{run_id}{id}"
            if not self.__mount_dir.exists():
                logging.debug(f"Creating %s", self.__mount_dir)
                self.mount_dir().mkdir(parents=True)
                return
        raise Exception("Couldn't find a free working directory")

    def mount_dir(self) -> Path:
        if self.__mount_dir == None:
            raise RuntimeError("Working dir was not created yet")
        return self.__mount_dir.absolute()

    def xds_config_server_port(self, n: int):
        return self.__ports[n]


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
        nodeId: str,
        verbosity="info",
    ):
        self.dockerClient = DockerClient.from_env()
        self.nodeId = nodeId
        self.__outputs = {}
        self.__queue = Queue()
        self.__bootstrap = bootstrap
        self.verbosity = verbosity

    def NextEvent(self, timeout: int) -> ChildProcessEvent:
        event: ChildProcessEvent = self.__queue.get(timeout=timeout)
        source = event.source
        message = event.data
        logging.info(f"[%s] %s", source, message)
        if not source in self.__outputs:
            self.__outputs[source] = []
        self.__outputs[source].append(message)
        return event

    def ExpectOutput(self, source: str, message: str, timeout_s=5) -> bool:
        logging.debug(f'Waiting for message "%s" on %s', message, source)
        if source in self.__outputs:
            for m in self.__outputs[source]:
                if m.find(message) >= 0:
                    return True
        deadline = datetime.now() + timedelta(seconds=timeout_s)
        while datetime.now() <= deadline:
            event = self.NextEvent(timeout_s)
            if event.source == source and event.data.find(message) >= 0:
                return True
        return False

    def OnMessage(self, source: str, message: str):
        self.__queue.put(ChildProcessEvent(source, message))

    def mount_dir(self):
        return self.__bootstrap.mount_dir()


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
        **config: docker.types.ContainerConfig,
    ):
        self.__manager = manager
        self.__container = None
        self.name = name
        self.__config = Configure(
            config, image=image, name=name, verbosity=manager.verbosity
        )

    def __enter__(self):
        self.__container = self.__manager.dockerClient.containers.run(**self.__config)
        self.__thread = Thread(
            target=lambda process: process.LogReaderLoop(),
            args=(self,),
        )
        self.__thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.__container.stop()
            self.__container.wait()
        except docker.errors.NotFound:
            # Ok, container was auto removed
            pass
        finally:
            self.__thread.join()

    def LogReaderLoop(self):
        prefix = ""
        for log in self.__container.logs(stream=True):
            s = str(prefix + log.decode("utf-8"))
            prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
            for l in s[: s.rfind("\n")].splitlines():
                self.__manager.OnMessage(self.name, _Sanitize(l))

class GrpcProcess:

    def __init__(
        self,
        manager: ProcessManager,
        name: str,
        port: int,
        ports,
        image: str,
        command: List[str],
        volumes={},
    ):
        self.__process = DockerProcess(
            image,
            name,
            manager,
            command=" ".join(command),
            hostname=name,
            ports=ports,
            volumes=volumes,
        )
        self.__manager = manager
        self.__port = port
        self.__grpc_channel: grpc.Channel = None

    def __enter__(self):
        self.__process.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.__grpc_channel != None:
            self.__grpc_channel.close()
        self.__process.__exit__(exc_type=exc_type, exc_val=exc_val, exc_tb=exc_tb)

    def ExpectOutput(self, message: str, timeout_s=5) -> bool:
        return self.__manager.ExpectOutput(self.__process.name, message, timeout_s)

    def channel(self) -> grpc.Channel:
        if self.__grpc_channel == None:
            self.__grpc_channel = grpc.insecure_channel(f"localhost:{self.__port}")
        return self.__grpc_channel

    def port(self):
        return self.__port


class ControlPlane(GrpcProcess):

    def __init__(
        self, manager: ProcessManager, name: str, port: int, upstream: str, image: str
    ):
        super().__init__(
            manager=manager,
            name=name,
            port=port,
            image=image,
            ports={3333: port},
            command=["--upstream", str(upstream), "--node", manager.nodeId],
        )

    def StopOnResourceRequest(
        self, resource_type: str, resource_name: str
    ) -> control_pb2.StopOnRequestResponse:
        stub = service_pb2_grpc.XdsConfigControlServiceStub(self.channel())
        res = stub.StopOnRequest(
            control_pb2.StopOnRequestRequest(
                resource_type=resource_type, resource_name=resource_name
            )
        )
        return res

    def UpdateResources(
        self, cluster: str, upstream_port: int, upstream_host="localhost"
    ):
        stub = service_pb2_grpc.XdsConfigControlServiceStub(self.channel())
        return stub.UpsertResources(
            control_pb2.UpsertResourcesRequest(
                cluster=cluster,
                upstream_host=upstream_host,
                upstream_port=upstream_port,
            )
        )


class Client(GrpcProcess):

    def __init__(
        self, manager: ProcessManager, port: int, name: str, url: str, image: str
    ):
        super().__init__(
            manager=manager,
            port=port,
            image=image,
            name=name,
            command=[f"--server={url}", "--print_response"],
            ports={50052: port},
            volumes={
                manager.mount_dir(): {
                    "bind": "/grpc",
                    "mode": "ro",
                }
            },
        )

    def GetStats(self, num_rpcs: int) -> messages_pb2.LoadBalancerStatsResponse:
        logging.debug(f"Sending {num_rpcs} requests")
        stub = test_pb2_grpc.LoadBalancerStatsServiceStub(self.channel())
        res = stub.GetClientStats(
            messages_pb2.LoadBalancerStatsRequest(
                num_rpcs=num_rpcs, timeout_sec=ceil(num_rpcs * 1.5)
            )
        )
        return res
