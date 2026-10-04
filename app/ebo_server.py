"""
EBO-SE bridge supervisor.

Spawns mediamtx (RTSP/WebRTC/HLS server), ffmpeg (H.265 passthrough, no transcode)
and the native ebo_bridge. Forwards video frames to ffmpeg, parses inbound status
(battery), and exposes a small control API plus the web app.

Required env:  EBO_LICENSE EBO_UID EBO_AUTHKEY EBO_IDENTITY EBO_TOKEN
Stream auth:   EBO_STREAM_USER EBO_STREAM_PASS         (mediamtx)
Web panel:     EBO_WEB_USER EBO_WEB_PASS               (HTTP basic; disabled if unset)
Video:         EBO_VIDEO (copy | h264)  EBO_H264_HEIGHT (720)  EBO_H264_BITRATE (2500k)
Other:         EBO_DIR (/opt/ebo)  EBO_PORT (8000)  EBO_BIND (127.0.0.1)  EBO_WEB_DIR (<EBO_DIR>/web)
"""
import os, time, struct, threading, subprocess, secrets, socket, base64, urllib.request, urllib.error, http.cookiejar
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from pydantic import BaseModel
import uvicorn

EBO_DIR = os.environ.get("EBO_DIR", "/opt/ebo")
PORT = int(os.environ.get("EBO_PORT", "8000"))
# Bind the web panel to loopback by default: it is reached through the Cloudflare
# tunnel (or a reverse proxy), not exposed directly on the LAN. Set EBO_BIND=0.0.0.0
# to expose it on the LAN instead.
BIND_HOST = os.environ.get("EBO_BIND", "127.0.0.1")
# Robot audio (listen-only). DISABLED by default: muxing the robot's G.711 added latency to both
# audio and video, so the stream is kept video-only for the smooth, low-latency experience.
# Set EBO_AUDIO=1 to re-enable (G.711 8 kHz mono, codec 0x8a; EBO_AUDIO_FMT=alaw if distorted).
AUDIO_ON = os.environ.get("EBO_AUDIO", "0") == "1"
AUDIO_FMT = os.environ.get("EBO_AUDIO_FMT", "mulaw")   # mulaw | alaw
AUDIO_RATE = os.environ.get("EBO_AUDIO_RATE", "8000")
# Audio processing (ffmpeg -af). Default "none" = raw G.711 passthrough (PCMU), lowest latency —
# this is the smooth, real-time path. Setting a filter chain enables denoise/volume but transcodes
# to Opus, which adds latency (the player then delays video to stay A/V-synced). Opt-in only, e.g.:
#   EBO_AUDIO_FILTER=highpass=f=200,afftdn=nr=20,dynaudnorm=g=11:m=15,alimiter=limit=0.9
AUDIO_FILTER = os.environ.get("EBO_AUDIO_FILTER", "none")
# Video: "copy" passes the robot's H.265 through untouched (no CPU). "h264" transcodes to
# H.264 for browsers that cannot decode HEVC over WebRTC (costs CPU on the bridge host).
VIDEO_MODE = os.environ.get("EBO_VIDEO", "copy")
H264_HEIGHT = int(os.environ.get("EBO_H264_HEIGHT", "720"))
H264_BITRATE = os.environ.get("EBO_H264_BITRATE", "2500k")
# The native bridge exits at once without these; the web app still runs and says so.
CONFIGURED = all(os.environ.get(k) for k in ("EBO_LICENSE", "EBO_UID", "EBO_AUTHKEY", "EBO_IDENTITY", "EBO_TOKEN"))
WEB_DIR = Path(os.environ.get("EBO_WEB_DIR", os.path.join(EBO_DIR, "web")))
RTSP_PATH = "ebo"
STREAM_USER = os.environ.get("EBO_STREAM_USER", "ebo")
STREAM_PASS = os.environ.get("EBO_STREAM_PASS", "")
WEB_USER = os.environ.get("EBO_WEB_USER")
WEB_PASS = os.environ.get("EBO_WEB_PASS")

