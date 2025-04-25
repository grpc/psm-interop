# Copyright 2025 gRPC authors.
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

Typical usage examples:

    # Help.
    ./run.sh ./bin/run_test_client_cloud_run.py --help

"""
import logging

from absl import app
from absl import flags

from bin.lib import common
from framework import xds_flags
from framework import xds_k8s_flags
from framework.infrastructure.mesh_resource_manager import (
    cloud_run_mesh_manager,
)

logger = logging.getLogger(__name__)

flags.adopt_module_key_flags(xds_flags)
flags.adopt_module_key_flags(xds_k8s_flags)
flags.adopt_module_key_flags(common)


def main(argv):
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    xds_flags.set_socket_default_timeout_from_flag()

    run_kwargs = dict()
    td = cloud_run_mesh_manager.CloudRunMeshManager(
        **common.td_attrs(), region=xds_flags.CLOUD_RUN_REGION.value
    )
    run_kwargs["config_mesh"] = td.make_resource_name(td.MESH_NAME)
    # Default server target pattern.
    server_target = f"xds:///{xds_flags.SERVER_XDS_HOST.value}"
    if xds_flags.SERVER_XDS_PORT.value != 80:
        server_target = f"{server_target}:{xds_flags.SERVER_XDS_PORT.value}"

    run_kwargs["server_target"] = server_target
    client_runner = common.make_cloud_run_client_runner(
        run_kwargs["config_mesh"], server_target
    )
    client_runner.run(**run_kwargs)


if __name__ == "__main__":
    app.run(main)
