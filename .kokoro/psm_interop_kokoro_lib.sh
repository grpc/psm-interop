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

# Prepend verbose mode commands (xtrace) with the date.
PS4='+ $(date "+[%H:%M:%S %Z]")\011 '

# --- Constants ---
# GKE cluster identifiers.
readonly GKE_CLUSTER_PSM_LB="psm-lb"
readonly GKE_CLUSTER_PSM_SECURITY="psm-security"
readonly GKE_CLUSTER_PSM_BASIC="psm-basic"
# TODO(sergiitk): 'if' can be removed when DOCKER_REGISTRY removed from buildscripts.
if [[ -z "${DOCKER_REGISTRY}" ]] ; then
  readonly DOCKER_REGISTRY="us-docker.pkg.dev"
fi
# Test driver
readonly TEST_DRIVER_REPO_NAME="psm-interop"
readonly TEST_DRIVER_PATH=""
readonly TEST_DRIVER_PROTOS_PATH="protos/grpc/testing"

# --- Injectable constants ---
readonly PYTHON_VERSION="${PYTHON_VERSION:-3.10}"

# Test driver
readonly TEST_DRIVER_REPO_OWNER="${TEST_DRIVER_REPO_OWNER:-grpc}"
readonly TEST_DRIVER_REPO_URL="https://github.com/${TEST_DRIVER_REPO_OWNER}/${TEST_DRIVER_REPO_NAME}.git"
readonly TEST_DRIVER_BRANCH="${TEST_DRIVER_BRANCH:-main}"

# Forces image build, even if a tag with GIT_COMMIT already exists.
readonly PSM_FORCE_IMAGE_BUILD="${PSM_FORCE_IMAGE_BUILD:-0}"
# Overrides the TESTING_VERSION. Can be used to force versioned image tag, see is_version_branch.
readonly PSM_FORCE_TESTING_VERSION="${PSM_FORCE_TESTING_VERSION:-}"
# Overrides the list of test to run. A whitespace-separated string. Example:
# PSM_TESTS="app_net_test baseline_test"
readonly PSM_TESTS="${PSM_TESTS:-}"
# A space-separated string with extra flags to append to the test driver arguments.
# Can be used to execute a run and test a new flag value, f.e.:
# PSM_EXTRA_FLAGS="--noenable_workload_identity --td_bootstrap_image=us-docker.pkg.dev/new-image..."
# In addition, can be used to run a single test in a suite. F.e. to run only test_mtls in security_test:
# PSM_TESTS="security_test" PSM_EXTRA_FLAGS="SecurityTest.test_mtls"
readonly PSM_EXTRA_FLAGS="${PSM_EXTRA_FLAGS:-}"


# --- LB TESTS ------------------------

#######################################
# LB Test Suite setup.
# Outputs:
#   Prints activated cluster names.
#######################################
psm::lb::setup() {
  activate_gke_cluster GKE_CLUSTER_PSM_LB
  activate_secondary_gke_cluster GKE_CLUSTER_PSM_LB
}

#######################################
# Prepares the list of tests in PSM LB test suite.
# Globals:
#   TESTING_VERSION: The version branch under test, f.e. master, dev, v1.42.x.
#   TESTS: Populated with tests in the test suite.
#######################################
psm::lb::get_tests() {
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
    "circuit_breaking_test"
  )
  # master-only tests
  if [[ "${TESTING_VERSION}" =~ "master" ]]; then
      psm::tools::log "Appending master-only tests to the LB suite."
      TESTS+=(
        "bootstrap_generator_test"
        "subsetting_test"
      )
  fi
}

#######################################
# Executes LB test case.
# Globals:
#   PSM_TEST_FLAGS: The array with flags for the test
# Arguments:
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::lb::run_test() {
  local test_name="${1:?${FUNCNAME[0]} missing the test name argument}"
  psm::run::finalize_test_flags "${test_name}"
  psm::tools::run_verbose python -m "tests.${test_name}" "${PSM_TEST_FLAGS[@]}"
}

# --- Security TESTS ------------------

