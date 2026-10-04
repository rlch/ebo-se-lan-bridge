#!/usr/bin/env bash
# Entrypoint for the EBO-SE bridge container.
# Credentials are NEVER baked into the image: they come from the environment.
set -e
APP=/opt/ebo

: "${EBO_LICENSE:?missing license_key}"; : "${EBO_UID:?missing uid}"; : "${EBO_AUTHKEY:?missing authkey}"
: "${EBO_IDENTITY:?missing av_identity}"; : "${EBO_TOKEN:?missing av_token}"
: "${EBO_STREAM_USER:=ebo}"; : "${EBO_STREAM_PASS:?missing stream_pass (RTSP password)}"

# Render the mediamtx config with the stream credentials
sed -e "s/__STREAM_USER__/${EBO_STREAM_USER}/g" -e "s/__STREAM_PASS__/${EBO_STREAM_PASS}/g" \
    "$APP/mediamtx.template.yml" > "$APP/mediamtx.yml"

cd "$APP"
# A Docker container already provides an isolated PID namespace (low PIDs), which the
# 32-bit bionic libc requires (pid <= 65535). No 'unshare' needed inside a container.
exec python3 "$APP/ebo_server.py"
