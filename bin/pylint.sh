#!/usr/bin/env bash
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

set -eo pipefail

SCRIPT_DIR="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
readonly SCRIPT_DIR
XDS_K8S_DRIVER_DIR="$( cd -- "$(dirname "${SCRIPT_DIR}")" >/dev/null 2>&1 ; pwd -P )"
readonly XDS_K8S_DRIVER_DIR

# TODO(sergiitk): Once upgraded to be compatible with requirements-dev.txt, use the default venv.
display_usage() {
  cat <<EOF >/dev/stderr
A helper to run black pylint.

USAGE:
  ./bin/pylint.sh

ONE-TIME INSTALLATION:
  1. python3.10 -m venv --upgrade-deps venv-pylint
  2. source ./venv-pylint/bin/activate
  3. pip install pylint==2.2.2 astroid==2.3.3 toml==0.10.2 "isort>=4.3.0,<5.0.0"
  4. deactivate

ENVIRONMENT:
   XDS_K8S_DRIVER_PYLINT_VENV_DIR: the path to python virtual environment directory
                                   Default: $XDS_K8S_DRIVER_DIR/venv-pylint
EOF
  exit 1
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  display_usage
fi

cd "${XDS_K8S_DRIVER_DIR}"

# Relative paths not yet supported by shellcheck.
# shellcheck source=/dev/null
XDS_K8S_DRIVER_VENV_DIR="${XDS_K8S_DRIVER_PYLINT_VENV_DIR:-$XDS_K8S_DRIVER_DIR/venv-pylint}" \
  source "${XDS_K8S_DRIVER_DIR}/bin/ensure_venv.sh"

EXIT=0
set -x
pylint -rn 'bin' 'framework' || EXIT=1
pylint --rcfile=./tests/.pylintrc -rn 'tests' || EXIT=1
exit "${EXIT}"