#######################################
# Security Test Suite setup.
# Outputs:
#   Prints activated cluster names.
#######################################
psm::security::setup() {
  activate_gke_cluster GKE_CLUSTER_PSM_SECURITY
}

#######################################
# Prepares the list of tests in PSM Security test suite.
# Globals:
#   TESTS: Populated with tests in PSM Security test suite.
#######################################
psm::security::get_tests() {
  TESTS=(
    "baseline_test"
    "security_test"
    "authz_test"
  )
}

#######################################
# Executes Security test case
# Globals:
#   PSM_TEST_FLAGS: The array with flags for the test
#   GRPC_LANGUAGE: The name of gRPC languages under test
# Arguments:
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::security::run_test() {
  local test_name="${1:?${FUNCNAME[0]} missing the test name argument}"

  # Only java supports extra checks for certificate matches (via channelz socket info).
  if [[ "${GRPC_LANGUAGE}" != "java"  ]]; then
    PSM_TEST_FLAGS+=("--nocheck_local_certs")
  fi

  psm::run::finalize_test_flags "${test_name}"
  psm::tools::run_verbose python -m "tests.${test_name}" "${PSM_TEST_FLAGS[@]}"
}

# --- DualStack TESTS ------------------

#######################################
# DualStack Test Suite setup.
# Outputs:
#   Prints activated cluster names.
#######################################
psm::dualstack::setup() {
  activate_gke_cluster GKE_CLUSTER_DUALSTACK
}

#######################################
# Prepares the list of tests in DualStack test suite.
# Globals:
#   TESTS: Populated with tests in PSM DualStack test suite.
#######################################
psm::dualstack::get_tests() {
  TESTS=(
    "dualstack_test"
  )
}

#######################################
# Executes DualStack test case
# Globals:
#   PSM_TEST_FLAGS: The array with flags for the test
#   GRPC_LANGUAGE: The name of gRPC languages under test
# Arguments:
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::dualstack::run_test() {
  local test_name="${1:?${FUNCNAME[0]} missing the test name argument}"

  PSM_TEST_FLAGS+=(
    "--flagfile=config/common-dualstack.cfg"
  )

  psm::run::finalize_test_flags "${test_name}"
  psm::tools::run_verbose python -m "tests.${test_name}" "${PSM_TEST_FLAGS[@]}"
}

# --- URL Map TESTS ------------------

#######################################
# URL Map Test Suite setup.
# Outputs:
#   Prints activated cluster names.
#######################################
psm::url_map::setup() {
  activate_gke_cluster GKE_CLUSTER_PSM_BASIC
}

#######################################
# Prepares the list of tests in URL Map test suite.
# Globals:
#   TESTS: Populated with tests in the test suite.
#######################################
psm::url_map::get_tests() {
  TESTS=("url_map")
}

#######################################
# Executes URL Map test case
# Globals:
#   PSM_TEST_FLAGS: The array with flags for the test
# Arguments:
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::url_map::run_test() {
  local test_name="${1:?${FUNCNAME[0]} missing the test name argument}"
  PSM_TEST_FLAGS+=(
    "--flagfile=config/url-map.cfg"
  )
  psm::run::finalize_test_flags "${test_name}"
  psm::tools::run_verbose python -m "tests.${test_name}" "${PSM_TEST_FLAGS[@]}"
}

# --- CSM TESTS ------------------

#######################################
# CSM Test Suite setup.
# Outputs:
#   Prints activated cluster names.
#######################################
psm::csm::setup() {
  activate_gke_cluster GKE_CLUSTER_PSM_CSM
}

#######################################
# Prepares the list of tests in CSM test suite.
# Globals:
#   TESTS: Populated with tests in the test suite.
#######################################
psm::csm::get_tests() {
  TESTS=(
    "gamma.gamma_baseline_test"
    "gamma.gamma_grpcroute_test"
    "gamma.affinity_test"
    "gamma.affinity_session_drain_test"
    "gamma.csm_observability_test"
    "app_net_ssa_test"
    "app_net_csm_observability_test"
  )
}

