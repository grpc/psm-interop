import datetime
import math
import pathlib
import queue
import threading

import absl
import grpc
import mako.template

import docker
import protos.grpc.testing
import protos.grpc.testing.xdsconfig.control_pb2
import protos.grpc.testing.xdsconfig.service_pb2_grpc

class Bootstrap:

    def __init__(self, base: str, ports: list[int], host_name: str):
        self.base = pathlib.PosixPath(base)
        self.host_name = host_name
        self.ports = ports
        self.mount_dir: pathlib.Path = None
        self.MakeWorkingDir(self.base)
        # Use Mako
        template = mako.template.Template(filename="templates/bootstrap.json")
        file = template.render(
            servers=[f"{self.host_name}:{port}" for port in self.ports]
        )
        destination = self.get_mount_dir() / "bootstrap.json"
        with open(destination, "w") as f:
            f.write(file)
            absl.logging.debug(f"Generated bootstrap file at %s", destination)

    def MakeWorkingDir(self, base: str):
        for i in range(100):
            # Date time to string
            run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.mount_dir = base / f"testrun_{run_id}"
            if not self.mount_dir.exists():
                absl.logging.debug(f"Creating %s", self.mount_dir)
                self.get_mount_dir().mkdir(parents=True)
                return
        raise Exception("Couldn't find a free working directory")

    def get_mount_dir(self):
        if self.mount_dir is None:
            raise RuntimeError("Working dir was not created yet")
        return self.mount_dir.absolute()

    def xds_config_server_port(self, n: int):
        return self.ports[n]


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
        self.dockerClient = docker.DockerClient.from_env()
        self.nodeId = nodeId
        self.outputs = {}
        self.queue = queue.Queue()
        self.bootstrap = bootstrap
        self.verbosity = verbosity

    def NextEvent(self, timeout: int) -> ChildProcessEvent:
        event: ChildProcessEvent = self.queue.get(timeout=timeout)
        source = event.source
        message = event.data
        absl.logging.info(f"[%s] %s", source, message)
        if not source in self.outputs:
            self.outputs[source] = []
        self.outputs[source].append(message)
        return event

    def ExpectOutput(self, source: str, message: str, timeout_s=5) -> bool:
        absl.logging.debug(f'Waiting for message "%s" on %s', message, source)
        if source in self.outputs:
            for m in self.outputs[source]:
                if m.find(message) >= 0:
                    return True
        deadline = datetime.datetime.now() + datetime.timedelta(
            seconds=timeout_s
        )
        while datetime.datetime.now() <= deadline:
            event = self.NextEvent(timeout_s)
            if event.source == source and event.data.find(message) >= 0:
                return True
        return False

    def OnMessage(self, source: str, message: str):
        self.queue.put(ChildProcessEvent(source, message))

    def get_mount_dir(self):
        return self.bootstrap.get_mount_dir()


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
        self.name = name
        self.config = Configure(
            config, image=image, name=name, verbosity=manager.verbosity
        )
        self.container = None
        self.manager = manager
        self.thread = None

    def __enter__(self):
        self.container = self.manager.dockerClient.containers.run(**self.config)
        self.thread = threading.Thread(
            target=lambda process: process.LogReaderLoop(),
            args=(self,),
        )
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.container.stop()
            self.container.wait()
        except docker.errors.NotFound:
            # Ok, container was auto removed
            pass
        finally:
            self.thread.join()

    def LogReaderLoop(self):
        prefix = ""
        for log in self.container.logs(stream=True):
            s = str(prefix + log.decode("utf-8"))
            prefix = "" if s[-1] == "\n" else s[s.rfind("\n") :]
            for l in s[: s.rfind("\n")].splitlines():
                self.manager.OnMessage(self.name, _Sanitize(l))


class GrpcProcess:

    def __init__(
        self,
        manager: ProcessManager,
        name: str,
        port: int,
        ports,
        image: str,
        command: list[str],
        volumes=None,
    ):
        self.process = DockerProcess(
            image,
            name,
            manager,
            command=" ".join(command),
            hostname=name,
            ports=ports,
            volumes=volumes if volumes is not None else {},
        )
        self.manager = manager
        self.port = port
        self.grpc_channel: grpc.Channel = None

    def __enter__(self):
        self.process.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.grpc_channel is not None:
            self.grpc_channel.close()
        self.process.__exit__(exc_type=exc_type, exc_val=exc_val, exc_tb=exc_tb)

    def ExpectOutput(self, message: str, timeout_s=5) -> bool:
        return self.manager.ExpectOutput(self.process.name, message, timeout_s)

    def channel(self) -> grpc.Channel:
        if self.grpc_channel is None:
            self.grpc_channel = grpc.insecure_channel(f"localhost:{self.port}")
        return self.grpc_channel

    def get_port(self):
        return self.port


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
            ports={3333: port},
            command=["--upstream", str(upstream), "--node", manager.nodeId],
        )

    def StopOnResourceRequest(
        self, resource_type: str, resource_name: str
    ) -> protos.grpc.testing.xdsconfig.control_pb2.StopOnRequestResponse:
        stub = protos.grpc.testing.xdsconfig.service_pb2_grpc.XdsConfigControlServiceStub(
            self.channel()
        )
        res = stub.StopOnRequest(
            protos.grpc.testing.xdsconfig.control_pb2.StopOnRequestRequest(
                resource_type=resource_type, resource_name=resource_name
            )
        )
        return res

    def UpdateResources(
        self, cluster: str, upstream_port: int, upstream_host="localhost"
    ):
        stub = protos.grpc.testing.xdsconfig.service_pb2_grpc.XdsConfigControlServiceStub(
            self.channel()
        )
        return stub.UpsertResources(
            protos.grpc.testing.xdsconfig.control_pb2.UpsertResourcesRequest(
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
            ports={50052: port},
            volumes={
                manager.get_mount_dir(): {
                    "bind": "/grpc",
                    "mode": "ro",
                }
            },
        )

    def GetStats(self, num_rpcs: int):
        absl.logging.debug(f"Sending {num_rpcs} requests")
        stub = protos.grpc.testing.test_pb2_grpc.LoadBalancerStatsServiceStub(
            self.channel()
        )
        res = stub.GetClientStats(
            protos.grpc.testing.messages_pb2.LoadBalancerStatsRequest(
                num_rpcs=num_rpcs, timeout_sec=math.ceil(num_rpcs * 1.5)
            )
        )
        return res
