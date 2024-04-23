#!/usr/bin/env bash
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
set -eo pipefail

# Constants
readonly PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
# Test driver
readonly TEST_DRIVER_REPO_NAME="psm-interop"
readonly TEST_DRIVER_REPO_URL="https://github.com/${TEST_DRIVER_REPO_OWNER:-grpc}/psm-interop.git"
readonly TEST_DRIVER_BRANCH="${TEST_DRIVER_BRANCH:-main}"
readonly TEST_DRIVER_PATH=""
readonly TEST_DRIVER_PROTOS_PATH="protos/grpc/testing"
readonly FORCE_TESTING_VERSION="${FORCE_TESTING_VERSION:-}"
# TODO(sergiitk): can be defined as readonly FORCE_IMAGE_BUILD when removed from buildscripts.
readonly FORCE_IMAGE_BUILD_PSM="${FORCE_IMAGE_BUILD:-0}"

# GKE cluster identifiers.
readonly GKE_CLUSTER_PSM_LB="psm-lb"
readonly GKE_CLUSTER_PSM_SECURITY="psm-security"
readonly GKE_CLUSTER_PSM_BASIC="psm-basic"

# TODO(sergiitk): all methods should be using "psm::" package name,
#   see https://google.github.io/styleguide/shellguide.html#function-names

# --- LB TESTS ---

#######################################
# Prepares the list of tests in PSM LB test suite.
# Globals:
#   TESTING_VERSION: The version branch under test, f.e. master, dev, v1.42.x.
#   TESTS: Populated with tests in PSM LB test suite.
# Outputs:
#   Prints TESTS to stdout.
#######################################
psm::lb::get_tests() {
  # TODO(sergiitk): load from env var?
  TESTS=(
    "affinity_test"
    "api_listener_test"
    "app_net_test"
    "change_backend_service_test"
    "custom_lb_test"
    "failover_test"
    "outlier_detection_test"
    "remove_neg_test"
    "round_robin_test"
  )
  # master-only tests
  if [[ "${TESTING_VERSION}" =~ "master" ]]; then
      TESTS+=(
        "bootstrap_generator_test"
        "subsetting_test"
      )
  fi
  echo "LB test suite:"
  printf -- "- %s\n" "${TESTS[@]}"
}

psm::lb::run() {
  activate_gke_cluster GKE_CLUSTER_PSM_LB
  activate_secondary_gke_cluster GKE_CLUSTER_PSM_LB
  psm::setup_test_driver
  psm::build_docker_images_if_needed
  psm::lb::get_tests
  psm::run_tests
}

# --- Security TESTS ---

#######################################
# Prepares the list of tests in PSM Security test suite.
# Globals:
#   TESTS: Populated with tests in PSM Security test suite.
# Outputs:
#   Prints TESTS to stdout.
#######################################
psm::security::get_tests() {
  # TODO(sergiitk): load from env var?
  TESTS=(
    "baseline_test"
    "security_test"
    "authz_test"
  )
  echo "Security test suite:"
  printf -- "- %s\n" "${TESTS[@]}"
}

psm::security::run() {
  activate_gke_cluster GKE_CLUSTER_PSM_SECURITY
  psm::setup_test_driver
  psm::build_docker_images_if_needed
  psm::lb::get_tests
  psm::run_tests
}

# --- URL Map TESTS ---

#######################################
# Prepares the list of tests in URL Map test suite.
# Globals:
#   TESTS: Populated with tests in URL Map test suite.
# Outputs:
#   Prints TESTS to stdout.
#######################################
psm::url_map::get_tests() {
  TESTS=("url_map")
  echo "URL Map test suite:"
  printf -- "- %s\n" "${TESTS[@]}"
}

psm::url_map::run() {
  activate_gke_cluster GKE_CLUSTER_PSM_BASIC
  psm::setup_test_driver
  psm::build_docker_images_if_needed
  psm::url_map::get_tests
  psm::run_tests
}

# --- Common test run ---