#######################################
# Executes CSM test case
# Globals:
#   PSM_TEST_FLAGS: The array with flags for the test
# Arguments:
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::csm::run_test() {
  local test_name="${1:?${FUNCNAME[0]} missing the test name argument}"
  PSM_TEST_FLAGS+=(
    "--flagfile=config/common-csm.cfg"
  )
  psm::run::finalize_test_flags "${test_name}"
  psm::tools::run_verbose python -m "tests.${test_name}" "${PSM_TEST_FLAGS[@]}"
}

# --- Common test run logic -----------

#######################################
# Prepares and runs the test suite.
#
# Main entry function to be used by the buildscripts.
# Provisions necessary software, configures the test driver, and executes the test suite.
#
# Globals:
#   GRPC_LANGUAGE: The name of gRPC languages under test
#   BUILD_SCRIPT_DIR: Absolute path to the directory with lang-specific buildscript
#     in the source repo.
# Arguments:
#   Test suite name, one of (lb, security, dualstack, url_map, csm)
# Outputs:
#   Writes the output of test execution to stdout, stderr
#######################################
psm::run() {
  local test_suite="${1:?${FUNCNAME[0]} missing the test suite argument}"
  psm::tools::log "Starting PSM Interop tests: ${test_suite}"
  if [[ $(type -t psm::lang::build_docker_images) != function ]]; then
      psm::tools::log "Method psm::lang::build_docker_images must be defined by the buildscript."
      exit 1
  fi

  psm::setup::docker_image_names "${GRPC_LANGUAGE}" "${test_suite}"

  case "${test_suite}" in
    lb | security | dualstack | url_map | csm)
      psm::setup::generic_test_suite "${test_suite}"
      ;;
    *)
      psm::tools::log "Unknown Test Suite: ${test_suite}"
      exit 1
      ;;
  esac

  psm::run::test_suite "${test_suite}"
  psm::tools::log "PSM Interop tests completed: ${test_suite}"
}

#######################################
# Executes tests in the test suite
# Globals:
#   TEST_DRIVER_FULL_DIR
#   TESTS: the list of the tests to execute
# Arguments:
#   Test suite name.
# Outputs:
#   Each test to stdout.
#######################################
psm::run::test_suite() {
  local test_suite="${1:?${FUNCNAME[0]} missing the test suite argument}"
  cd "${TEST_DRIVER_FULL_DIR}"
  local failed_tests=0
  for test_name in "${TESTS[@]}"; do
    psm::run::test "${test_suite}" "${test_name}" || (( ++failed_tests ))
    psm::tools::log "Finished ${test_suite} suite test: ${test_name}"
    echo
  done
  psm::tools::log "Failed test suites: ${failed_tests}"
}

#######################################
# Executes a single test case of given test suite.
# Globals:
#   TEST_DRIVER_FLAGFILE: Relative path to test driver flagfile
#   KUBE_CONTEXT: The name of kubectl context with GKE cluster access
#   TEST_XML_OUTPUT_DIR: Output directory for the test xUnit XML report
#   CLIENT_IMAGE_NAME: Test client Docker image name
#   GIT_COMMIT: SHA-1 of git commit being built
#   TESTING_VERSION: version branch under test: used by the framework to
#                     determine the supported PSM features.
# Arguments:
#   Test suite name
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::run::test() {
  # Test driver usage: https://github.com/grpc/psm-interop#basic-usage
  local test_suite="${1:?${FUNCNAME[0]} missing the test suite argument}"
  local test_name="${2:?${FUNCNAME[0]} missing the test name argument}"
  local out_dir="${TEST_XML_OUTPUT_DIR:-/tmp/psm}/${test_name}"
  local test_log="${out_dir}/sponge_log.log"
  mkdir -p "${out_dir}"

  PSM_TEST_FLAGS=(
    "--flagfile=${TEST_DRIVER_FLAGFILE}"
    "--kube_context=${KUBE_CONTEXT}"
    "--force_cleanup"
    "--collect_app_logs"
    "--log_dir=${out_dir}"
    "--xml_output_file=${out_dir}/sponge_log.xml"
    "--testing_version=${TESTING_VERSION}"
    "--client_image=${CLIENT_IMAGE_NAME}:${GIT_COMMIT}"
  )

  # Some test suites have canonical server image configured in the flagfiles.
  if [[ -z "${SERVER_IMAGE_USE_CANONICAL}" ]]; then
    PSM_TEST_FLAGS+=("--server_image=${SERVER_IMAGE_NAME}:${GIT_COMMIT}")
  elif [[ "${GRPC_LANGUAGE}" == "node"  ]]; then
    # TODO(b/261911148): To be replaced with --server_image_use_canonical when implemented.
    PSM_TEST_FLAGS+=("--server_image=us-docker.pkg.dev/grpc-testing/psm-interop/java-server:canonical-v1.65")
  fi

  # So far, only LB test uses secondary GKE cluster.
  if [[ -n "${SECONDARY_KUBE_CONTEXT}" ]]; then
    PSM_TEST_FLAGS+=("--secondary_kube_context=${SECONDARY_KUBE_CONTEXT}")
  fi

  psm::tools::log "Running ${test_suite} suite test: ${test_name}" |& tee "${test_log}"
  # Must be the last line.
  "psm::${test_suite}::run_test" "${test_name}" |& tee -a "${test_log}"
}