def _has_param_set(data, codec):
    """True if an Annex-B frame carries codec parameter sets, i.e. is a clean
    point for ffmpeg to start: HEVC VPS(32)/SPS(33), H.264 SPS(7)."""
    i, n = 0, len(data)
    while i + 4 < n:
        if data[i] == 0 and data[i+1] == 0 and (data[i+2] == 1 or (data[i+2] == 0 and i+3 < n and data[i+3] == 1)):
            j = i + (3 if data[i+2] == 1 else 4)
            if j < n:
                if codec == 80:                       # HEVC
                    t = (data[j] >> 1) & 0x3F
                    if t == 32 or t == 33:
                        return True
                else:                                 # H.264
                    if (data[j] & 0x1F) == 7:
                        return True
            i = j
        else:
            i += 1
    return False

# ---------------- MAVLink builders (control over RDT) ----------------
def mavlink_crc(data, crc_extra):
    crc = 0xFFFF
    for b in data:
        t = b ^ (crc & 0xFF); t = (t ^ (t << 4)) & 0xFF
        crc = ((crc >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xFFFF
    t = crc_extra ^ (crc & 0xFF); t = (t ^ (t << 4)) & 0xFF
    crc = ((crc >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xFFFF
    return crc

def _frame(msgid, payload, crc_extra):
    hdr = bytes([len(payload), 0, 0, 0, msgid])
    return bytes([0xfe]) + hdr + payload + struct.pack('<H', mavlink_crc(hdr + payload, crc_extra))

def motor_frame(ly=0.0, rx=0.0, lx=0.0, ry=0.0, buttons=0):
    # ly>0 = forward (robot convention is inverted -> negate)
    return _frame(202, struct.pack('<ffff', lx, -ly, rx, ry) + bytes([buttons & 0xFF]), 211)

def param_set_frame(group, key, value, ptype=11):
    pid = f"{group}-{key}".encode()[:32].ljust(32, b'\x00')
    return _frame(229, struct.pack('<f', value) + bytes([255, 3]) + pid + bytes([ptype]), 208)

def command_frame(command):
    return _frame(200, struct.pack('<H', command) + bytes([255, 1]), 196)

CMD_DOCK = 40154

PARAM_TOGGLES = {
    "eyes_on": ("display", "enable", 1.0), "eyes_off": ("display", "enable", 0.0),
    "night_on": ("video", "night_vision", 1.0), "night_off": ("video", "night_vision", 0.0),
    "avoid_on": ("control", "auto_avoidance", 1.0), "avoid_off": ("control", "auto_avoidance", 0.0),
    "fall_on": ("control", "fallarrest", 1.0), "fall_off": ("control", "fallarrest", 0.0),
    "patrol_on": ("security_patrol", "enable", 1.0), "patrol_off": ("security_patrol", "enable", 0.0),
    "sleep": ("power", "sleep", 1.0), "wake": ("power", "sleep", 0.0),
}

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"


class Bridge:
    def __init__(self):
        self.frame_count = 0
        self.last_frame = 0.0   # time.time() of the last video frame (for awake/sleep detection)
        self.audio_codec = None # codec_id of inbound audio frames (discovered at runtime)
        self.audio_flags = None # TUTK audio flags (sample rate / bits / channel)
        self.audio_count = 0
        self.connected = False
        self.codec_name = None
        self.battery = -1
        self.charge = -1   # 0 = unplugged, 1 = charging/docked
        self._ctrl_r, self._ctrl_w = os.pipe()
        self._a_r, self._a_w = os.pipe()    # robot audio -> ffmpeg second input
        os.set_inheritable(self._a_r, True)
        os.set_blocking(self._a_w, False)   # never block: drop audio if ffmpeg lags
        self._a_buf = bytearray()           # jitter buffer for robot audio
        self._a_lock = threading.Lock()
        self._ctrl_lock = threading.Lock()  # keep fd3 frames atomic across HTTP/WebSocket threads
        self._speaker_ready = threading.Event()
        self._speaker_error = None
        self._speaker_state_lock = threading.Lock()
        self._running = True
        self._ff_primed = False   # becomes True once ffmpeg has been fed an HEVC keyframe
        self.paused = False       # when True the native bridge is stopped, freeing the robot for the app
        self._start_mediamtx()
        time.sleep(0.6)
        self._start_ffmpeg()
        self._start_bridge()
        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._log_stderr, args=(self.proc,), daemon=True).start()
        if AUDIO_ON:
            threading.Thread(target=self._audio_feeder, daemon=True).start()

    def _audio_feeder(self):
        # Emit a smooth real-time G.711 stream to ffmpeg: robot audio when available, silence
        # otherwise. A continuous audio input keeps ffmpeg from blocking on an empty pipe (which
        # would stall the muxer and, with it, the video). Silence byte: 0xFF µ-law / 0xD5 A-law.
        rate = int(AUDIO_RATE); chunk = max(80, rate // 50); interval = chunk / rate   # ~20 ms
        silence = (b"\xff" if AUDIO_FMT == "mulaw" else b"\xd5") * chunk
        next_t = time.monotonic()
        while self._running:
            next_t += interval
            with self._a_lock:
                if len(self._a_buf) >= chunk:
                    out = bytes(self._a_buf[:chunk]); del self._a_buf[:chunk]
                elif self._a_buf:
                    out = bytes(self._a_buf) + silence[len(self._a_buf):]; self._a_buf.clear()
                else:
                    out = silence
            try: os.write(self._a_w, out)
            except Exception: pass
            d = next_t - time.monotonic()
            if d > 0: time.sleep(d)
            else: next_t = time.monotonic()

    def is_awake(self):
        # The camera only streams when the robot is awake; treat "a video frame within
        # the last few seconds" as awake, otherwise the robot is sleeping / camera off.
        return (time.time() - self.last_frame) < 6.0

    def _start_mediamtx(self):
        self.mtx = subprocess.Popen([os.path.join(EBO_DIR, "mediamtx"), os.path.join(EBO_DIR, "mediamtx.yml")],
                                    cwd=EBO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[supervisor] mediamtx pid", self.mtx.pid, flush=True)

    def _start_ffmpeg(self):
        # MediaMTX permits anonymous publishing only from loopback. Keep reader
        # credentials out of ffmpeg's process arguments and /proc command line.
        url = f"rtsp://127.0.0.1:8554/{RTSP_PATH}"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning",
               "-fflags", "nobuffer", "-flags", "low_delay",
               "-analyzeduration", "500000", "-probesize", "1000000",
               "-thread_queue_size", "256", "-f", "hevc", "-i", "pipe:0"]
        pass_fds = ()
        if AUDIO_ON:
            # second input: raw G.711 from the robot, passed through as PCMU/PCMA (no transcode).
            # -max_interleave_delta 0 = emit packets immediately, never hold video to wait for
            # audio interleaving (that buffering was adding seconds of latency to both).
            if AUDIO_FILTER and AUDIO_FILTER.lower() != "none":
                aout = ["-af", AUDIO_FILTER, "-c:a", "libopus", "-application", "voip",
                        "-b:a", "24k", "-ar", "48000", "-ac", "1"]
            else:
                aout = ["-c:a", "copy"]   # raw G.711 passthrough (PCMU/PCMA), no processing
            cmd += ["-use_wallclock_as_timestamps", "1", "-thread_queue_size", "256",
                    "-f", AUDIO_FMT, "-ar", AUDIO_RATE, "-ac", "1", "-i", f"pipe:{self._a_r}",
                    "-map", "0:v", "-map", "1:a"] + self._video_args() + aout + \
                   ["-max_interleave_delta", "0"]
            pass_fds = (self._a_r,)
        else:
            cmd += self._video_args() + ["-an"]
        cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", url]
        self.ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, pass_fds=pass_fds)
        print("[supervisor] ffmpeg pid", self.ff.pid, flush=True)

    @staticmethod
    def _video_args():
        if VIDEO_MODE != "h264":
            return ["-c:v", "copy"]
        # Low-latency H.264 for WebRTC: no B-frames, 1 s GOP so a new viewer starts fast.
        return ["-vf", f"scale=-2:{H264_HEIGHT}", "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "zerolatency", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
                "-g", "30", "-bf", "0", "-b:v", H264_BITRATE, "-maxrate", H264_BITRATE,
                "-bufsize", H264_BITRATE]

    def _restart_ffmpeg(self):
        # ffmpeg died (e.g. broken pipe). Restart it and re-sync at the next keyframe.
        try: self.ff.stdin.close()
        except Exception: pass
        try: self.ff.kill()
        except Exception: pass
        self._ff_primed = False
        self._start_ffmpeg()
        print("[supervisor] ffmpeg restarted", flush=True)

    def _start_bridge(self):
        linker = os.path.join(EBO_DIR, "bionic", "linker")
        bridge_name = os.environ.get("EBO_BRIDGE_BIN", "ebo_bridge")
        if os.path.basename(bridge_name) != bridge_name:
            raise ValueError("EBO_BRIDGE_BIN must be a filename")
        bridge = os.path.abspath(os.path.join(EBO_DIR, bridge_name))
        qemu = os.environ.get("EBO_QEMU")
        command = [qemu, linker, bridge] if qemu else [linker, bridge]
        env = dict(os.environ)
        env["EBO_LIB_DIR"] = os.path.join(EBO_DIR, "lib")
        env["EBO_IOCTL9930"] = os.path.join(EBO_DIR, "ioctl9930.bin")
        env["LD_LIBRARY_PATH"] = os.path.join(EBO_DIR, "bionic") + ":" + os.path.join(EBO_DIR, "lib")
        self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, pass_fds=(self._ctrl_r,),
                                    preexec_fn=lambda: os.dup2(self._ctrl_r, 3))
        with self._speaker_state_lock:
            self._speaker_error = None
            self._speaker_ready.clear()
        print("[supervisor] bridge pid", self.proc.pid, flush=True)

    def pause_bridge(self):
        # Stop the native bridge -> releases the robot's single P2P session so the Enabot app
        # can connect. ffmpeg/mediamtx keep running; video just stops until resumed.
        if self.paused:
            return
        self.paused = True
        self.connected = False
        try: self.speaker_stop()
        except Exception: pass
        print("[supervisor] pausing bridge (releasing robot for the app)", flush=True)
        try: self.proc.terminate()
        except Exception: pass
        try: self.proc.wait(timeout=3)
        except Exception:
            try: self.proc.kill()
            except Exception: pass

    def resume_bridge(self):
        # Reconnect to the robot (will fail/retry while the native app still holds the session).
        if not self.paused:
            return
        self.paused = False
        self._ff_primed = False
        print("[supervisor] resuming bridge", flush=True)
        self._start_bridge()
        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._log_stderr, args=(self.proc,), daemon=True).start()

    def _log_stderr(self, proc):
        for line in iter(proc.stderr.readline, b""):
            s = line.decode(errors="replace").rstrip()
            print("[bridge]", s, flush=True)
            if "connected" in s:
                self.connected = True
            if "speaker ready " in s:
                with self._speaker_state_lock:
                    self._speaker_error = None
                    self._speaker_ready.set()
            elif "avServStartEx speaker err=" in s:
                with self._speaker_state_lock:
                    self._speaker_error = s.rsplit("=", 1)[-1]
                    self._speaker_ready.set()
        if proc is self.proc:
            self.connected = False
            with self._speaker_state_lock:
                if not self._speaker_ready.is_set():
                    self._speaker_error = "bridge disconnected"
                    self._speaker_ready.set()

    def _read_frames(self):
        f = self.proc.stdout
        while self._running:
            hdr = f.read(5)
            if len(hdr) < 5:
                break
            n = struct.unpack("<I", hdr[:4])[0]; codec = hdr[4]
            data = f.read(n)
            if len(data) < n:
                break
            if codec == 0xFF:
                self._parse_status(data)   # inbound MAVLink (battery/status)
                continue
            if codec == 0xA0:              # audio frame: [codec_id:1][flags:1][data]
                if len(data) >= 2:
                    self.audio_codec, self.audio_flags = data[0], data[1]
                    self.audio_count += 1
                    if AUDIO_ON and len(data) > 2:
                        with self._a_lock:
                            self._a_buf += data[2:]
                            if len(self._a_buf) > 1600:      # cap latency (~200ms): drop oldest
                                del self._a_buf[:len(self._a_buf) - 1600]
                continue
            self.frame_count += 1
            self.last_frame = time.time()
            self.codec_name = "hevc" if codec == 80 else ("h264" if codec == 78 else f"codec{codec}")
            # ffmpeg must start at a keyframe: skip frames until one carries the
            # parameter sets (HEVC VPS/SPS, H.264 SPS), otherwise it can't determine
            # the stream dimensions and exits ("dimensions not set").
            if not self._ff_primed:
                if not _has_param_set(data, codec):
                    continue
                self._ff_primed = True
            try:
                self.ff.stdin.write(data); self.ff.stdin.flush()
            except Exception:
                self._restart_ffmpeg()

    def _parse_status(self, u):
        i = 0
        while i + 8 <= len(u):
            if u[i] == 0xfe:
                plen = u[i+1]; msgid = u[i+5] if i+5 < len(u) else -1
                if msgid == 207 and i + 24 <= len(u):   # BATTERY_STATUS
                    bp = u[i+22]; cs = u[i+23]
                    self.battery = bp; self.charge = cs
                i += 8 + plen + 2 if plen else i + 1
            else:
                i += 1

    def _send_command(self, kind: int, data: bytes = b""):
        if not 0 <= kind <= 255:
            raise ValueError("invalid command kind")
        payload = bytes((kind,)) + data
        framed = struct.pack("<I", len(payload)) + payload
        with self._ctrl_lock:
            view = memoryview(framed)
            while view:
                written = os.write(self._ctrl_w, view)
                if written <= 0:
                    raise BrokenPipeError("bridge control pipe closed")
                view = view[written:]

    def send_rdt(self, mavlink: bytes):
        self._send_command(0, mavlink)

    def send_ioctl(self, io_type: int, data: bytes = b""):
        self._send_command(1, struct.pack("<H", io_type) + data)

    def speaker_start(self, timeout: float = 12.0):
        if self.paused or not self.proc or self.proc.poll() is not None:
            raise RuntimeError("bridge is not running")
        with self._speaker_state_lock:
            self._speaker_error = None
            self._speaker_ready.clear()
        self._send_command(2)
        if not self._speaker_ready.wait(timeout):
            try: self._send_command(4)
            except Exception: pass
            raise TimeoutError("speaker start timed out")
        with self._speaker_state_lock:
            error = self._speaker_error
        if error:
            try: self._send_command(4)
            except Exception: pass
            raise RuntimeError(f"speaker start failed ({error})")

    def speaker_send_pcm(self, pcm16le: bytes):
        if not pcm16le or len(pcm16le) > 1280 or len(pcm16le) % 2:
            raise ValueError("PCM must be even-length, nonempty, and at most 1280 bytes")
        self._send_command(3, pcm16le)

    def speaker_stop(self):
        try:
            self._send_command(4)
        finally:
            with self._speaker_state_lock:
                self._speaker_error = None
                self._speaker_ready.clear()


# ---------------- actions ----------------
def do_action(name: str) -> bool:
    if name == "dock":
        for _ in range(5): bridge.send_rdt(command_frame(CMD_DOCK)); time.sleep(0.15)
    elif name == "undock":
        for _ in range(6): bridge.send_rdt(motor_frame(ly=0.5)); time.sleep(0.05)
        bridge.send_rdt(motor_frame())
    elif name in PARAM_TOGGLES:
        g, k, v = PARAM_TOGGLES[name]; bridge.send_rdt(param_set_frame(g, k, v))
    else:
        return False
    return True

def do_move(ly: float, rx: float, duration: float = 0.4):
    n = max(1, int(duration * 20))
    for _ in range(n):
        bridge.send_rdt(motor_frame(ly=ly, rx=rx)); time.sleep(0.05)
    bridge.send_rdt(motor_frame())


# ---------------- HTTP API + web panel (optional basic auth) ----------------
bridge = None
_talk_owner = threading.Lock()

def _valid_basic_auth(header: str | None) -> bool:
    if not WEB_USER:
        return True
    try:
        scheme, encoded = (header or "").split(" ", 1)
        if scheme.lower() != "basic":
            return False
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeError):
        return False
    return (secrets.compare_digest(username, WEB_USER) and
            secrets.compare_digest(password, WEB_PASS or ""))