#######################################
# TBD
# Globals:
#   KOKORO_ARTIFACTS_DIR
#   GITHUB_REPOSITORY_NAME
#   SRC_DIR: Populated with absolute path to the source repo
#   TEST_DRIVER_REPO_DIR: Populated with the path to the repo containing
#                         the test driver
#   TEST_DRIVER_FULL_DIR: Populated with the path to the test driver source code
#   TEST_DRIVER_FLAGFILE: Populated with relative path to test driver flagfile
#   TEST_XML_OUTPUT_DIR: Populated with the path to test xUnit XML report
#   GIT_ORIGIN_URL: Populated with the origin URL of git repo used for the build
#   GIT_COMMIT: Populated with the SHA-1 of git commit being built
#   GIT_COMMIT_SHORT: Populated with the short SHA-1 of git commit being built
#   KUBE_CONTEXT: Populated with name of kubectl context with GKE cluster access
# Arguments:
#   None
# Outputs:
#   Writes the output of test execution to stdout, stderr
#######################################
psm::run() {
  psm::set_docker_images "${GRPC_LANGUAGE}"
  case $1 in
    lb)
      psm::lb::run
      ;;
    security)
      psm::security::run
      ;;
    url_map)
      psm::url_map::run
      ;;
    *)
      echo "Unknown Test Suite: ${1}"
      exit 1
      ;;
  esac
}

psm::set_docker_images() {
  case $1 in
    java)
      SERVER_IMAGE_NAME="us-docker.pkg.dev/grpc-testing/psm-interop/${GRPC_LANGUAGE}-server"
      CLIENT_IMAGE_NAME="us-docker.pkg.dev/grpc-testing/psm-interop/${GRPC_LANGUAGE}-client"
      ;;
    *)
      echo "Unknown Language: ${1}"
      exit 1
      ;;
  esac
  declare -r SERVER_IMAGE_NAME
  declare -r CLIENT_IMAGE_NAME
}

psm::run_tests() {
  cd "${TEST_DRIVER_FULL_DIR}"
  local failed_tests=0
  for test_name in "${TESTS[@]}"; do
    run_test "${test_name}" || (( ++failed_tests ))
  done
  echo "Failed test suites: ${failed_tests}"
}

psm::setup_test_driver() {
  local script_dir
  script_dir="$(dirname "$0")"
  set -x
  echo "Sourcing ${script_dir}/psm-interop-build.sh"
  if [[ -f "${script_dir}/psm-interop-build.sh" ]]; then
    source "${script_dir}/psm-interop-build.sh"
  fi
  set +x

  if [[ -n "${KOKORO_ARTIFACTS_DIR}" ]]; then
    kokoro_setup_test_driver "${GITHUB_REPOSITORY_NAME}"
  else
    local_setup_test_driver "${script_dir}"
  fi

}

#######################################
# Builds test app and its docker images unless they already exist
# Globals:
#   SERVER_IMAGE_NAME: Test server Docker image name
#   CLIENT_IMAGE_NAME: Test client Docker image name
#   GIT_COMMIT: SHA-1 of git commit being built
#   FORCE_IMAGE_BUILD
# Arguments:
#   None
# Outputs:
#   Writes the output to stdout, stderr
#######################################
psm::build_docker_images_if_needed() {
  # Check if images already exist
  server_tags="$(gcloud_gcr_list_image_tags "${SERVER_IMAGE_NAME}" "${GIT_COMMIT}")"
  printf "Server image: %s:%s\n" "${SERVER_IMAGE_NAME}" "${GIT_COMMIT}"
  echo "${server_tags:-Server image not found}"

  client_tags="$(gcloud_gcr_list_image_tags "${CLIENT_IMAGE_NAME}" "${GIT_COMMIT}")"
  printf "Client image: %s:%s\n" "${CLIENT_IMAGE_NAME}" "${GIT_COMMIT}"
  echo "${client_tags:-Client image not found}"

  # Build if any of the images are missing, or FORCE_IMAGE_BUILD=1
  if [[ "${FORCE_IMAGE_BUILD_PSM}" == "1" || -z "${server_tags}" || -z "${client_tags}" ]]; then
    build_test_app_docker_images
  else
    echo "Skipping ${GRPC_LANGUAGE} test app build"
  fi
}