#######################################
# Appends extra flags (if any) to the end of PSM_TEST_FLAGS, prints the flag list.
# Globals:
#   PSM_TEST_FLAGS: The array with flags for the test
#   PSM_EXTRA_FLAGS: Space-separated string with extra flags to append
# Arguments:
#   Test case name
# Outputs:
#   Prints the list of test flags to the stdout
#######################################
psm::run::finalize_test_flags() {
  local test_name="${1:?${FUNCNAME[0]} missing the test name argument}"

  # Append extra flags
  if [[ -n "${PSM_EXTRA_FLAGS}" ]]; then
    declare -a extra_flags
    IFS=' ' read -r -a extra_flags <<< "${PSM_EXTRA_FLAGS}"
    PSM_TEST_FLAGS+=("${extra_flags[@]}")
  fi

  psm::tools::log "Test driver flags for ${test_name}:"
  printf -- "%s\n" "${PSM_TEST_FLAGS[@]}"
  echo
}


# --- Common test setup logic -----------

psm::setup::generic_test_suite() {
  local test_suite="${1:?${FUNCNAME[0]} missing the test suite argument}"
  "psm::${test_suite}::setup"
  psm::setup::test_driver
  psm::build::docker_images_if_needed
  psm::setup::get_tests "${test_suite}"
}

psm::setup::get_tests() {
  local test_suite="${1:?${FUNCNAME[0]} missing the test suite argument}"

  if [[ -n "${PSM_TESTS}" ]]; then
    # Test list overridden in env var.
    IFS=' ' read -r -a TESTS <<< "${PSM_TESTS}"
    if (( ${#TESTS[@]} == 0 )); then
        psm::tools::log "Error: test list overridden, but no tests specified"
        exit 1
    fi
  else
    "psm::${test_suite}::get_tests"
  fi

  psm::tools::log "Tests in ${test_suite} test suite:"
  printf -- "  - %s\n" "${TESTS[@]}"
  echo
}

#######################################
# Executes the test case
# Globals:
#   CLIENT_IMAGE_NAME: Populated with xDS test client image name
#   SERVER_IMAGE_NAME: Populated with xDS test server image name
#   SERVER_IMAGE_USE_CANONICAL: Set to "1" when canonical server image will be used in the tests
#                               instead of the one configured in SERVER_IMAGE_NAME.
# Arguments:
#   gRPC Language
#   Test case name
# Outputs:
#   Writes the output of test execution to stdout, stderr
#   Test xUnit report to ${TEST_XML_OUTPUT_DIR}/${test_name}/sponge_log.xml
#######################################
psm::setup::docker_image_names() {
  local language="${1:?${FUNCNAME[0]} missing the language argument}"
  local test_suite="${2:?${FUNCNAME[0]} missing the test suite argument}"
  SERVER_IMAGE_USE_CANONICAL=""

  case "${language}" in
    java | cpp | python | go)
      CLIENT_IMAGE_NAME="${DOCKER_REGISTRY}/grpc-testing/psm-interop/${GRPC_LANGUAGE}-client"
      SERVER_IMAGE_NAME="${DOCKER_REGISTRY}/grpc-testing/psm-interop/${GRPC_LANGUAGE}-server"
      ;;
    node)
      CLIENT_IMAGE_NAME="${DOCKER_REGISTRY}/grpc-testing/psm-interop/${GRPC_LANGUAGE}-client"
      SERVER_IMAGE_NAME=""
      SERVER_IMAGE_USE_CANONICAL="1"
      ;;
    *)
      psm::tools::log "Unknown Language: ${1}"
      exit 1
      ;;
  esac

  case "${test_suite}" in
    url_map)
      # Uses the canonical server image configured in url-map.cfg
      SERVER_IMAGE_USE_CANONICAL="1"
      ;;
  esac

  declare -r CLIENT_IMAGE_NAME
  declare -r SERVER_IMAGE_NAME
  declare -r SERVER_IMAGE_USE_CANONICAL
}

