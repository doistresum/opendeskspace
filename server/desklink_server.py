#!/usr/bin/env python3
"""Open Deskspace host.

Creates a real GNOME virtual monitor, streams it to browsers over WebSocket
as MJPEG, and injects pointer, touch, and keyboard input back into that monitor.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

VERSION = "1.0.0"
DISCOVERY_PORT = 53530
ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
CONFIG_PATH = Path.home() / ".config" / "desklink" / "config.json"
STATE_DIR = Path.home() / ".local" / "state" / "desklink"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
QUALITIES = {"low": 35, "medium": 60, "high": 80}
RESOLUTIONS = {
    "3840x2160",
    "2560x1440",
    "1920x1080",
    "1600x900",
    "1280x720",
    "1024x768",
    "854x480",
}

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

import dbus  # noqa: E402
from dbus.mainloop.glib import DBusGMainLoop  # noqa: E402

VERBOSE = False
_MEDIA_READY = False


def ensure_media() -> None:
    global _MEDIA_READY
    if _MEDIA_READY:
        return
    DBusGMainLoop(set_as_default=True)
    Gst.init(None)
    _MEDIA_READY = True


def log(message: str) -> None:
    print(f"[desklink] {message}", flush=True)


def vlog(message: str) -> None:
    if VERBOSE:
        log(message)


def default_config() -> dict:
    return {
        "server": {
            "port": 9090,
            "bind_address": "0.0.0.0",
            "max_clients": 5,
            "password": None,
            "ssl_enabled": False,
            "ssl_cert": None,
            "ssl_key": None,
        },
        "display": {
            "resolution": "1920x1080",
            "refresh_rate": 60,
            "virtual_displays": 1,
            "resolutions": ["1920x1080"],
        },
        "encoding": {
            "codec": "mjpeg",
            "quality": "high",
            "fps": 30,
            "max_bandwidth_kbps": 0,
            "latency_mode": "normal",
        },
        "input": {
            "mouse_enabled": True,
            "touch_enabled": True,
            "keyboard_enabled": True,
        },
        "audio": {"enabled": True, "sample_rate": 48000},
    }


def load_config() -> dict:
    cfg = default_config()
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError as exc:
            log(f"ignoring broken config {CONFIG_PATH}: {exc}")
            return cfg
        for section, values in stored.items():
            if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def parse_resolution(value: str) -> tuple[int, int]:
    if value not in RESOLUTIONS:
        raise ValueError(f"unsupported resolution {value}")
    width, height = value.split("x")
    return int(width), int(height)


def password_matches(given: str, expected: str | None) -> bool:
    if not expected:
        return True
    return hmac.compare_digest(
        hashlib.sha256((given or "").encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def host_addresses() -> list[str]:
    ips: list[str] = []
    try:
        text = subprocess.check_output(["hostname", "-I"], text=True, timeout=2)
        ips = [part for part in text.split() if ":" not in part]
    except (OSError, subprocess.SubprocessError):
        ips = []
    if not ips:
        ips = ["127.0.0.1"]

    def rank(ip: str) -> tuple[int, str]:
        if ip.startswith("192.168.") or ip.startswith("10."):
            return (0, ip)
        if ip.startswith("172."):
            return (1, ip)
        if ip.startswith("127."):
            return (3, ip)
        return (2, ip)

    return sorted(dict.fromkeys(ips), key=rank)


def parse_gdctl(text: str) -> list[dict]:
    physical: list[dict] = []
    logical: list[dict] = []
    section = ""
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith("Monitors:"):
            section = "phys"
            current = None
            continue
        if line.startswith("Logical monitors:"):
            section = "logical"
            current = None
            continue
        if section == "phys" and "Monitor " in line:
            label = line.split("Monitor ", 1)[1].strip()
            connector = label.split(" ", 1)[0]
            product = label[len(connector) :].strip()
            if product.startswith("(") and product.endswith(")"):
                product = product[1:-1]
            current = {
                "connector": connector,
                "product": product,
                "mode": "",
                "virtual": connector.startswith("Meta") or "Virtual" in product,
            }
            physical.append(current)
        elif section == "phys" and current is not None and "@" in line and "x" in line:
            current["mode"] = line.strip().split("└──")[-1].strip()
        elif section == "logical" and "Logical monitor" in line:
            current = {"position": "", "primary": False, "scale": "", "connector": ""}
            logical.append(current)
        elif section == "logical" and current is not None:
            if "Position:" in line:
                current["position"] = line.split("Position:", 1)[1].strip()
            elif "Primary:" in line:
                current["primary"] = "yes" in line.lower()
            elif "Scale:" in line:
                current["scale"] = line.split("Scale:", 1)[1].strip()
            elif "└──" in line:
                current["connector"] = line.split("└──", 1)[1].split(" ", 1)[0].strip()
    by_connector = {item["connector"]: item for item in physical}
    merged = []
    seen = set()
    for item in logical:
        base = dict(by_connector.get(item["connector"], {}))
        base.update(item)
        merged.append(base)
        seen.add(item["connector"])
    for item in physical:
        if item["connector"] not in seen:
            merged.append(item)
    return merged


def list_monitors() -> list[dict]:
    try:
        text = subprocess.check_output(["gdctl", "show"], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as exc:
        vlog(f"gdctl: {exc}")
        return []
    return parse_gdctl(text)


def encode_frame(opcode: int, payload: bytes) -> bytes:
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + struct.pack("!H", length)
    else:
        header += bytes([127]) + struct.pack("!Q", length)
    return header + payload


def recvn(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = sock.recv(size - len(chunks))
        if not piece:
            raise ConnectionError("connection closed")
        chunks += piece
    return bytes(chunks)


def read_frame(sock: socket.socket) -> tuple[int, bytes]:
    first, second = recvn(sock, 2)
    opcode = first & 0x0F
    masked = (second & 0x80) != 0
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recvn(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recvn(sock, 8))[0]
    if length > 8_000_000:
        raise ConnectionError("frame too large")
    mask = recvn(sock, 4) if masked else b""
    payload = recvn(sock, length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


NAMED_KEYSYMS = {
    "Enter": 0xFF0D,
    "Backspace": 0xFF08,
    "Tab": 0xFF09,
    "Escape": 0xFF1B,
    " ": 0x20,
    "ArrowLeft": 0xFF51,
    "ArrowUp": 0xFF52,
    "ArrowRight": 0xFF53,
    "ArrowDown": 0xFF54,
    "Delete": 0xFFFF,
    "Home": 0xFF50,
    "End": 0xFF57,
    "PageUp": 0xFF55,
    "PageDown": 0xFF56,
    "Insert": 0xFF63,
    "Control": 0xFFE3,
    "Shift": 0xFFE1,
    "Alt": 0xFFE9,
    "Meta": 0xFFE7,
    "CapsLock": 0xFFE5,
}


def keysym_for(key: str) -> int | None:
    if key in NAMED_KEYSYMS:
        return NAMED_KEYSYMS[key]
    if len(key) == 1 and ord(key) < 128:
        return ord(key)
    return None


class Client:
    def __init__(self, sock: socket.socket, address: tuple) -> None:
        self.sock = sock
        self.address = address
        self.lock = threading.Lock()
        self.alive = True
        self.display: VirtualDisplay | None = None
        self.last_frame_id = -1
        self.bytes_sent = 0
        self.audio = False
        self.transport = "ws"
        self.streaming = False

    def send_video(self, payload: bytes) -> None:
        if not self.alive or not self.streaming:
            return
        try:
            with self.lock:
                if self.transport == "tcp":
                    self.sock.sendall(struct.pack("!I", len(payload)) + payload)
                else:
                    self.sock.sendall(encode_frame(2, payload))
            self.bytes_sent += len(payload)
        except OSError:
            self.alive = False

    def send(self, opcode: int, payload: bytes) -> None:
        if not self.alive:
            return
        packet = encode_frame(opcode, payload)
        try:
            with self.lock:
                self.sock.sendall(packet)
            self.bytes_sent += len(payload)
        except OSError:
            self.alive = False

    def send_json(self, payload: dict) -> None:
        self.send(1, json.dumps(payload).encode())

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class VirtualDisplay:
    def __init__(self, hub: Hub, width: int, height: int, fps: int, quality: str) -> None:
        self.hub = hub
        self.width = width
        self.height = height
        self.fps = max(5, min(60, fps))
        self.quality = quality if quality in QUALITIES else "high"
        self.clients: list[Client] = []
        self.ready = threading.Event()
        self.error: str | None = None
        self.jpeg: bytes | None = None
        self.frame_id = 0
        self.frame_event = threading.Event()
        self.node_id: int | None = None
        self.stream_path: str | None = None
        self.rd_session = None
        self.pipeline = None
        self.alive = True
        self.got_frame = False
        self.last_emit = 0.0
        self.frames_sent = 0
        self.window_started = time.monotonic()
        self.window_bytes = 0
        self.broadcast_fps = 0
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)

    def start(self) -> None:
        self.ready.clear()
        self.error = None
        self.got_frame = False

        def begin() -> bool:
            try:
                self._open_session()
            except Exception as exc:
                self.error = str(exc)
                self.ready.set()
            return False

        GLib.idle_add(begin)
        if not self.ready.wait(8):
            self.error = self.error or "timed out waiting for the virtual monitor"
        if self.error:
            self.alive = False
            raise RuntimeError(self.error)
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
            self._thread.start()

    def _open_session(self) -> None:
        bus = dbus.SessionBus()
        remote = dbus.Interface(
            bus.get_object("org.gnome.Mutter.RemoteDesktop", "/org/gnome/Mutter/RemoteDesktop"),
            "org.gnome.Mutter.RemoteDesktop",
        )
        session_path = remote.CreateSession()
        props = dbus.Interface(
            bus.get_object("org.gnome.Mutter.RemoteDesktop", session_path),
            "org.freedesktop.DBus.Properties",
        )
        session_id = str(props.Get("org.gnome.Mutter.RemoteDesktop.Session", "SessionId"))
        screen_cast = dbus.Interface(
            bus.get_object("org.gnome.Mutter.ScreenCast", "/org/gnome/Mutter/ScreenCast"),
            "org.gnome.Mutter.ScreenCast",
        )
        cast_path = screen_cast.CreateSession({"remote-desktop-session-id": session_id})
        cast_session = dbus.Interface(
            bus.get_object("org.gnome.Mutter.ScreenCast", cast_path),
            "org.gnome.Mutter.ScreenCast.Session",
        )
        stream_path = cast_session.RecordVirtual(
            {"cursor-mode": dbus.UInt32(1), "is-platform": dbus.Boolean(True)}
        )
        self.stream_path = str(stream_path)
        stream = bus.get_object("org.gnome.Mutter.ScreenCast", stream_path)
        stream.connect_to_signal(
            "PipeWireStreamAdded",
            self._on_node,
            dbus_interface="org.gnome.Mutter.ScreenCast.Stream",
        )
        self.rd_session = dbus.Interface(
            bus.get_object("org.gnome.Mutter.RemoteDesktop", session_path),
            "org.gnome.Mutter.RemoteDesktop.Session",
        )
        self.rd_session.Start()
        log(f"virtual monitor session started, waiting for frames at {self.width}x{self.height}")

    def _on_node(self, node_id: int) -> None:
        if self.node_id is not None:
            return
        self.node_id = int(node_id)
        vlog(f"pipewire node {self.node_id}")
        try:
            self._start_pipeline()
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()

    def _start_pipeline(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        quality = QUALITIES.get(self.quality, 80)
        if self.hub.config["encoding"].get("latency_mode") == "ultra":
            quality = max(20, quality - 25)
        description = (
            f"pipewiresrc path={self.node_id} do-timestamp=true ! "
            f"videoconvert ! videoscale ! "
            f"video/x-raw,width={self.width},height={self.height} ! "
            "queue max-size-buffers=1 max-size-time=0 max-size-bytes=0 leaky=downstream ! "
            f"jpegenc quality={quality} ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        pipeline = Gst.parse_launch(description)
        sink = pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus)
        self.pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)

    def _on_bus(self, _bus, message) -> bool:
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.error = str(err)
            log(f"video pipeline error: {err} {debug}")
            self.ready.set()
        return True

    def _on_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        now = time.monotonic()
        interval = 1.0 / float(self.fps)
        if now - self.last_emit < interval:
            return Gst.FlowReturn.OK
        self.last_emit = now
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        data = bytes(info.data)
        buffer.unmap(info)
        self.jpeg = data
        self.frame_id += 1
        self.frame_event.set()
        if not self.got_frame:
            self.got_frame = True
            log(f"first frame {len(data)} bytes")
            self.ready.set()
        return Gst.FlowReturn.OK

    def _broadcast_loop(self) -> None:
        while self.alive and self.hub.running:
            self.frame_event.wait(1.0)
            self.frame_event.clear()
            frame = self.jpeg
            frame_id = self.frame_id
            if not frame:
                continue
            limit = int(self.hub.config["encoding"].get("max_bandwidth_kbps") or 0) * 1000 // 8
            now = time.monotonic()
            if now - self.window_started >= 1:
                self.broadcast_fps = self.frames_sent
                self.frames_sent = 0
                self.window_bytes = 0
                self.window_started = now
            if limit and self.window_bytes + len(frame) > limit and self.window_bytes:
                continue
            for client in list(self.clients):
                if not client.alive or not client.streaming or client.last_frame_id == frame_id:
                    continue
                client.send_video(frame)
                if client.alive:
                    client.last_frame_id = frame_id
                    self.window_bytes += len(frame)
            self.frames_sent += 1
            if int(now) != int(now - 0.001):
                pass
            self._maybe_send_stats()

    def _maybe_send_stats(self) -> None:
        now = time.monotonic()
        if now - getattr(self, "_stats_at", 0) < 1:
            return
        self._stats_at = now
        payload = {
            "type": "stats",
            "fps": self.broadcast_fps,
            "bytesPerSec": self.window_bytes,
            "clients": len([client for client in self.clients if client.alive]),
            "width": self.width,
            "height": self.height,
            "codec": "mjpeg",
        }
        for client in list(self.clients):
            if client.alive and client.transport == "ws" and client.streaming:
                client.send_json(payload)

    def local_point(self, x: float, y: float) -> tuple[float, float]:
        px = min(max(float(x), 0.0), 1.0) * max(self.width - 1, 1)
        py = min(max(float(y), 0.0), 1.0) * max(self.height - 1, 1)
        return px, py

    def pointer(self, x: float, y: float) -> None:
        if not self.hub.config["input"].get("mouse_enabled", True):
            return
        px, py = self.local_point(x, y)
        stream = self.stream_path
        session = self.rd_session

        def go() -> bool:
            try:
                if session is not None and stream:
                    session.NotifyPointerMotionAbsolute(stream, float(px), float(py))
            except Exception as exc:
                vlog(f"pointer: {exc}")
            return False

        GLib.idle_add(go)

    def button(self, button: int, down: bool, x: float, y: float) -> None:
        if not self.hub.config["input"].get("mouse_enabled", True):
            return
        self.pointer(x, y)
        mapped = {0: 1, 1: 2, 2: 3}.get(int(button), int(button) + 1)
        session = self.rd_session

        def go() -> bool:
            try:
                if session is not None:
                    session.NotifyPointerButton(int(mapped), bool(down))
            except Exception as exc:
                vlog(f"button: {exc}")
            return False

        GLib.idle_add(go)

    def wheel(self, dx: float, dy: float) -> None:
        session = self.rd_session

        def go() -> bool:
            try:
                if session is None:
                    return False
                if abs(dy) >= abs(dx) and dy:
                    session.NotifyPointerAxisDiscrete(0, 1 if dy > 0 else -1)
                elif dx:
                    session.NotifyPointerAxisDiscrete(1, 1 if dx > 0 else -1)
            except Exception as exc:
                vlog(f"wheel: {exc}")
            return False

        GLib.idle_add(go)

    def touch(self, phase: str, slot: int, x: float, y: float) -> None:
        if not self.hub.config["input"].get("touch_enabled", True):
            return
        px, py = self.local_point(x, y)
        stream = self.stream_path
        session = self.rd_session
        slot = max(0, min(int(slot), 31))

        def go() -> bool:
            try:
                if session is None or not stream:
                    return False
                if phase == "down":
                    session.NotifyTouchDown(stream, slot, float(px), float(py))
                elif phase == "move":
                    session.NotifyTouchMotion(stream, slot, float(px), float(py))
                elif phase == "up":
                    session.NotifyTouchUp(slot)
            except Exception as exc:
                vlog(f"touch: {exc}")
            return False

        GLib.idle_add(go)

    def key(self, key: str, down: bool) -> None:
        if not self.hub.config["input"].get("keyboard_enabled", True):
            return
        keysym = keysym_for(key)
        if keysym is None:
            return
        session = self.rd_session

        def go() -> bool:
            try:
                if session is not None:
                    session.NotifyKeyboardKeysym(int(keysym), bool(down))
            except Exception as exc:
                vlog(f"key: {exc}")
            return False

        GLib.idle_add(go)

    def reconfigure(self, width: int, height: int, fps: int, quality: str, size_changed: bool) -> None:
        done = threading.Event()

        def go() -> bool:
            try:
                self.width = width
                self.height = height
                self.fps = max(5, min(60, fps))
                self.quality = quality if quality in QUALITIES else self.quality
                self.jpeg = None
                if size_changed:
                    self._teardown_session()
                    self.got_frame = False
                    self.error = None
                    self.ready.clear()
                    self._open_session()
                elif self.node_id is not None:
                    self._start_pipeline()
                    done.set()
            except Exception as exc:
                self.error = str(exc)
                log(f"reconfigure failed: {exc}")
                self.ready.set()
                done.set()
            return False

        GLib.idle_add(go)
        if size_changed:
            self.ready.wait(8)
        else:
            done.wait(4)

    def teardown_now(self) -> None:
        self.alive = False
        self.frame_event.set()
        self._teardown_session()

    def _teardown_session(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.rd_session is not None:
            try:
                self.rd_session.Stop()
            except Exception as exc:
                vlog(f"session stop: {exc}")
            self.rd_session = None
        self.node_id = None
        self.stream_path = None


class AudioCapture:
    def __init__(self) -> None:
        self.pipeline = None
        self.clients: list[Client] = []
        self.error: str | None = None
        self.active = False

    def start(self) -> bool:
        done = threading.Event()
        result = {"ok": False}

        def go() -> bool:
            result["ok"] = self._start_now()
            done.set()
            return False

        GLib.idle_add(go)
        done.wait(4)
        return result["ok"]

    def _start_now(self) -> bool:
        device = monitor_source()
        if not device:
            self.error = "No PulseAudio/PipeWire default sink monitor was found"
            log(self.error)
            return False
        try:
            pipeline = Gst.Pipeline.new("desklink-audio")
            source = Gst.ElementFactory.make("pulsesrc", "src")
            convert = Gst.ElementFactory.make("audioconvert", "convert")
            resample = Gst.ElementFactory.make("audioresample", "resample")
            capsfilter = Gst.ElementFactory.make("capsfilter", "caps")
            sink = Gst.ElementFactory.make("appsink", "asink")
            if not all((source, convert, resample, capsfilter, sink)):
                self.error = "GStreamer audio elements are missing"
                return False
            source.set_property("device", device)
            capsfilter.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=48000,channels=1"),
            )
            sink.set_property("emit-signals", True)
            sink.set_property("max-buffers", 4)
            sink.set_property("drop", True)
            sink.set_property("sync", False)
            for element in (source, convert, resample, capsfilter, sink):
                pipeline.add(element)
            source.link(convert)
            convert.link(resample)
            resample.link(capsfilter)
            capsfilter.link(sink)
            sink.connect("new-sample", self._on_sample)
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_bus)
            pipeline.set_state(Gst.State.PLAYING)
            self.pipeline = pipeline
            self.active = True
            self.error = None
            log(f"desktop audio capture from {device}")
            return True
        except Exception as exc:
            self.error = str(exc)
            log(f"audio disabled: {exc}")
            return False

    def _on_bus(self, _bus, message) -> bool:
        if message.type == Gst.MessageType.ERROR:
            err, _debug = message.parse_error()
            self.error = str(err)
            self.active = False
            log(f"audio pipeline error: {err}")
        return True

    def _on_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        data = bytes(info.data)
        buffer.unmap(info)
        for client in list(self.clients):
            if client.alive:
                client.send(2, data)
        return Gst.FlowReturn.OK

    def stop_now(self) -> None:
        self.active = False
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None


def monitor_source() -> str | None:
    sink = ""
    try:
        sink = subprocess.check_output(["pactl", "get-default-sink"], text=True, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        sink = ""
    if not sink:
        try:
            text = subprocess.check_output(
                ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], text=True, timeout=2
            )
        except (OSError, subprocess.SubprocessError):
            text = ""
        for line in text.splitlines():
            stripped = line.strip().lstrip("* ").strip()
            if stripped.startswith("node.name"):
                sink = stripped.split("=", 1)[1].strip().strip('"')
                break
    if not sink:
        return None
    if sink.endswith(".monitor"):
        return sink
    return f"{sink}.monitor"


class Hub:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.running = True
        self.displays: list[VirtualDisplay] = []
        self.clients: list[Client] = []
        self.lock = threading.Lock()
        self.audio = AudioCapture()
        self.listen_port = int(config["server"]["port"])
        self.started_at = time.time()

    def encoding_fps(self) -> int:
        fps = int(self.config["encoding"].get("fps") or 30)
        if self.config["encoding"].get("latency_mode") == "ultra":
            fps = max(fps, 30)
        return max(5, min(60, fps))

    def resolution_for_index(self, index: int) -> tuple[int, int]:
        items = self.config["display"].get("resolutions") or []
        chosen = items[index] if index < len(items) else self.config["display"]["resolution"]
        return parse_resolution(str(chosen))

    def attach(self, client: Client) -> VirtualDisplay:
        with self.lock:
            if len([item for item in self.clients if item.alive]) >= int(self.config["server"]["max_clients"]):
                raise RuntimeError("Open Deskspace is already serving the maximum number of clients")
            slots = max(1, int(self.config["display"].get("virtual_displays") or 1))
            reusable = [display for display in self.displays if display.alive and display.error is None]
            if slots == 1 and reusable:
                display = reusable[0]
            elif len(reusable) < slots:
                width, height = self.resolution_for_index(len(reusable))
                display = VirtualDisplay(
                    self,
                    width,
                    height,
                    self.encoding_fps(),
                    str(self.config["encoding"].get("quality") or "high"),
                )
                self.displays.append(display)
                start_new = True
            else:
                display = reusable[-1]
                start_new = False
            if display not in self.displays:
                self.displays.append(display)
            display.clients.append(client)
            client.display = display
            self.clients.append(client)
            start_new = len(display.clients) == 1 and not display.got_frame
        if start_new:
            try:
                display.start()
            except Exception:
                with self.lock:
                    self._drop_display(display)
                    if client in self.clients:
                        self.clients.remove(client)
                raise
            if self.config["audio"].get("enabled", True) and not self.audio.active:
                self.audio.start()
        elif not display.got_frame:
            display.ready.wait(8)
            if display.error:
                raise RuntimeError(display.error)
        return display

    def detach(self, client: Client) -> None:
        display = client.display
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
            if client in self.audio.clients:
                self.audio.clients.remove(client)
            if display and client in display.clients:
                display.clients.remove(client)
            empty = display is not None and not any(item.alive for item in display.clients)
        client.display = None
        if empty and display is not None:
            done = threading.Event()

            def go() -> bool:
                display.teardown_now()
                done.set()
                return False

            GLib.idle_add(go)
            done.wait(3)
            with self.lock:
                self._drop_display(display)
            self._stop_audio_if_idle()

    def _drop_display(self, display: VirtualDisplay) -> None:
        if display in self.displays:
            self.displays.remove(display)

    def _stop_audio_if_idle(self) -> None:
        with self.lock:
            busy = any(display.alive and display.clients for display in self.displays)
        if not busy and self.audio.active:
            done = threading.Event()

            def go() -> bool:
                self.audio.stop_now()
                done.set()
                return False

            GLib.idle_add(go)
            done.wait(2)

    def reconfigure_live(self, size_changed: bool) -> None:
        with self.lock:
            displays = list(self.displays)
        for index, display in enumerate(displays):
            width, height = self.resolution_for_index(index if size_changed else 0)
            if not size_changed:
                width, height = display.width, display.height
            display.reconfigure(
                width,
                height,
                self.encoding_fps(),
                str(self.config["encoding"].get("quality") or "high"),
                size_changed,
            )

    def shutdown(self) -> None:
        self.running = False
        for display in list(self.displays):
            display.teardown_now()
        self.audio.stop_now()
        for client in list(self.clients):
            client.close()

    def status(self) -> dict:
        displays = [display for display in self.displays if display.alive]
        virtual = None
        if displays:
            current = displays[0]
            virtual = {
                "active": True,
                "width": current.width,
                "height": current.height,
                "fps": current.broadcast_fps or current.fps,
                "clients": len([client for client in current.clients if client.alive]),
                "connector": "Meta-0",
                "product": "Virtual remote monitor",
            }
        addresses = host_addresses()
        scheme = "https" if self.config["server"].get("ssl_enabled") else "http"
        preferred = addresses[0]
        return {
            "status": "running",
            "version": VERSION,
            "hostname": socket.gethostname(),
            "addresses": addresses,
            "viewerUrl": f"{scheme}://{preferred}:{self.listen_port}/?view=viewer",
            "port": self.listen_port,
            "tabletPort": self.listen_port + 1,
            "configuredPort": int(self.config["server"]["port"]),
            "tls": bool(self.config["server"].get("ssl_enabled")),
            "passwordRequired": bool(self.config["server"].get("password")),
            "clients": len([client for client in self.clients if client.alive]),
            "display": virtual["connector"] if virtual else None,
            "virtualDisplay": virtual,
            "displays": list_monitors(),
            "audio": self.audio.active,
            "audioError": self.audio.error,
            "encoder": "mjpeg",
            "platform": "gnome-wayland",
            "config": {
                "resolution": self.config["display"]["resolution"],
                "fps": int(self.config["encoding"]["fps"]),
                "quality": self.config["encoding"]["quality"],
                "touchEnabled": bool(self.config["input"].get("touch_enabled", True)),
                "keyboardEnabled": bool(self.config["input"].get("keyboard_enabled", True)),
                "audioEnabled": bool(self.config["audio"].get("enabled", True)),
                "virtualDisplays": int(self.config["display"].get("virtual_displays") or 1),
                "maxBandwidthKbps": int(self.config["encoding"].get("max_bandwidth_kbps") or 0),
                "latencyMode": self.config["encoding"].get("latency_mode") or "normal",
            },
        }


HUB: Hub | None = None
HTTPD: ThreadingHTTPServer | None = None


class DeskLinkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    rbufsize = 0
    disable_nagle_algorithm = True

    def log_message(self, fmt: str, *args) -> None:
        if VERBOSE:
            log("%s - %s" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/stream", "/audio") and (
            self.headers.get("Upgrade", "").lower() == "websocket"
        ):
            self._websocket(parsed.path)
            return
        if parsed.path in ("/api/status", "/api/info"):
            self._json(200, HUB.status() if HUB else {"status": "stopped"})
            return
        if parsed.path == "/api/displays":
            self._json(200, {"displays": list_monitors()})
            return
        self._static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/config":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return
        if HUB is None:
            self._json(503, {"error": "server is not running"})
            return
        expected = HUB.config["server"].get("password")
        if expected and not password_matches(str(body.get("password") or ""), expected):
            self._json(401, {"error": "password required"})
            return
        try:
            restart = apply_config_update(HUB, body)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        save_config(HUB.config)
        payload = HUB.status()
        payload["restartRequired"] = restart
        self._json(200, payload)

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _static(self, path: str) -> None:
        relative = unquote(path).lstrip("/")
        if relative == "":
            relative = "index.html"
        root = DIST.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
            inside = True
        except ValueError:
            inside = False
        if not inside or not candidate.is_file():
            fallback = root / "index.html"
            if "api" not in path and fallback.is_file() and "." not in Path(relative).name:
                candidate = fallback
            elif not fallback.is_file() and relative == "index.html":
                body = (
                    "<!doctype html><meta charset=utf-8><title>Open Deskspace</title>"
                    "<body style='font-family:sans-serif;background:#111;color:#eee;padding:2rem'>"
                    "<h1>Open Deskspace is running</h1>"
                    "<p>The web UI has not been built yet. From the project directory run "
                    "<code>npm run build</code> and reload.</p>"
                    f"<p>API status: <a href='/api/status'>/api/status</a></p></body>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self._json(404, {"error": "not found"})
                return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json",
            ".png": "image/png",
            ".webp": "image/webp",
            ".ico": "image/x-icon",
        }.get(candidate.suffix, "application/octet-stream")
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if candidate.name == "index.html":
            self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _websocket(self, path: str) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or HUB is None:
            self._json(400, {"error": "websocket upgrade failed"})
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self.wfile.write(response.encode())
        self.wfile.flush()
        client = Client(self.connection, self.client_address)
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            if path == "/audio":
                serve_audio(client)
            else:
                serve_stream(client)
        except Exception as exc:
            vlog(f"socket closed: {exc}")
        finally:
            if path == "/audio":
                with HUB.lock:
                    if client in HUB.audio.clients:
                        HUB.audio.clients.remove(client)
            else:
                HUB.detach(client)
            client.close()
        self.close_connection = True


def serve_audio(client: Client) -> None:
    assert HUB is not None
    client.audio = True
    with HUB.lock:
        HUB.audio.clients.append(client)
    while HUB.running and client.alive:
        opcode, _payload = read_frame(client.sock)
        if opcode == 0x8:
            break
        if opcode == 0x9:
            client.send(0xA, _payload)


def serve_stream(client: Client) -> None:
    assert HUB is not None
    welcomed = False
    while HUB.running and client.alive:
        opcode, payload = read_frame(client.sock)
        if opcode == 0x8:
            break
        if opcode == 0x9:
            client.send(0xA, payload)
            continue
        if opcode != 0x1:
            continue
        try:
            message = json.loads(payload.decode())
        except json.JSONDecodeError:
            continue
        kind = message.get("type")
        if kind == "hello" and not welcomed:
            try:
                if not password_matches(
                    str(message.get("password") or ""),
                    HUB.config["server"].get("password"),
                ):
                    client.send_json({"type": "error", "message": "Wrong password"})
                    break
                apply_hello_settings(HUB, message)
                display = HUB.attach(client)
            except Exception as exc:
                client.send_json({"type": "error", "message": str(exc)})
                break
            client.send_json(
                {
                    "type": "hello",
                    "width": display.width,
                    "height": display.height,
                    "fps": display.fps,
                    "codec": "mjpeg",
                    "audio": bool(HUB.audio.active),
                    "audioRate": 48000,
                    "touchEnabled": bool(HUB.config["input"].get("touch_enabled", True)),
                    "tabletPort": HUB.listen_port + 1,
                }
            )
            client.streaming = True
            welcomed = True
            if display.jpeg:
                display.frame_event.set()
            continue
        if not welcomed:
            continue
        if kind == "ping":
            client.send_json({"type": "pong", "t": message.get("t")})
            continue
        handle_control(client, message)


def handle_control(client: Client, message: dict) -> None:
    display = client.display
    if display is None:
        return
    kind = message.get("type")
    if kind == "pointer":
        display.pointer(float(message.get("x") or 0), float(message.get("y") or 0))
    elif kind == "button":
        display.button(
            int(message.get("button") or 0),
            bool(message.get("down")),
            float(message.get("x") or 0),
            float(message.get("y") or 0),
        )
    elif kind == "wheel":
        display.wheel(float(message.get("dx") or 0), float(message.get("dy") or 0))
    elif kind == "touch":
        display.touch(
            str(message.get("phase") or ""),
            int(message.get("slot") or 0),
            float(message.get("x") or 0),
            float(message.get("y") or 0),
        )
    elif kind == "key":
        display.key(str(message.get("key") or ""), bool(message.get("down")))


def read_text_line(conn: socket.socket, limit: int = 2048) -> str:
    chunks = bytearray()
    while b"\n" not in chunks:
        piece = conn.recv(1)
        if not piece:
            raise ConnectionError("connection closed")
        chunks += piece
        if len(chunks) > limit:
            raise ConnectionError("line too long")
    return chunks.decode("utf-8", "replace").strip()


def serve_tcp_client(conn: socket.socket, address: tuple) -> None:
    if HUB is None:
        conn.close()
        return
    client = Client(conn, address)
    client.transport = "tcp"
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(20)
        line = read_text_line(conn)
        try:
            password, resolution = parse_tcp_hello(line)
        except ValueError:
            conn.sendall(b"ERR expected HELLO\n")
            return
        if not password_matches(password, HUB.config["server"].get("password")):
            conn.sendall(b"ERR password\n")
            return
        if resolution:
            try:
                apply_hello_settings(HUB, {"resolution": resolution})
            except ValueError as exc:
                conn.sendall(f"ERR {exc}\n".encode())
                return
        display = HUB.attach(client)
        conn.sendall(f"OK {display.width} {display.height}\n".encode())
        client.streaming = True
        if display.jpeg:
            display.frame_event.set()
        conn.settimeout(None)
        pending = b""
        while HUB.running and client.alive:
            data = conn.recv(4096)
            if not data:
                break
            pending += data
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                handle_tcp_command(client, raw.decode("utf-8", "replace").strip())
    except Exception as exc:
        vlog(f"tablet {address}: {exc}")
    finally:
        HUB.detach(client)
        client.close()


def handle_tcp_command(client: Client, line: str) -> None:
    if not line or line == "BYE":
        client.alive = False
        return
    parts = line.split(" ")
    kind = parts[0]
    try:
        if kind == "POINTER" and len(parts) >= 3:
            handle_control(client, {"type": "pointer", "x": float(parts[1]), "y": float(parts[2])})
        elif kind == "BUTTON" and len(parts) >= 5:
            handle_control(
                client,
                {
                    "type": "button",
                    "button": int(float(parts[1])),
                    "down": parts[2] == "1",
                    "x": float(parts[3]),
                    "y": float(parts[4]),
                },
            )
        elif kind == "TOUCH" and len(parts) >= 5:
            handle_control(
                client,
                {
                    "type": "touch",
                    "phase": parts[1],
                    "slot": int(parts[2]),
                    "x": float(parts[3]),
                    "y": float(parts[4]),
                },
            )
        elif kind == "KEY" and len(parts) >= 3:
            handle_control(client, {"type": "key", "key": parts[1], "down": parts[2] == "1"})
        elif kind == "WHEEL" and len(parts) >= 3:
            handle_control(client, {"type": "wheel", "dx": float(parts[1]), "dy": float(parts[2])})
    except ValueError:
        return


def broadcast_addresses() -> list[str]:
    found = {"255.255.255.255"}
    try:
        text = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return sorted(found)
    for line in text.splitlines():
        parts = line.split()
        if "brd" in parts:
            index = parts.index("brd") + 1
            if index < len(parts) and parts[index] not in ("", "0.0.0.0"):
                found.add(parts[index])
    return sorted(found)


def tcp_loop(hub: Hub, host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(8)
    sock.settimeout(1.0)
    log(f"Android tablet protocol on tcp://{host}:{port}")
    while hub.running:
        try:
            conn, address = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=serve_tcp_client, args=(conn, address), daemon=True).start()
    sock.close()


def parse_tcp_hello(line: str) -> tuple[str, str | None]:
    parts = line.split(" ")
    if not parts or parts[0] != "HELLO":
        raise ValueError("expected HELLO")
    password_parts: list[str] = []
    resolution = None
    for token in parts[1:]:
        if token.startswith("resolution=") and resolution is None:
            resolution = token.split("=", 1)[1]
        else:
            password_parts.append(token)
    return " ".join(password_parts), resolution


def apply_hello_settings(hub: Hub, message: dict) -> None:
    if any(display.alive and display.got_frame for display in hub.displays):
        return
    changed = False
    if message.get("resolution"):
        width, height = parse_resolution(str(message["resolution"]))
        hub.config["display"]["resolution"] = f"{width}x{height}"
        hub.config["display"]["resolutions"] = [f"{width}x{height}"]
        changed = True
    if message.get("fps"):
        hub.config["encoding"]["fps"] = max(5, min(60, int(message["fps"])))
        changed = True
    if message.get("quality") in QUALITIES:
        hub.config["encoding"]["quality"] = message["quality"]
        changed = True
    if changed:
        save_config(hub.config)


def apply_config_update(hub: Hub, body: dict) -> bool:
    restart_port = False
    size_changed = False
    if body.get("resolution"):
        width, height = parse_resolution(str(body["resolution"]))
        value = f"{width}x{height}"
        size_changed = value != hub.config["display"]["resolution"]
        hub.config["display"]["resolution"] = value
        resolutions = list(hub.config["display"].get("resolutions") or [value])
        resolutions[0] = value
        hub.config["display"]["resolutions"] = resolutions
    if body.get("fps") is not None:
        hub.config["encoding"]["fps"] = max(5, min(60, int(body["fps"])))
    if body.get("quality") in QUALITIES:
        hub.config["encoding"]["quality"] = body["quality"]
    if "touchEnabled" in body:
        hub.config["input"]["touch_enabled"] = bool(body["touchEnabled"])
    if "keyboardEnabled" in body:
        hub.config["input"]["keyboard_enabled"] = bool(body["keyboardEnabled"])
    if "audioEnabled" in body:
        hub.config["audio"]["enabled"] = bool(body["audioEnabled"])
        if not body["audioEnabled"]:
            done = threading.Event()

            def stop_audio() -> bool:
                hub.audio.stop_now()
                done.set()
                return False

            GLib.idle_add(stop_audio)
            done.wait(2)
        elif hub.clients and not hub.audio.active:
            hub.audio.start()
    if body.get("virtualDisplays") is not None:
        hub.config["display"]["virtual_displays"] = max(1, min(5, int(body["virtualDisplays"])))
    if body.get("maxBandwidthKbps") is not None:
        hub.config["encoding"]["max_bandwidth_kbps"] = max(0, int(body["maxBandwidthKbps"]))
    if body.get("latencyMode") in ("normal", "ultra"):
        hub.config["encoding"]["latency_mode"] = body["latencyMode"]
    if "password" in body:
        value = body.get("password")
        hub.config["server"]["password"] = value or None
    if body.get("port") is not None:
        new_port = int(body["port"])
        if new_port != hub.listen_port:
            hub.config["server"]["port"] = new_port
            restart_port = True
    if hub.displays and any(k in body for k in ("resolution", "fps", "quality", "latencyMode")):
        hub.reconfigure_live(size_changed)
    return restart_port


def discovery_loop(hub: Hub) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while hub.running:
        payload = json.dumps(
            {
                "name": "Open Deskspace",
                "hostname": socket.gethostname(),
                "port": hub.listen_port,
                "tabletPort": hub.listen_port + 1,
                "version": VERSION,
                "addresses": host_addresses(),
            }
        ).encode()
        try:
            for target in broadcast_addresses():
                sock.sendto(b"DESKLINK " + payload, (target, DISCOVERY_PORT))
        except OSError as exc:
            vlog(f"discovery: {exc}")
        time.sleep(1)
    sock.close()


def install_service() -> None:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    python = Path(sys.executable).resolve()
    unit = unit_dir / "desklink.service"
    unit.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Open Deskspace virtual display server",
                "After=graphical-session.target",
                "",
                "[Service]",
                "Type=simple",
                f"ExecStart={python} {script} --start",
                "Restart=on-failure",
                "RestartSec=2",
                "Environment=PYTHONUNBUFFERED=1",
                "",
                "[Install]",
                "WantedBy=graphical-session.target",
                "",
            ]
        )
    )
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    launcher = bindir / "desklink-server"
    launcher.write_text(f"#!/bin/sh\nexec {python} {script} \"$@\"\n")
    launcher.chmod(0o755)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "desklink.service"], check=False)
    log(f"wrote {unit}")
    log("start it with: systemctl --user start desklink.service")
    log(f"command installed at {launcher}")


def generate_certificate(cfg: dict) -> None:
    directory = CONFIG_PATH.parent
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "3650",
            "-nodes",
            "-subj",
            "/CN=Open Deskspace",
        ],
        check=True,
    )
    cfg["server"]["ssl_cert"] = str(cert)
    cfg["server"]["ssl_key"] = str(key)
    cfg["server"]["ssl_enabled"] = True
    save_config(cfg)
    log(f"wrote {cert} and {key}")


def print_status(port: int, tls: bool) -> int:
    import urllib.request

    url = f"{'https' if tls else 'http'}://127.0.0.1:{port}/api/status"
    try:
        context = ssl._create_unverified_context() if tls else None
        with urllib.request.urlopen(url, timeout=2, context=context) as response:
            print(response.read().decode())
        return 0
    except Exception as exc:
        print(f"Open Deskspace is not running on port {port}: {exc}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desklink-server", description="Open Deskspace virtual display host")
    parser.add_argument("--start", action="store_true", help="Run the server")
    parser.add_argument("--port", type=int)
    parser.add_argument("--bind", default=None)
    parser.add_argument("--resolution")
    parser.add_argument("--fps", type=int)
    parser.add_argument("--quality", choices=sorted(QUALITIES))
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--init-config", action="store_true")
    parser.add_argument("--set-password")
    parser.add_argument("--set-resolution")
    parser.add_argument("--enable-ssl", action="store_true")
    parser.add_argument("--generate-cert", action="store_true")
    parser.add_argument("--install-service", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--list-displays", action="store_true")
    parser.add_argument("--list-encoders", action="store_true")
    parser.add_argument("--encoder", choices=["mjpeg"])
    parser.add_argument("--latency-mode", choices=["normal", "ultra"])
    parser.add_argument("--max-bandwidth", type=int, help="Limit stream bandwidth in kilobits per second")
    parser.add_argument("--virtual-displays", type=int)
    parser.add_argument("--protocol", choices=["websocket"])
    parser.add_argument("--password")
    parser.add_argument("--display-1")
    parser.add_argument("--display-2")
    parser.add_argument("--display-3")
    return parser


def daemonize() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    log_path = STATE_DIR / "desklink.log"
    with open(log_path, "a", buffering=1) as handle:
        os.dup2(handle.fileno(), 1)
        os.dup2(handle.fileno(), 2)
    (STATE_DIR / "desklink.pid").write_text(str(os.getpid()))


def serve(cfg: dict) -> int:
    global HUB, HTTPD, VERBOSE
    host = cfg["server"].get("bind_address") or "0.0.0.0"
    port = int(cfg["server"]["port"])
    HUB = Hub(cfg)
    HUB.listen_port = port
    server = ThreadingHTTPServer((host, port), DeskLinkHandler)
    server.daemon_threads = True
    if cfg["server"].get("ssl_enabled"):
        cert = cfg["server"].get("ssl_cert")
        key = cfg["server"].get("ssl_key")
        if not cert or not key or not Path(cert).exists() or not Path(key).exists():
            log("TLS is enabled but the certificate files are missing. Run --enable-ssl --generate-cert")
            return 1
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    HTTPD = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=discovery_loop, args=(HUB,), daemon=True).start()
    threading.Thread(
        target=tcp_loop, args=(HUB, host, port + 1), daemon=True
    ).start()
    addresses = host_addresses()
    scheme = "https" if cfg["server"].get("ssl_enabled") else "http"
    log(f"Open Deskspace {VERSION} listening on {scheme}://{host}:{port}")
    log(f"tablet URL: {scheme}://{addresses[0]}:{port}/?view=viewer")
    log(f"Android app connects to {addresses[0]}:{port + 1}")
    loop = GLib.MainLoop()

    def stop(*_args) -> bool:
        log("stopping")
        if HUB:
            HUB.shutdown()
        server.shutdown()
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, stop)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, stop)
    try:
        loop.run()
    finally:
        if HUB:
            HUB.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    global VERBOSE
    parser = build_parser()
    args = parser.parse_args(argv)
    VERBOSE = bool(args.verbose)
    cfg = load_config()
    should_start = args.start or not any(
        [
            args.init_config,
            args.set_password is not None,
            args.set_resolution,
            args.enable_ssl,
            args.generate_cert,
            args.install_service,
            args.status,
            args.list_displays,
            args.list_encoders,
        ]
    )
    if args.init_config or not CONFIG_PATH.exists():
        save_config(cfg)
        if args.init_config:
            log(f"wrote {CONFIG_PATH}")
            if not should_start:
                return 0
    if args.set_password is not None:
        cfg["server"]["password"] = args.set_password or None
        save_config(cfg)
        log("password updated")
        if not args.start:
            return 0
    if args.set_resolution or args.resolution:
        value = args.set_resolution or args.resolution
        parse_resolution(value)
        cfg["display"]["resolution"] = value
        cfg["display"]["resolutions"] = [value]
        save_config(cfg)
        if args.set_resolution and not args.start:
            log(f"resolution set to {value}")
            return 0
    if args.generate_cert or args.enable_ssl:
        if args.generate_cert or not cfg["server"].get("ssl_cert"):
            generate_certificate(cfg)
        else:
            cfg["server"]["ssl_enabled"] = True
            save_config(cfg)
        if not args.start:
            return 0
    if args.install_service:
        install_service()
        if not args.start:
            return 0
    if args.list_displays:
        for monitor in list_monitors():
            virtual = " virtual" if monitor.get("virtual") else ""
            print(
                f"{monitor.get('connector', '?')} {monitor.get('mode', '')} "
                f"{monitor.get('position', '')}{virtual}".strip()
            )
        return 0
    if args.list_encoders:
        print("mjpeg    available    browser stream")
        return 0
    if args.status and not args.start:
        return print_status(int(cfg["server"]["port"]), bool(cfg["server"].get("ssl_enabled")))
    if args.port:
        cfg["server"]["port"] = args.port
    if args.bind:
        cfg["server"]["bind_address"] = args.bind
    if args.fps:
        cfg["encoding"]["fps"] = max(5, min(60, args.fps))
    if args.quality:
        cfg["encoding"]["quality"] = args.quality
    if args.latency_mode:
        cfg["encoding"]["latency_mode"] = args.latency_mode
    if args.max_bandwidth is not None:
        cfg["encoding"]["max_bandwidth_kbps"] = max(0, args.max_bandwidth)
    if args.virtual_displays:
        cfg["display"]["virtual_displays"] = max(1, min(5, args.virtual_displays))
    if args.password is not None:
        cfg["server"]["password"] = args.password or None
    resolutions = list(cfg["display"].get("resolutions") or [cfg["display"]["resolution"]])
    for index, value in enumerate((args.display_1, args.display_2, args.display_3), start=1):
        if not value:
            continue
        parse_resolution(value)
        while len(resolutions) < index:
            resolutions.append(cfg["display"]["resolution"])
        resolutions[index - 1] = value
        if index == 1:
            cfg["display"]["resolution"] = value
    cfg["display"]["resolutions"] = resolutions
    if args.virtual_displays is None:
        cfg["display"]["virtual_displays"] = max(
            int(cfg["display"].get("virtual_displays") or 1),
            len([value for value in (args.display_1, args.display_2, args.display_3) if value]),
        )
    if args.daemon:
        daemonize()
    ensure_media()
    try:
        return serve(cfg)
    except OSError as exc:
        log(f"failed to listen: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
