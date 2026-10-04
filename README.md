# EBO-SE Bridge

A **phone-free** bridge for the **Enabot EBO SE** robot, with its own installable web app.
It runs the official TUTK/Kalay libraries on an ARM Linux host (or a Mac via OrbStack) next
to the robot, and serves a React PWA for live view, driving and talkback — so the robot is
reached over your own network (e.g. Tailscale) instead of the vendor's P2P relay.

It connects directly to the robot over the LAN (Kalay P2P + DTLS-PSK), receives the live
**H.265 1080p30 video** with **G.711 microphone audio**, provides **push-to-talk audio to
the robot speaker**, and lets you **drive it and control it** (analog joystick, dock,
wake/sleep, eye lights, night vision, collision/fall protection, patrol), with **battery**
status.

> You provide your own robot credentials and the TUTK libraries (extracted from your own
> device). This repo contains only the integration code — see [docs/SETUP.md](docs/SETUP.md).

## How it works

```
 Robot ◄──Kalay P2P / DTLS──► ebo_bridge (native, bionic)
                                 │ H.265/G.711             ▲ control + speaker PCM (fd3)
                                 ▼                         │
                            ffmpeg ──► mediamtx (RTSP/WebRTC/HLS)
                                          │                │
          browser / PWA ◄── WHEP, LL-HLS ─┴── ebo_server.py (supervisor, API, web app)
```

- **The bridge host holds all the secrets.** Browsers only talk to `ebo_server.py`; the
  WHEP and HLS endpoints are proxied same-origin with the stream credentials added server-side.
- **Video is passthrough** (`ffmpeg -c:v copy`, ~5% CPU). Set `EBO_VIDEO=h264` to transcode
  for browsers that can't decode HEVC over WebRTC.
- **Battery/status** comes from the robot over the reliable channel (`RDT_Read`).

## Components

| Path | Role |
|------|------|
| `app/ebo_bridge.c` / `app/ebo_bridge` | native client: connects, video→stdout, control←fd3 |
| `app/ebo_server.py` | supervisor: spawns mediamtx+ffmpeg+bridge, REST/WebSocket API, serves the web app |
| `web/` | the web app: Vite + React + TypeScript PWA, built into the image |
| `app/ebo.html` | the original single-file panel, still served at `/classic` |
| `app/mediamtx.template.yml` | RTSP/WebRTC/HLS server config (auth + ICE hosts injected at start) |
| `app/run.sh` | container entrypoint |
| `Dockerfile`, `docker-compose.yml` | packaging (ARM Linux host, host networking) |
| `deploy/macos/compose.yml` | macOS host behind `tailscale serve` (published ports) |
| `vendor/` | **you provide**: TUTK `.so`, bionic runtime, optional `ioctl9930.bin` (gitignored) |

## Quick start (ARM Linux host)

```bash
git clone <this-repo> && cd ebo-se-bridge
# 1) Populate ./vendor (TUTK libs + bionic runtime) — see docs/SETUP.md
# 2) Configure
cp .env.example .env && nano .env          # robot creds + stream/web auth
# 3) Build & run
docker compose up -d --build
```

The web app binds to `127.0.0.1:8000` by default and is meant to be exposed over **HTTPS**
(a reverse proxy, Cloudflare Tunnel or `tailscale serve`): installing the PWA and using the
microphone both need a secure context. To reach it directly on the LAN over plain HTTP
instead, set `EBO_BIND=0.0.0.0` and open `http://<host>:8000`.

## Mac host over Tailscale

[docs/TAILSCALE.md](docs/TAILSCALE.md) runs the bridge on a Mac on the robot's LAN
(OrbStack) and serves the app at `https://<mac>.<tailnet>.ts.net` to your phone, anywhere.

## The web app

- **Live view**: WebRTC (WHEP) for lowest latency, falling back to LL-HLS when the browser
  can't take the codec or ICE can't connect. Video mode is selectable in the controls sheet.
- **Drive**: analog joystick (or arrows/WASD) streamed over `/ws/drive` at 15 Hz; the server
  stops the robot if updates stop for 350 ms or the socket closes.
- **Talk**: hold the mic button; 8 kHz PCM goes over `/ws/talk` to the robot speaker. One
  talker at a time; releasing or disconnecting always sends the native speaker STOP. See
  [docs/TALKBACK.md](docs/TALKBACK.md).
- **Snapshot**, **listen** (robot mic, `EBO_AUDIO=1`), **fullscreen**, screen wake lock, and a
  sheet with wake/sleep/dock and the robot feature toggles.
- **Release for ROLA**: the robot accepts one client; this frees it for the vendor app.

Develop with `cd web && pnpm install && EBO_DEV_TARGET=http://<bridge>:8000 pnpm dev`.

For an x86-64, non-Docker deployment see [docs/HOST_QEMU.md](docs/HOST_QEMU.md).

## Security

RTSP, WebRTC and HLS are authenticated; the web app has optional HTTP basic auth (`EBO_WEB_USER`/`EBO_WEB_PASS`)
and should otherwise only be reachable on a private network such as your tailnet.
Keep the bridge host on a trusted network; the stream/control are only as private as those credentials.
The local ffmpeg publisher is admitted anonymously only from loopback, keeping reader
credentials out of process arguments.

## Status

Video, robot microphone audio, control, battery and diagnostics have been tested on the
original ARM32 Raspberry Pi path. The new speaker path was live-tested with the owned ARM64
ROLA libraries on a rooted Pixel 3: channel 2 became ready, audio frames were accepted, STOP
completed, and the process exited cleanly. The ARM64 ODROID-C2 container deployment remains
to be validated on that host.

## Credits

- **Original project and core bridge:** [lilium360](https://github.com/lilium360)
- **Two-way audio research and implementation:** GPT-5.6-Sol by OpenAI
- Opus 5.5 by Anthropic
- [Same Sun Foundation](https://www.samesun.foundation)
- From Porch in Nuremberg, by Brothers: Grandpa, Phil, Ghost, Fourth

## Legal & disclaimer

**Not affiliated.** This is an independent, community project. It is **not** affiliated with,
authorized, sponsored, or endorsed by Enabot or ThroughTek. *Enabot*, *EBO*, *ROLA*, *TUTK* and
*Kalay* are trademarks of their respective owners; they are used here only nominatively, to
describe interoperability with those products.

**No proprietary components are redistributed.** This repository contains **only original code**.
The TUTK/Kalay SDK and the Enabot/ROLA application are proprietary and are **not** included,
copied, or derived here — no SDK libraries, no Android runtime, no app code, no keys. You obtain
those components yourself, **from a device and software you own and are licensed to use** (see
[docs/SETUP.md](docs/SETUP.md)).

**Interoperability & personal use.** This project exists to interoperate with **hardware you own**,
for personal use and research. You are solely responsible for complying with your local laws and
with the terms of service / EULA of the Enabot app and the ThroughTek SDK when using your own
device and credentials. **Use at your own risk.**

**License.** The original code in this repository is released under the **MIT License** (see
[LICENSE](LICENSE)). The MIT license covers *this project's code only* — it grants **no rights** to
any third-party component (TUTK SDK, Android runtime, etc.), which remain under their own licenses.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND. See [LICENSE](LICENSE).