psm::setup::test_driver() {
  if [[ -n "${KOKORO_ARTIFACTS_DIR}" ]]; then
    psm::setup::kokoro "${BUILD_SCRIPT_DIR}"
  else
    local_setup_test_driver "${BUILD_SCRIPT_DIR}"
  fi
}

#######################################
# Installs and configures the test driver for testing build script locally.
# Globals:
#   TEST_DRIVER_REPO_NAME The repository name of the test driver directory
#   TEST_DRIVER_REPO_DIR: The path to the test driver directory (optional)
#   SRC_DIR: Populated with absolute path to the source repo
#   KUBE_CONTEXT: Populated with name of kubectl context with GKE cluster access
#   SECONDARY_KUBE_CONTEXT: Populated with name of kubectl context with secondary GKE cluster
#      access, if any
#   TEST_DRIVER_FLAGFILE: Populated with relative path to test driver flagfile
#   TEST_XML_OUTPUT_DIR: Populated with the path to test xUnit XML report
#   GIT_ORIGIN_URL: Populated with the origin URL of git repo used for the build
#   GIT_COMMIT: Populated with the SHA-1 of git commit being built
#   GIT_COMMIT_SHORT: Populated with the short SHA-1 of git commit being built
# Arguments:
#   Absolute path to the directory with lang-specific buildscript, must be in the source repo.
# Outputs:
#   Writes the output to stdout, stderr, files
#######################################
psm::setup::kokoro() {
  local script_dir="${1:?${FUNCNAME[0]} missing the build script dir argument}"

  psm::tools::log "Starting Kokoro provisioning"
  # Capture Kokoro VM version info in the log.
  kokoro_print_version
  # Get testing version from the job name.
  kokoro_get_testing_version

  # Absolute path to the root of the source git repo.
  readonly SRC_DIR="$(git -C "${script_dir}" rev-parse --show-toplevel)"

  # Test artifacts dir: xml reports, logs, etc.
  local artifacts_dir="${KOKORO_ARTIFACTS_DIR}/artifacts"
  # Folders after $artifacts_dir reported as target name
  readonly TEST_XML_OUTPUT_DIR="${artifacts_dir}/${KOKORO_JOB_NAME}"
  readonly BUILD_LOGS_ROOT="${TEST_XML_OUTPUT_DIR}"

  mkdir -p "${artifacts_dir}" "${TEST_XML_OUTPUT_DIR}" "${BUILD_LOGS_ROOT}"
  parse_src_repo_git_info SRC_DIR
  kokoro_write_sponge_properties

  psm::tools::log "Installing packages with apt, see install-apt.log"
  kokoro_install_dependencies &> "${BUILD_LOGS_ROOT}/install-apt.log"

  # Get kubectl cluster credentials.
  psm::tools::log "Fetching GKE cluster credentials"
  gcloud_get_cluster_credentials

  # Install the driver.
  local test_driver_repo_dir
  test_driver_repo_dir="${TEST_DRIVER_REPO_DIR:-$(mktemp -d)/${TEST_DRIVER_REPO_NAME}}"
  test_driver_install "${test_driver_repo_dir}"
  # shellcheck disable=SC2034  # Used in the main script
  readonly TEST_DRIVER_FLAGFILE="config/grpc-testing.cfg"
}

