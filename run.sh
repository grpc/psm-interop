#!/usr/bin/env bash
# Copyright 2021 gRPC authors.
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

XDS_K8S_DRIVER_DIR="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
readonly XDS_K8S_DRIVER_DIR
readonly XDS_K8S_CONFIG="${XDS_K8S_CONFIG:-$XDS_K8S_DRIVER_DIR/config/local-dev.cfg}"
readonly PSM_LOG_DIR="${PSM_LOG_DIR:-${XDS_K8S_DRIVER_DIR}/out/run}"

display_usage() {
  cat <<EOF >/dev/stderr
Convenience script to execute tests/ and helper bin/ scripts.

USAGE: $0 script_path [arguments]
   script_path: path to python script to execute, relative to driver root folder
   arguments ...: arguments passed to program in sys.argv

ENVIRONMENT:
   XDS_K8S_CONFIG: file path to the config flagfile, relative to
                   driver root folder. Default: config/local-dev.cfg
                   Will be appended as --flagfile="config_absolute_path" argument
   XDS_K8S_DRIVER_VENV_DIR: the path to python virtual environment directory
                            Default: $XDS_K8S_DRIVER_DIR/venv
   PSM_EXTRA_FLAGS: Extra flags to append after the flagfile, but before the arguments
DESCRIPTION:
This tool performs the following:
1) Ensures python virtual env installed and activated
2) Exports test driver root in PYTHONPATH
3) Automatically appends --flagfile="\$XDS_K8S_CONFIG" argument

EXAMPLES:
$0 bin/run_td_setup.py --help      # list script-specific options
$0 bin/run_td_setup.py --helpfull  # list all available options
XDS_K8S_CONFIG=./path-to-flagfile.cfg ./run.sh bin/run_td_setup.py --resource_suffix=override-prefix
$0 tests/baseline_test.py
$0 tests/security_test.py --verbosity=1 --logger_levels=__main__:DEBUG,framework:DEBUG
$0 tests/security_test.py SecurityTest.test_mtls --nocheck_local_certs
EOF
  exit 1
}

if [[ "$#" -eq 0 || "$1" = "-h" || "$1" = "--help" ]]; then
  display_usage
fi


ensure_venv() {
  # Relative paths not yet supported by shellcheck.
  # shellcheck source=/dev/null
  source "${XDS_K8S_DRIVER_DIR}/bin/ensure_venv.sh"
  export PYTHONPATH="${XDS_K8S_DRIVER_DIR}"
}

main() {
  cd "${XDS_K8S_DRIVER_DIR}"
  ensure_venv

  # Split path to python file from the rest of the args.
  local py_file="$1"
  shift
  local psm_log_file prefix script_basename

  if [[ "${py_file}" =~ tests/unit($|/) ]]; then
    # Do not set the flagfile when running unit tests.
    exec python "${py_file}" "$@"
  fi

  prefix="$(date '+%Y%m%d-%H%M%S')"
  if [[ -f "${py_file}" && "${py_file}" =~ (bin|tests)/.+\.py$ ]]; then
    script_basename="$(basename "${py_file}" ".py")"
    psm_log_file="${prefix}-${script_basename}.log"
  else
    echo "Can't run '${py_file}'. Did you mean to run it directly?"
    exit 1
  fi

  declare -a extra_flags

  # Append extra flags
  if [[ -n "${PSM_EXTRA_FLAGS}" ]]; then
    declare -a extra_flags
    IFS=' ' read -r -a extra_flags <<< "${PSM_EXTRA_FLAGS}"
    echo "Appending flags from \$PSM_EXTRA_FLAGS:"
    printf -- "%s\n" "${extra_flags[@]}"
    echo
  fi

  # Automatically save last run logs to out
  if [[ -n "${psm_log_file}" ]]; then
    mkdir -p "${PSM_LOG_DIR}"
    exec &> >(tee -i "${PSM_LOG_DIR}/${psm_log_file}")
    echo "Saving framework log to ${PSM_LOG_DIR}/${psm_log_file}"
  fi

  PS4='+ $(date "+[%H:%M:%S %Z]")\011 '
  set -x
  # Append args after --flagfile, so they take higher priority.
  exec python "${py_file}" --flagfile="${XDS_K8S_CONFIG}" "${extra_flags[@]}" "$@"
}

main "$@"
