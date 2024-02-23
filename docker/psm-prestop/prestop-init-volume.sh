#!/bin/sh

readonly TARGET_DIR="${1:?Usage prestop-init-volume.sh TARGET_DIR}"
mkdir -vp "${TARGET_DIR}"
cp -rv ./grpcurl "${TARGET_DIR}"
cp -rv ./protos "${TARGET_DIR}"