# --- Common test build logic -----------

#######################################
# Builds test app and its docker images unless they already exist
# Globals:
#   SERVER_IMAGE_NAME: Test server Docker image name
#   CLIENT_IMAGE_NAME: Test client Docker image name
#   GIT_COMMIT: SHA-1 of git commit being built
#   PSM_FORCE_IMAGE_BUILD
# Arguments:
#   None
# Outputs:
#   Writes the output to stdout, stderr
#######################################
psm::build::docker_images_if_needed() {
  # Check if images already exist
  client_tags="$(gcloud_gcr_list_image_tags "${CLIENT_IMAGE_NAME}" "${GIT_COMMIT}")"
  psm::tools::log "Client image: ${CLIENT_IMAGE_NAME}:${GIT_COMMIT}"
  echo "${client_tags:-Client image not found}"

  if [[ -z "${SERVER_IMAGE_USE_CANONICAL}" ]]; then
    server_tags="$(gcloud_gcr_list_image_tags "${SERVER_IMAGE_NAME}" "${GIT_COMMIT}")"
    psm::tools::log "Server image: ${SERVER_IMAGE_NAME}:${GIT_COMMIT}"
    echo "${server_tags:-Server image not found}"
  else
    server_tags="ignored-use-canonical"
  fi

  # Build if any of the images are missing, or PSM_FORCE_IMAGE_BUILD=1
  if [[ "${PSM_FORCE_IMAGE_BUILD}" == "1" || -z "${server_tags}" || -z "${client_tags}" ]]; then
    {
      psm::tools::log "Building xDS interop test app Docker images"
      gcloud -q auth configure-docker "${DOCKER_REGISTRY}"
      # This method must be defined in the language-specific buildscript.
      psm::lang::build_docker_images
      psm::tools::log "Finished xDS interop test app Docker images"
    } |& tee -a "${BUILD_LOGS_ROOT}/build-docker.log"
  else
    psm::tools::log "Skipping ${GRPC_LANGUAGE} test app build"
  fi
}

#######################################
# Builds test app Docker images and pushes them to GCR
# Globals:
#   SRC_DIR: Absolute path to the source repo on Kokoro VM
#   SERVER_IMAGE_NAME: Test server Docker image name
#   CLIENT_IMAGE_NAME: Test client Docker image name
#   GIT_COMMIT: SHA-1 of git commit being built
#   DOCKER_REGISTRY: Docker registry to push to
# Arguments:
#   The path to xDS test client Dockerfile. Can be absolute or relative to SRC_DIR
#   The path to xDS test server Dockerfile. Optional, server build is skip when omitted.
# Outputs:
#   Writes the output of docker image build stdout, stderr
#######################################
psm::build::docker_images_generic() {
  local client_dockerfile="${1:?${FUNCNAME[0]} missing the client dockerfile argument}"
  local server_dockerfile="${2:-}"
  pushd "${SRC_DIR}"

  # Client is required.
  psm::tools::log "Building ${GRPC_LANGUAGE} xDS interop test client"
  psm::tools::run_verbose docker build -f "${client_dockerfile}" -t "${CLIENT_IMAGE_NAME}:${GIT_COMMIT}" .
  psm::tools::run_verbose docker push "${CLIENT_IMAGE_NAME}:${GIT_COMMIT}"

  # Server is optional
  if [[ -n "${server_dockerfile}" ]]; then
    psm::tools::log "Building ${GRPC_LANGUAGE} xDS interop test server"
    psm::tools::run_verbose docker build -f "${server_dockerfile}" -t "${SERVER_IMAGE_NAME}:${GIT_COMMIT}" .
    psm::tools::run_verbose docker push "${SERVER_IMAGE_NAME}:${GIT_COMMIT}"
  fi
  popd

  if is_version_branch "${TESTING_VERSION}"; then
    psm::tools::log "Detected proper version branch ${TESTING_VERSION}, adding version tags."
    tag_and_push_docker_image "${CLIENT_IMAGE_NAME}" "${GIT_COMMIT}" "${TESTING_VERSION}"
    if [[ -n "${server_dockerfile}" ]]; then
      tag_and_push_docker_image "${SERVER_IMAGE_NAME}" "${GIT_COMMIT}" "${TESTING_VERSION}"
    fi
  fi
}

