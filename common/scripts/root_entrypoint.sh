#!/bin/bash
#set -x #echo on

# Check if the user exists, and if not, create it without the home directory
if ! id -u "$username" &>/dev/null; then
  useradd -u "$user_id" -M "$username"
fi

# Check if the group exists, and if not, modify it
if ! getent group "$username" &>/dev/null; then
  groupmod -g "$group_id" "$username"
fi

if [ -f "/usr/local/bin/workload_root_entrypoint.sh" ]; then
  bash /usr/local/bin/workload_root_entrypoint.sh $APPNAME
fi

# Resolve DynamoRIO before chmod (partial images may lack the baked install).
if [ -z "${DYNAMORIO_HOME:-}" ] || [ ! -e "${DYNAMORIO_HOME}/lib64/release/libdynamorio.so" ]; then
  if [ -e "/tmp_home/DynamoRIO-Linux-10.0.0/exports/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="/tmp_home/DynamoRIO-Linux-10.0.0/exports"
  elif [ -e "/tmp_home/DynamoRIO-Linux-10.0.0/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="/tmp_home/DynamoRIO-Linux-10.0.0"
  elif [ -e "/home/${username}/build/opt/deps/dynamorio/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="/home/${username}/build/opt/deps/dynamorio"
  elif [ -e "/tmp_home/application/scarab/src/build/opt/deps/dynamorio/lib64/release/libdynamorio.so" ]; then
    export DYNAMORIO_HOME="/tmp_home/application/scarab/src/build/opt/deps/dynamorio"
  fi
fi

if [ -n "${DYNAMORIO_HOME:-}" ] && [ -e "${DYNAMORIO_HOME}/lib64/release/libdynamorio.so" ]; then
  chmod 777 "${DYNAMORIO_HOME}/lib64/release/libdynamorio.so" || true
fi