#######################################
# Determines the cluster name and zone based on the given cluster identifier.
# Globals:
#   GKE_CLUSTER_NAME: Set to reflect the cluster name to use
#   GKE_CLUSTER_ZONE: Set to reflect the cluster zone to use.
#   GKE_CLUSTER_REGION: Set to reflect the cluster region to use (for regional clusters).
# Arguments:
#   The cluster identifier
# Outputs:
#   Writes the output to stdout, stderr
#######################################
activate_gke_cluster() {
  case $1 in
    GKE_CLUSTER_PSM_LB)
      GKE_CLUSTER_NAME="psm-interop-lb-primary"
      GKE_CLUSTER_ZONE="us-central1-a"
      ;;
    GKE_CLUSTER_PSM_SECURITY)
      GKE_CLUSTER_NAME="psm-interop-security"
      GKE_CLUSTER_ZONE="us-central1-a"
      ;;
    GKE_CLUSTER_PSM_CSM)
      GKE_CLUSTER_NAME="psm-interop-csm"
      GKE_CLUSTER_REGION="us-central1"
      ;;
    GKE_CLUSTER_PSM_GAMMA)
      GKE_CLUSTER_NAME="psm-interop-gamma"
      GKE_CLUSTER_ZONE="us-central1-a"
      ;;
    GKE_CLUSTER_PSM_BASIC)
      GKE_CLUSTER_NAME="interop-test-psm-basic"
      GKE_CLUSTER_ZONE="us-central1-c"
      ;;
    *)
      echo "Unknown GKE cluster: ${1}"
      exit 1
      ;;
  esac
  echo -n "Activated GKE cluster: GKE_CLUSTER_NAME=${GKE_CLUSTER_NAME} "
  if [[ -n "${GKE_CLUSTER_REGION}" ]]; then
    echo "GKE_CLUSTER_REGION=${GKE_CLUSTER_REGION}"
  else
    echo "GKE_CLUSTER_ZONE=${GKE_CLUSTER_ZONE}"
  fi
}

#######################################
# Determines the secondary cluster name and zone based on the given cluster
# identifier.
# Globals:
#   GKE_CLUSTER_NAME: Set to reflect the cluster name to use
#   GKE_CLUSTER_ZONE: Set to reflect the cluster zone to use.
# Arguments:
#   The cluster identifier
# Outputs:
#   Writes the output to stdout, stderr
#######################################
activate_secondary_gke_cluster() {
  case $1 in
    GKE_CLUSTER_PSM_LB)
      SECONDARY_GKE_CLUSTER_NAME="psm-interop-lb-secondary"
      SECONDARY_GKE_CLUSTER_ZONE="us-west1-b"
      ;;
    *)
      echo "Unknown secondary GKE cluster: ${1}"
      exit 1
      ;;
  esac
  echo -n "Activated secondary GKE cluster: SECONDARY_GKE_CLUSTER_NAME=${SECONDARY_GKE_CLUSTER_NAME}"
  echo " SECONDARY_GKE_CLUSTER_ZONE=${SECONDARY_GKE_CLUSTER_ZONE}"
}

#######################################
# Run command end report its exit code. Doesn't exit on non-zero exit code.
# Globals:
#   None
# Arguments:
#   Command to execute
# Outputs:
#   Writes the output of given command to stdout, stderr
#######################################
run_ignore_exit_code() {
  local exit_code=0
  "$@" || exit_code=$?
  if [[ $exit_code != 0 ]]; then
    echo "Cmd: '$*', exit code: ${exit_code}"
  fi
}

#######################################
# Parses information about git repository at given path to global variables.
# Globals:
#   GIT_ORIGIN_URL: Populated with the origin URL of git repo used for the build
#   GIT_COMMIT: Populated with the SHA-1 of git commit being built
#   GIT_COMMIT_SHORT: Populated with the short SHA-1 of git commit being built
# Arguments:
#   Git source dir
#######################################
parse_src_repo_git_info() {
  local src_dir="${SRC_DIR:?SRC_DIR must be set}"
  readonly GIT_ORIGIN_URL=$(git -C "${src_dir}" remote get-url origin)
  readonly GIT_COMMIT=$(git -C "${src_dir}" rev-parse HEAD)
  readonly GIT_COMMIT_SHORT=$(git -C "${src_dir}" rev-parse --short HEAD)
}