def require_auth(connection: HTTPConnection):
    if connection.scope["type"] == "websocket":
        return  # WebSockets are checked explicitly before accept().
    if connection.url.path == "/sleep.svg":   # public: harmless fallback graphic, usable as a card image
        return
    if not _valid_basic_auth(connection.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})

app = FastAPI(dependencies=[Depends(require_auth)])

@app.get("/classic", response_class=HTMLResponse)
def classic():
    p = Path(__file__).parent / "ebo.html"
    return p.read_text(encoding="utf-8") if p.exists() else "<h1>EBO-SE</h1>"

@app.get("/sleep.svg")
def sleep_image():
    p = Path(__file__).parent / "sleep.svg"
    if not p.exists():
        raise HTTPException(status_code=404)
    return Response(content=p.read_text(encoding="utf-8"), media_type="image/svg+xml")

# WHEP (WebRTC) signaling proxy: the panel (already behind web auth) posts its SDP offer
# here same-origin, and we forward it to mediamtx with the stream credentials in the
# Authorization header — so no credentials ever appear in a browser URL (Chrome blocks
# subresource URLs with embedded credentials). Media still flows peer-to-peer.
_WHEP_UP = f"http://127.0.0.1:8889/{RTSP_PATH}/whep"

def _whep_proxy(offer: bytes) -> Response:
    headers = {"Content-Type": "application/sdp"}
    if STREAM_PASS:
        tok = base64.b64encode(f"{STREAM_USER}:{STREAM_PASS}".encode()).decode()
        headers["Authorization"] = "Basic " + tok
    req = urllib.request.Request(_WHEP_UP, data=offer, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return Response(content=r.read(), media_type=r.headers.get("Content-Type", "application/sdp"))
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code, media_type="application/sdp")

