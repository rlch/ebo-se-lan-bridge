# Mac host over Tailscale

Runs the bridge on a Mac on the robot's LAN and serves the web app to your phone over the
tailnet. The robot is reached locally (no vendor relay); the phone only needs Tailscale.

Tested on an Apple-silicon Mac mini (macOS 14) with OrbStack and the Tailscale GUI app.

## 1. Build inputs

Populate `vendor/` with the **ARM64** libraries and bionic runtime and build the ARM64
bridge (docs/SETUP.md §1, §2, §5):

```bash
NDK=/opt/homebrew/share/android-ndk EBO_ARCH=arm64 \
  EBO_OUTPUT=app/ebo_bridge-arm64 bash scripts/build_bridge.sh
```

Pages are 4K inside the OrbStack VM, which the Android linker accepts.

## 2. Configure

`.env` in the repo root, as in `.env.example`, plus:

```bash
EBO_WEBRTC_HOSTS=<the Mac's Tailscale IPv4>   # tailscale ip -4
EBO_AUDIO=1                                   # optional: robot microphone
```

`deploy/macos/compose.yml` sets `EBO_BIND=0.0.0.0`, `EBO_PORT=8010` and
`EBO_BRIDGE_BIN=ebo_bridge-arm64`. Override the port with `EBO_PORT=… docker compose …`.

## 3. Run

```bash
cd deploy/macos && docker compose up -d --build
```

Host networking on macOS stays inside the Linux VM, so this compose file publishes ports
instead: the app on `127.0.0.1:8010` and WebRTC media on UDP `8189` on every interface.
mediamtx advertises `EBO_WEBRTC_HOSTS` as an ICE candidate so the phone sends media to the
Mac's Tailscale address.

## 4. HTTPS on the tailnet

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8010
```

This works over SSH with the GUI app (unlike `tailscale set`), persists across restarts, and
serves `https://<mac>.<tailnet>.ts.net/` with a Let's Encrypt certificate, tailnet only. The
first request after enabling can fail while the certificate is issued.

## 5. Install on the phone

Open the URL in Chrome (Android) or Safari (iOS) with Tailscale connected, then *Install app*
/ *Add to Home Screen*. Close ROLA while using it: the robot accepts one client at a time.

If the video stays on "Starting video" over WebRTC, pick **HLS** in the controls sheet, or set
`EBO_VIDEO=h264` when the browser can't decode HEVC over WebRTC.