#######################################
# Checks if the given string is a version branch.
# Version branches: "master", "v1.47.x"
# NOT version branches: "v1.47.0", "1.47.x", "", "dev", "main"
# Arguments:
#   Version to test
#######################################
is_version_branch() {
  if [ $# -eq 0 ]; then
    echo "Usage is_version_branch VERSION"
    false
    return
  fi
  if [[ $1 == "master" ]]; then
    true
    return
  fi
  # Do not inline version_regex: keep it a string to avoid issues with escaping chars in ~= expr.
  local version_regex='^v[0-9]+\.[0-9]+\.x$'
  [[ "${1}" =~ $version_regex ]]
}

#######################################
# List GCR image tags matching given tag name.
# Arguments:
#   Image name
#   Tag name
# Outputs:
#   Writes the table with the list of found tags to stdout.
#   If no tags found, the output is an empty string.
#######################################
gcloud_gcr_list_image_tags() {
  gcloud container images list-tags --format="table[box](tags,digest,timestamp.date())" --filter="tags:$2" "$1"
}

#######################################
# Create kube context authenticated with GKE cluster, saves context name.
# to KUBE_CONTEXT
# Globals:
#   GKE_CLUSTER_NAME
#   GKE_CLUSTER_ZONE
#   KUBE_CONTEXT: Populated with name of kubectl context with GKE cluster access
#   SECONDARY_KUBE_CONTEXT: Populated with name of kubectl context with secondary GKE cluster access, if any
# Arguments:
#   None
# Outputs:
#   Writes the output of `gcloud` command to stdout, stderr
#   Writes authorization info $HOME/.kube/config
#######################################
gcloud_get_cluster_credentials() {
  # Secondary cluster, when set.
  if [[ -n "${SECONDARY_GKE_CLUSTER_NAME}" && -n "${SECONDARY_GKE_CLUSTER_ZONE}" ]]; then
    gcloud container clusters get-credentials "${SECONDARY_GKE_CLUSTER_NAME}" --zone "${SECONDARY_GKE_CLUSTER_ZONE}"
    readonly SECONDARY_KUBE_CONTEXT="$(kubectl config current-context)"
  else
    readonly SECONDARY_KUBE_CONTEXT=""
  fi
  # Primary cluster.
  if [[ -n "${GKE_CLUSTER_REGION}" ]]; then
    gcloud container clusters get-credentials "${GKE_CLUSTER_NAME}" --region "${GKE_CLUSTER_REGION}"
  else
    gcloud container clusters get-credentials "${GKE_CLUSTER_NAME}" --zone "${GKE_CLUSTER_ZONE}"
  fi
  readonly KUBE_CONTEXT="$(kubectl config current-context)"
}

#######################################
# Clone the source code of the test driver to $TEST_DRIVER_REPO_DIR, unless
# given folder exists.
# Globals:
#   TEST_DRIVER_REPO_URL
#   TEST_DRIVER_BRANCH
#   TEST_DRIVER_REPO_DIR: path to the repo containing the test driver
#   TEST_DRIVER_REPO_DIR_USE_EXISTING: set non-empty value to use exiting
#      clone of the driver repo located at $TEST_DRIVER_REPO_DIR.
#      Useful for debugging the build script locally.
# Arguments:
#   None
# Outputs:
#   Writes the output of `git` command to stdout, stderr
#   Writes driver source code to $TEST_DRIVER_REPO_DIR
#######################################
test_driver_get_source() {
  if [[ -n "${TEST_DRIVER_REPO_DIR_USE_EXISTING}" && -d "${TEST_DRIVER_REPO_DIR}" ]]; then
    echo "Using exiting driver directory: ${TEST_DRIVER_REPO_DIR}."
  else
    echo "Cloning driver to ${TEST_DRIVER_REPO_URL} branch ${TEST_DRIVER_BRANCH} to ${TEST_DRIVER_REPO_DIR}"
    git clone -b "${TEST_DRIVER_BRANCH}" --depth=1 "${TEST_DRIVER_REPO_URL}" "${TEST_DRIVER_REPO_DIR}"
  fi
}

#######################################
# Install Python modules from required in $TEST_DRIVER_FULL_DIR/requirements.lock
# to Python virtual environment. Creates and activates Python venv if necessary.
# Globals:
#   TEST_DRIVER_FULL_DIR
#   PYTHON_VERSION
# Arguments:
#   None
# Outputs:
#   Writes the output of `python`, `pip` commands to stdout, stderr
#   Writes the list of installed modules to stdout
#######################################
test_driver_pip_install() {
  echo "Install python dependencies"
  cd "${TEST_DRIVER_FULL_DIR}"

  # Create and activate virtual environment unless already using one
  if [[ -z "${VIRTUAL_ENV}" ]]; then
    local venv_dir="${TEST_DRIVER_FULL_DIR}/venv"
    if [[ -d "${venv_dir}" ]]; then
      echo "Found python virtual environment directory: ${venv_dir}"
    else
      echo "Creating python virtual environment: ${venv_dir}"
      "python${PYTHON_VERSION}" -m venv "${venv_dir}" --upgrade-deps
    fi
    # Intentional: No need to check python venv activate script.
    # shellcheck source=/dev/null
    source "${venv_dir}/bin/activate"
  fi

  python3 -m pip install -r requirements.lock
  echo "Installed Python packages:"
  python3 -m pip list
}

#######################################
# Compile proto-files needed for the test driver
# Globals:
#   TEST_DRIVER_REPO_DIR
#   TEST_DRIVER_FULL_DIR
#   TEST_DRIVER_PROTOS_PATH
# Arguments:
#   None
# Outputs:
#   Writes the output of `python -m grpc_tools.protoc` to stdout, stderr
#   Writes the list if compiled python code to stdout
#   Writes compiled python code with proto messages and grpc services to
#   $TEST_DRIVER_FULL_DIR/protos/grpc/testing
#######################################
test_driver_compile_protos() {
  declare -a protos
  protos=(
    "${TEST_DRIVER_PROTOS_PATH}/test.proto"
    "${TEST_DRIVER_PROTOS_PATH}/messages.proto"
    "${TEST_DRIVER_PROTOS_PATH}/empty.proto"
  )
  echo "Generate python code from grpc.testing protos: ${protos[*]}"
  cd "${TEST_DRIVER_REPO_DIR}"
  python3 -m grpc_tools.protoc \
    --proto_path=. \
    --python_out="${TEST_DRIVER_FULL_DIR}" \
    --grpc_python_out="${TEST_DRIVER_FULL_DIR}" \
    "${protos[@]}"
  local protos_out_dir="${TEST_DRIVER_FULL_DIR}/${TEST_DRIVER_PROTOS_PATH}"
  echo "Generated files ${protos_out_dir}:"
  ls -Fl "${protos_out_dir}"
}

#######################################
# Installs the test driver and it's requirements.
# https://github.com/grpc/grpc/tree/master/tools/run_tests/xds_k8s_test_driver#installation
# Globals:
#   TEST_DRIVER_REPO_DIR: Populated with the path to the repo containing
#                         the test driver
#   TEST_DRIVER_FULL_DIR: Populated with the path to the test driver source code
# Arguments:
#   The directory for test driver's source code
# Outputs:
#   Writes the output to stdout, stderr
#######################################
test_driver_install() {
  readonly TEST_DRIVER_REPO_DIR="${1:?Usage test_driver_install TEST_DRIVER_REPO_DIR}"
  readonly TEST_DRIVER_FULL_DIR="${TEST_DRIVER_REPO_DIR}"
  test_driver_get_source
  test_driver_pip_install
  test_driver_compile_protos
}

#######################################
# Outputs Ubuntu's lsb_release and system python, pip versions
# Arguments:
#   None
# Outputs:
#   Writes the output to stdout
#######################################
kokoro_print_version() {
  echo "Kokoro Ubuntu version:"
  run_ignore_exit_code lsb_release -a
  run_ignore_exit_code "python${PYTHON_VERSION}" --version
  run_ignore_exit_code "python${PYTHON_VERSION}" -m pip --version
}

#######################################
# Report extra information about the job via sponge properties.
# Globals:
#   KOKORO_ARTIFACTS_DIR
#   GIT_ORIGIN_URL
#   GIT_COMMIT_SHORT
#   TESTGRID_EXCLUDE
# Arguments:
#   None
# Outputs:
#   Writes the output to stdout
#   Writes job properties to $KOKORO_ARTIFACTS_DIR/custom_sponge_config.csv
#######################################
kokoro_write_sponge_properties() {
  # CSV format: "property_name","property_value"
  # Bump TESTS_FORMAT_VERSION when reported test name changed enough to when it
  # makes more sense to discard previous test results from a testgrid board.
  # Use GIT_ORIGIN_URL to exclude test runs executed against repo forks from
  # testgrid reports.
  cat >"${KOKORO_ARTIFACTS_DIR}/custom_sponge_config.csv" <<EOF
TESTS_FORMAT_VERSION,2
TESTGRID_EXCLUDE,${TESTGRID_EXCLUDE:-0}
GIT_ORIGIN_URL,${GIT_ORIGIN_URL:?GIT_ORIGIN_URL must be set}
GIT_COMMIT_SHORT,${GIT_COMMIT_SHORT:?GIT_COMMIT_SHORT must be set}
EOF
  echo "Sponge properties:"
  cat "${KOKORO_ARTIFACTS_DIR}/custom_sponge_config.csv"
}

#######################################
# Install packages via apt.
# Arguments:
#   None
# Outputs:
#   Writes the output of `apt` commands to stdout
#######################################
kokoro_install_dependencies() {
  # needrestart checks which daemons need to be restarted after library
  # upgrades. It's useless to us in non-interactive mode.
  sudo DEBIAN_FRONTEND=noninteractive apt-get -qq remove needrestart
  sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update
  sudo DEBIAN_FRONTEND=noninteractive apt-get -qq install --auto-remove \
    "python${PYTHON_VERSION}-venv" \
    google-cloud-sdk-gke-gcloud-auth-plugin \
    kubectl
  sudo rm -rf /var/lib/apt/lists
}

#######################################
# Determines the version branch under test from Kokoro environment.
# Globals:
#   KOKORO_JOB_NAME
#   KOKORO_BUILD_INITIATOR
#   FORCE_TESTING_VERSION: Forces the testing version to be something else.
#   TESTING_VERSION: Populated with the version branch under test,
#                    f.e. master, dev, v1.42.x.
# Outputs:
#   Sets TESTING_VERSION global variable.
#######################################
kokoro_get_testing_version() {
  # All grpc kokoro jobs names structured to have the version identifier in the third position:
  # - grpc/core/master/linux/...
  # - grpc/core/v1.42.x/branch/linux/...
  # - grpc/java/v1.47.x/branch/...
  # - grpc/go/v1.47.x/branch/...
  # - grpc/node/v1.6.x/...
  local version_from_job_name
  version_from_job_name=$(echo "${KOKORO_JOB_NAME}" | cut -d '/' -f3)

  if [[ -n "${FORCE_TESTING_VERSION}" ]]; then
    # Allows to override the testing version, and force tagging the built
    # images, if necessary.
    readonly TESTING_VERSION="${FORCE_TESTING_VERSION}"
  elif [[ "${KOKORO_BUILD_INITIATOR:-anonymous}" != "kokoro" ]]; then
    # If not initiated by Kokoro, it's a dev branch.
    # This allows to know later down the line that the built image doesn't need
    # to be tagged, and avoid overriding an actual versioned image used in tests
    # (e.g. v1.42.x, master) with a dev build.
    if [[ -n "${version_from_job_name}" ]]; then
      readonly TESTING_VERSION="dev-${version_from_job_name}"
    else
      readonly TESTING_VERSION="dev"
    fi
  else
    readonly TESTING_VERSION="${version_from_job_name}"
  fi
}

#######################################
# Installs and configures the test driver on Kokoro VM.
# Globals:
#   KOKORO_ARTIFACTS_DIR
#   KOKORO_JOB_NAME
#   TEST_DRIVER_REPO_NAME
#   TESTING_VERSION: Populated with the version branch under test, f.e. v1.42.x, master
#   SRC_DIR: Populated with absolute path to the source repo on Kokoro VM
#   TEST_DRIVER_REPO_DIR: Populated with the path to the repo containing
#                         the test driver
#   TEST_DRIVER_FULL_DIR: Populated with the path to the test driver source code
#   TEST_DRIVER_FLAGFILE: Populated with relative path to test driver flagfile
#   TEST_XML_OUTPUT_DIR: Populated with the path to test xUnit XML report
#   KUBE_CONTEXT: Populated with name of kubectl context with GKE cluster access
#   SECONDARY_KUBE_CONTEXT: Populated with name of kubectl context with secondary GKE cluster access, if any
#   GIT_ORIGIN_URL: Populated with the origin URL of git repo used for the build
#   GIT_COMMIT: Populated with the SHA-1 of git commit being built
#   GIT_COMMIT_SHORT: Populated with the short SHA-1 of git commit being built
# Arguments:
#   The name of github repository being built
# Outputs:
#   Writes the output to stdout, stderr, files
#######################################
kokoro_setup_test_driver() {
  # Unset noisy verbose mode often set in the parent script.
  set +x
  local src_repository_name="${1:?Usage kokoro_setup_test_driver GITHUB_REPOSITORY_NAME}"
  # Capture Kokoro VM version info in the log.
  kokoro_print_version

  # Get testing version from the job name.
  kokoro_get_testing_version

  # Kokoro clones repo to ${KOKORO_ARTIFACTS_DIR}/github/${GITHUB_REPOSITORY}
  local github_root="${KOKORO_ARTIFACTS_DIR}/github"
  readonly SRC_DIR="${github_root}/${src_repository_name}"
  parse_src_repo_git_info SRC_DIR
  kokoro_write_sponge_properties
  kokoro_install_dependencies

  # Get kubectl cluster credentials.
  gcloud_get_cluster_credentials

  # Install the driver.
  local test_driver_repo_dir
  test_driver_repo_dir="${TEST_DRIVER_REPO_DIR:-$(mktemp -d)/${TEST_DRIVER_REPO_NAME}}"
  test_driver_install "${test_driver_repo_dir}"
  # shellcheck disable=SC2034  # Used in the main script
  readonly TEST_DRIVER_FLAGFILE="config/grpc-testing.cfg"

  # Test artifacts dir: xml reports, logs, etc.
  local artifacts_dir="${KOKORO_ARTIFACTS_DIR}/artifacts"
  # Folders after $artifacts_dir reported as target name
  readonly TEST_XML_OUTPUT_DIR="${artifacts_dir}/${KOKORO_JOB_NAME}"
  mkdir -p "${artifacts_dir}" "${TEST_XML_OUTPUT_DIR}"
}

#######################################
# Installs and configures the test driver for testing build script locally.
# Globals:
#   TEST_DRIVER_REPO_NAME
#   TEST_DRIVER_REPO_DIR: Unless provided, populated with a temporary dir with
#                         the path to the test driver repo
#   SRC_DIR: Populated with absolute path to the source repo
#   KUBE_CONTEXT: Populated with name of kubectl context with GKE cluster access
#   TEST_DRIVER_FLAGFILE: Populated with relative path to test driver flagfile
#   TEST_XML_OUTPUT_DIR: Populated with the path to test xUnit XML report
#   GIT_ORIGIN_URL: Populated with the origin URL of git repo used for the build
#   GIT_COMMIT: Populated with the SHA-1 of git commit being built
#   GIT_COMMIT_SHORT: Populated with the short SHA-1 of git commit being built
#   SECONDARY_KUBE_CONTEXT: Populated with name of kubectl context with secondary GKE cluster access, if any
# Arguments:
#   The path to the folder containing the build script
# Outputs:
#   Writes the output to stdout, stderr, files
#######################################
local_setup_test_driver() {
  local script_dir="${1:?Usage: local_setup_test_driver SCRIPT_DIR}"
  readonly SRC_DIR="$(git -C "${script_dir}" rev-parse --show-toplevel)"
  parse_src_repo_git_info "${SRC_DIR}"
  readonly KUBE_CONTEXT="${KUBE_CONTEXT:-$(kubectl config current-context)}"
  readonly SECONDARY_KUBE_CONTEXT="${SECONDARY_KUBE_CONTEXT}"

  # Never override docker image for local runs, unless explicitly forced.
  if [[ -n "${FORCE_TESTING_VERSION}" ]]; then
    readonly TESTING_VERSION="${FORCE_TESTING_VERSION}"
  else
    readonly TESTING_VERSION="dev"
  fi

  local test_driver_repo_dir
  test_driver_repo_dir="${TEST_DRIVER_REPO_DIR:-$(mktemp -d)/${TEST_DRIVER_REPO_NAME}}"
  test_driver_install "${test_driver_repo_dir}"

  # shellcheck disable=SC2034  # Used in the main script
  readonly TEST_DRIVER_FLAGFILE="config/local-dev.cfg"
  # Test out
  readonly TEST_XML_OUTPUT_DIR="${TEST_DRIVER_FULL_DIR}/out"
  mkdir -p "${TEST_XML_OUTPUT_DIR}"
}

#######################################
# Tag and push the given Docker image
# Arguments:
#   The Docker image name
#   The Docker image original tag name
#   The Docker image new tag name
# Outputs:
#   Writes the output to stdout, stderr, files
#######################################
tag_and_push_docker_image() {
  local image_name="$1"
  local from_tag="$2"
  local to_tag="$3"

  docker tag "${image_name}:${from_tag}" "${image_name}:${to_tag}"
  docker push "${image_name}:${to_tag}"
}