@app.post("/whep")
async def whep(request: Request):
    offer = await request.body()
    return await run_in_threadpool(_whep_proxy, offer)

# HLS (LL-HLS) reverse proxy. WebRTC media is peer-to-peer and can't cross the Cloudflare tunnel,
# so when the panel is opened remotely it falls back to HLS, which is plain HTTP and proxied here
# (same-origin, already behind web auth; mediamtx's stream credentials are injected server-side).
def _hls_open(path: str, query: str):
    url = f"http://127.0.0.1:8888/{path}" + (("?" + query) if query else "")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    req = urllib.request.Request(url)
    if STREAM_PASS:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{STREAM_USER}:{STREAM_PASS}".encode()).decode())
    return opener.open(req, timeout=20)   # response object (raises HTTPError on 4xx/5xx)

@app.get("/hls/{path:path}")
async def hls(path: str, request: Request):
    try:
        r = await run_in_threadpool(_hls_open, path, request.url.query)
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code,
                        media_type=e.headers.get("Content-Type", "application/json"))
    except Exception:
        return Response(content=b"", status_code=502)
    # Stream the bytes through (chunked) instead of buffering the whole response: lets LL-HLS
    # parts/segments flow promptly and avoids adding a full-response delay through the tunnel.
    def gen():
        try:
            while True:
                chunk = r.read(65536)
                if not chunk: break
                yield chunk
        finally:
            r.close()
    return StreamingResponse(gen(), media_type=r.headers.get("Content-Type", "application/octet-stream"))