# --- Common helpers ----------------------

#######################################
# Print the command with its arguments (aka set xtrace/ set -x) before execution.
# Arguments:
#   The command to run
# Returns:
#   The exit status of the command executed.
#######################################
psm::tools::run_verbose() {
  local exit_code=0
  set -x
  "$@" || exit_code=$?
  { set +x; } 2>/dev/null

  if (( exit_code == 0 )); then
    psm::tools::log "Cmd finished: ${1}"
  else
    psm::tools::log "Cmd failed with exit code ${exit_code}: $*"
  fi

  return ${exit_code}
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
psm::tools::run_ignore_exit_code() {
  local exit_code=0
  "$@" || exit_code=$?

  if (( exit_code == 0 )); then
    psm::tools::log "Cmd failed with exit code ${exit_code}: $*"
  fi
}

psm::tools::log() {
  echo -en "+ $(date "+[%H:%M:%S %Z]")\011 "
  echo "$@"
}

# --- "Unsorted" methods --------------
# TODO(sergiitk): all methods should be using "psm::" package name,
#   see https://google.github.io/styleguide/shellguide.html#function-names

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
    GKE_CLUSTER_DUALSTACK)
      GKE_CLUSTER_NAME="psm-interop-dualstack"
      GKE_CLUSTER_ZONE="us-central1-a"
      ;;
    *)
      psm::tools::log "Unknown GKE cluster: ${1}"
      exit 1
      ;;
  esac
  psm::tools::log -n "Activated GKE cluster: GKE_CLUSTER_NAME=${GKE_CLUSTER_NAME} "
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
      psm::tools::log "Unknown secondary GKE cluster: ${1}"
      exit 1
      ;;
  esac
  psm::tools::log -n "Activated secondary GKE cluster: SECONDARY_GKE_CLUSTER_NAME=${SECONDARY_GKE_CLUSTER_NAME}"
  echo " SECONDARY_GKE_CLUSTER_ZONE=${SECONDARY_GKE_CLUSTER_ZONE}"
}

#######################################
# Run command end report its exit code. Doesn't exit on non-zero exit code.
#
# Deprecated. Use psm::tools::run_ignore_exit_code
# TODO(sergiitk): delete this method when no longer used in per-language buildscripts.
#
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
    psm::tools::log "Cmd: '$*', exit code: ${exit_code}"
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
    psm::tools::log "Using exiting driver directory: ${TEST_DRIVER_REPO_DIR}."
  else
    psm::tools::log "Cloning driver to ${TEST_DRIVER_REPO_URL} branch ${TEST_DRIVER_BRANCH} to ${TEST_DRIVER_REPO_DIR}"
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
  psm::tools::log "Install python dependencies"
  cd "${TEST_DRIVER_FULL_DIR}"

  # Create and activate virtual environment unless already using one
  if [[ -z "${VIRTUAL_ENV}" ]]; then
    local venv_dir="${TEST_DRIVER_FULL_DIR}/venv"
    if [[ -d "${venv_dir}" ]]; then
      psm::tools::log "Found python virtual environment directory: ${venv_dir}"
    else
      psm::tools::log "Creating python virtual environment: ${venv_dir}"
      "python${PYTHON_VERSION}" -m venv "${venv_dir}" --upgrade-deps
    fi
    # Intentional: No need to check python venv activate script.
    # shellcheck source=/dev/null
    source "${venv_dir}/bin/activate"
  fi

  psm::tools::log "Installing Python packages with pip, see install-pip.log"
  psm::driver::pip_install &>> "${BUILD_LOGS_ROOT}/install-pip.log"
}

