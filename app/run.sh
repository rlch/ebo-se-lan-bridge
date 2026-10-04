#!/usr/bin/env bash
# Entrypoint for the EBO-SE bridge container.
# Credentials are NEVER baked into the image: they come from the environment.
set -e
APP=/opt/ebo

for v in EBO_LICENSE EBO_UID EBO_AUTHKEY EBO_IDENTITY EBO_TOKEN; do
  [ -n "${!v:-}" ] || echo "[run] $v is not set: the web app runs but cannot reach the robot" >&2
done
: "${EBO_STREAM_USER:=ebo}"; : "${EBO_STREAM_PASS:?missing stream_pass (RTSP password)}"

# Extra WebRTC ICE hosts (comma-separated IPs/names the browser can reach, e.g. a Tailscale IP)
# for when the container's own interface addresses are not routable from the viewer.
WEBRTC_HOSTS=$(printf '%s' "${EBO_WEBRTC_HOSTS:-}" | tr -d ' ')
WEBRTC_HOSTS="[${WEBRTC_HOSTS}]"

# Render the mediamtx config with the stream credentials
sed -e "s/__STREAM_USER__/${EBO_STREAM_USER}/g" -e "s/__STREAM_PASS__/${EBO_STREAM_PASS}/g" \
    -e "s/__WEBRTC_HOSTS__/${WEBRTC_HOSTS}/g" -e "s/__WEBRTC_UDP__/${EBO_WEBRTC_UDP_PORT:-8189}/g" \
    "$APP/mediamtx.template.yml" > "$APP/mediamtx.yml"

cd "$APP"
# A Docker container already provides an isolated PID namespace (low PIDs), which the
# 32-bit bionic libc requires (pid <= 65535). No 'unshare' needed inside a container.
exec python3 "$APP/ebo_server.py"