@app.get("/info")
def info():
    ip = lan_ip()
    return JSONResponse({
        "configured": CONFIGURED,
        "connected": bridge.connected if bridge else False,
        "paused": bridge.paused if bridge else False,
        "awake": bridge.is_awake() if bridge else False,
        "codec": bridge.codec_name if bridge else None,
        "video": VIDEO_MODE,
        "audio_enabled": AUDIO_ON,
        "frames_received": bridge.frame_count if bridge else 0,
        "audio": ({"codec": f"0x{bridge.audio_codec:02x}", "flags": f"0x{bridge.audio_flags:02x}",
                   "count": bridge.audio_count} if bridge and bridge.audio_codec is not None else None),
        "battery": bridge.battery if bridge else -1,
        "charge": bridge.charge if bridge else -1,
        "rtsp": f"rtsp://{ip}:8554/{RTSP_PATH}",
    })

class Move(BaseModel):
    ly: float = 0.0
    rx: float = 0.0
    duration: float = 0.4

@app.post("/move")
def move(m: Move):
    do_move(m.ly, m.rx, m.duration); return {"ok": True}

# Analog joystick: the panel POSTs the current vector at ~15 Hz while dragging. Each call
# sends ONE motor frame (no trailing stop) so motion is continuous; a server-side watchdog
# stops the robot if updates stop arriving (release, navigation away, network drop).
_last_drive = 0.0