psm::driver::pip_install() {
  psm::tools::run_verbose python3 -m pip install -r requirements.lock
  echo
  psm::tools::run_verbose python3 -m pip list
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
  psm::tools::log "Generate python code from grpc.testing protos: ${protos[*]}"
  cd "${TEST_DRIVER_REPO_DIR}"
  python3 -m grpc_tools.protoc \
    --proto_path=. \
    --python_out="${TEST_DRIVER_FULL_DIR}" \
    --grpc_python_out="${TEST_DRIVER_FULL_DIR}" \
    "${protos[@]}"
  local protos_out_dir="${TEST_DRIVER_FULL_DIR}/${TEST_DRIVER_PROTOS_PATH}"

  if command -v sha256sum &> /dev/null; then
    psm::tools::log "Generated files ${protos_out_dir}:"
    find "${protos_out_dir}" -type f -exec sha256sum {} \;
  fi
}

#######################################
# Installs the test driver and it's requirements.
# https://github.com/grpc/psm-interop#basic-usage#installation
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
  psm::tools::log "Kokoro Ubuntu version:"
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
  psm::tools::log "Sponge properties:"
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
#   PSM_FORCE_TESTING_VERSION: Forces the testing version to be something else.
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

  if [[ -n "${PSM_FORCE_TESTING_VERSION}" ]]; then
    # Allows to override the testing version, and force tagging the built
    # images, if necessary.
    readonly TESTING_VERSION="${PSM_FORCE_TESTING_VERSION}"
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
#
# Deprecated. Use psm::setup::kokoro
#
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
  # TODO(sergiitk): Remove after per-lang buildscripts are backported.
  # Unset noisy verbose mode often set in the parent scripts.
  set +x

  psm::tools::log "Starting Kokoro provisioning"

  local src_repository_name="${1:?Usage kokoro_setup_test_driver GITHUB_REPOSITORY_NAME}"
  # Capture Kokoro VM version info in the log.
  kokoro_print_version

  # Get testing version from the job name.
  kokoro_get_testing_version

  # Kokoro clones repo to ${KOKORO_ARTIFACTS_DIR}/github/${GITHUB_REPOSITORY}
  local github_root="${KOKORO_ARTIFACTS_DIR}/github"
  readonly SRC_DIR="${github_root}/${src_repository_name}"

  # Test artifacts dir: xml reports, logs, etc.
  local artifacts_dir="${KOKORO_ARTIFACTS_DIR}/artifacts"
  # Folders after $artifacts_dir reported as target name
  readonly TEST_XML_OUTPUT_DIR="${artifacts_dir}/${KOKORO_JOB_NAME}"
  readonly BUILD_LOGS_ROOT="${TEST_XML_OUTPUT_DIR}"

  mkdir -p "${artifacts_dir}" "${TEST_XML_OUTPUT_DIR}" "${BUILD_LOGS_ROOT}"
  parse_src_repo_git_info SRC_DIR
  kokoro_write_sponge_properties

  psm::tools::log "Installing packages with apt, see install-apt.log"
  kokoro_install_dependencies &> "${BUILD_LOGS_ROOT}/install-apt.log"

  # Get kubectl cluster credentials.
  psm::tools::log "Fetching GKE cluster credentials"
  gcloud_get_cluster_credentials

  # Install the driver.
  local test_driver_repo_dir
  test_driver_repo_dir="${TEST_DRIVER_REPO_DIR:-$(mktemp -d)/${TEST_DRIVER_REPO_NAME}}"
  test_driver_install "${test_driver_repo_dir}"
  # shellcheck disable=SC2034  # Used in the main script
  readonly TEST_DRIVER_FLAGFILE="config/grpc-testing.cfg"
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
  if [[ -n "${PSM_FORCE_TESTING_VERSION}" ]]; then
    readonly TESTING_VERSION="${PSM_FORCE_TESTING_VERSION}"
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
  readonly BUILD_LOGS_ROOT="${TEST_XML_OUTPUT_DIR}"
  mkdir -p "${TEST_XML_OUTPUT_DIR}" "${BUILD_LOGS_ROOT}"
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

  psm::tools::run_verbose docker tag "${image_name}:${from_tag}" "${image_name}:${to_tag}"
  psm::tools::run_verbose docker push "${image_name}:${to_tag}"
}