@app.post("/drive")
def drive(m: Move):
    global _last_drive
    bridge.send_rdt(motor_frame(ly=m.ly, rx=m.rx))
    _last_drive = time.time()
    return {"ok": True}

def _drive_watchdog():
    global _last_drive
    while True:
        time.sleep(0.1)
        if _last_drive and (time.time() - _last_drive) > 0.35:
            _last_drive = 0.0
            try: bridge.send_rdt(motor_frame())   # deadman stop
            except Exception: pass

# Same analog drive over a WebSocket: each {"ly","rx"} message is one motor frame. Lower overhead
# than an HTTP POST per update on mobile links; the watchdog above still stops the robot when
# messages stop, and a disconnect stops it immediately.
@app.websocket("/ws/drive")
async def drive_ws(websocket: WebSocket):
    global _last_drive
    if not _valid_basic_auth(websocket.headers.get("authorization")):
        await websocket.close(code=4401, reason="Unauthorized")
        return
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_json()
            try:
                ly = max(-1.0, min(1.0, float(msg.get("ly", 0.0))))
                rx = max(-1.0, min(1.0, float(msg.get("rx", 0.0))))
            except (TypeError, ValueError, AttributeError):
                await websocket.close(code=1008, reason="Invalid drive message")
                return
            if bridge:
                bridge.send_rdt(motor_frame(ly=ly, rx=rx))
            _last_drive = time.time() if (ly or rx) else 0.0
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        if bridge:
            try: bridge.send_rdt(motor_frame())
            except Exception: pass

@app.post("/stop")
def stop():
    global _last_drive
    _last_drive = 0.0
    for _ in range(3): bridge.send_rdt(motor_frame()); time.sleep(0.02)
    return {"ok": True}

@app.post("/action/{name}")
def action(name: str):
    return {"ok": True, "action": name} if do_action(name) else {"ok": False, "err": "unknown"}

@app.post("/connection/{state}")
def connection(state: str):
    # state = "stop" (release the robot for the Enabot app) or "start" (reconnect)
    if state == "stop":
        bridge.pause_bridge()
    elif state == "start":
        bridge.resume_bridge()
    else:
        return {"ok": False, "err": "unknown"}
    return {"ok": True, "paused": bridge.paused}


@app.websocket("/ws/talk")
async def talk(websocket: WebSocket):
    if not _valid_basic_auth(websocket.headers.get("authorization")):
        await websocket.close(code=4401, reason="Unauthorized")
        return
    if not _talk_owner.acquire(blocking=False):
        await websocket.close(code=4409, reason="Talkback already in use")
        return

    started = False
    try:
        await websocket.accept()
        if not bridge or bridge.paused:
            await websocket.send_json({"type": "error", "error": "bridge unavailable"})
            await websocket.close(code=1013)
            return
        try:
            await run_in_threadpool(bridge.speaker_start, 12.0)
            started = True
        except Exception as exc:
            print("[supervisor] talkback start failed:", type(exc).__name__, flush=True)
            await websocket.send_json({"type": "error", "error": "speaker start failed"})
            await websocket.close(code=1011)
            return

        await websocket.send_json({"type": "ready", "format": "pcm_s16le", "rate": 8000,
                                   "channels": 1, "max_bytes": 1280})
        # At 8 kHz mono PCM16 the natural rate is 16 kB/s. Allow a 200 ms burst,
        # then reject clients that continuously outrun real time.
        budget = 3200.0
        last = time.monotonic()
        while True:
            pcm = await websocket.receive_bytes()
            now = time.monotonic()
            budget = min(3200.0, budget + (now - last) * 16000.0)
            last = now
            if not pcm or len(pcm) > 1280 or len(pcm) % 2 or len(pcm) > budget:
                await websocket.close(code=1008, reason="Invalid PCM stream")
                return
            budget -= len(pcm)
            await run_in_threadpool(bridge.speaker_send_pcm, pcm)
    except WebSocketDisconnect:
        pass
    finally:
        if started and bridge:
            try: await run_in_threadpool(bridge.speaker_stop)
            except Exception: pass
        _talk_owner.release()


# ---------------- web app (built PWA in WEB_DIR; SPA fallback to index.html) ----------------
def _web_file(path: str) -> Path | None:
    root = WEB_DIR.resolve()
    target = (root / path).resolve()
    if target.is_relative_to(root) and target.is_file():
        return target
    return None

@app.get("/{path:path}")
def web(path: str):
    target = _web_file(path) or _web_file("index.html")
    if target is None:
        return RedirectResponse("/classic") if not path else Response(status_code=404)
    if target.name in ("index.html", "sw.js", "manifest.webmanifest"):
        headers = {"Cache-Control": "no-cache"}
    elif target.parent.name == "assets":   # content-hashed by Vite
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    else:
        headers = {"Cache-Control": "public, max-age=3600"}
    return FileResponse(target, headers=headers)


if __name__ == "__main__":
    bridge = Bridge()
    threading.Thread(target=_drive_watchdog, daemon=True).start()
    uvicorn.run(app, host=BIND_HOST, port=PORT, log_level="warning")
