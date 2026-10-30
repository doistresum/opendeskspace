export interface MonitorInfo {
  connector?: string;
  product?: string;
  mode?: string;
  position?: string;
  primary?: boolean;
  virtual?: boolean;
}

export interface ServerStatus {
  status: string;
  hostname: string;
  addresses: string[];
  port: number;
  tabletPort: number;
  viewerUrl: string;
  tls: boolean;
  passwordRequired: boolean;
  clients: number;
  display: string | null;
  virtualDisplay: {
    active: boolean;
    width: number;
    height: number;
    fps: number;
    clients: number;
    product?: string;
  } | null;
  displays: MonitorInfo[];
  audio: boolean;
  audioError?: string | null;
  encoder: string;
  platform: string;
  config: {
    resolution: string;
    fps: number;
    quality: string;
    touchEnabled: boolean;
    keyboardEnabled: boolean;
    audioEnabled: boolean;
    virtualDisplays: number;
    maxBandwidthKbps: number;
    latencyMode: string;
  };
}

export interface LiveState {
  connected: boolean;
  connecting: boolean;
  error: string;
  host: string;
  port: number;
  width: number;
  height: number;
  fps: number;
  rttMs: number;
  bandwidthMbps: number;
  codec: string;
}

export interface FoundServer {
  ip: string;
  port: number;
  hostname: string;
  tabletPort: number;
}

const initialLive = (): LiveState => ({
  connected: false,
  connecting: false,
  error: "",
  host: "",
  port: 9090,
  width: 0,
  height: 0,
  fps: 0,
  rttMs: 0,
  bandwidthMbps: 0,
  codec: "mjpeg",
});

export class DeskLinkClient {
  state: LiveState = initialLive();
  frameHandler: ((frame: ArrayBuffer) => void) | null = null;
  lastFrame: ArrayBuffer | null = null;
  autoReconnect = true;
  touchEnabled = true;
  audioEnabled = true;
  private socket: WebSocket | null = null;
  private audioSocket: WebSocket | null = null;
  private audioContext: AudioContext | null = null;
  private audioTime = 0;
  private listener: (state: LiveState) => void = () => undefined;
  private userClosed = false;
  private reconnectTimer = 0;
  private pingTimer = 0;
  private frameCount = 0;
  private frameWindow = 0;

  subscribe(listener: (state: LiveState) => void): () => void {
    this.listener = listener;
    listener(this.state);
    return () => {
      if (this.listener === listener) this.listener = () => undefined;
    };
  }

  private update(patch: Partial<LiveState>): void {
    this.state = { ...this.state, ...patch };
    this.listener(this.state);
  }

  connect(host: string, port: number, password = ""): void {
    this.userClosed = false;
    this.clearReconnect();
    this.open(host, port, password);
  }

  disconnect(): void {
    this.userClosed = true;
    this.clearReconnect();
    this.stopPing();
    this.stopAudio();
    this.socket?.close();
    this.socket = null;
    this.update({ connected: false, connecting: false, error: "" });
  }

  private open(host: string, port: number, password: string): void {
    this.socket?.close();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${host}:${port}/stream`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.update({ connecting: true, connected: false, error: "", host, port });
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: "hello", password }));
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") {
        this.lastFrame = event.data;
        this.frameCount += 1;
        const now = performance.now();
        if (now - this.frameWindow >= 1000) {
          this.update({ fps: this.frameCount });
          this.frameCount = 0;
          this.frameWindow = now;
        }
        this.frameHandler?.(event.data);
        return;
      }
      const message = JSON.parse(event.data) as {
        type: string;
        message?: string;
        width?: number;
        height?: number;
        fps?: number;
        codec?: string;
        audio?: boolean;
        bytesPerSec?: number;
        t?: number;
      };
      if (message.type === "hello") {
        this.frameWindow = performance.now();
        this.update({
          connecting: false,
          connected: true,
          error: "",
          width: message.width || 0,
          height: message.height || 0,
          codec: message.codec || "mjpeg",
        });
        if (message.audio && this.audioEnabled) this.startAudio(host, port);
        this.startPing();
      } else if (message.type === "error") {
        this.update({ connecting: false, connected: false, error: message.message || "Connection failed" });
        this.userClosed = true;
        socket.close();
      } else if (message.type === "stats") {
        this.update({
          fps: message.fps || this.state.fps,
          bandwidthMbps: ((message.bytesPerSec || 0) * 8) / 1_000_000,
          width: message.width || this.state.width,
          height: message.height || this.state.height,
        });
      } else if (message.type === "pong" && typeof message.t === "number") {
        this.update({ rttMs: Math.max(0, Math.round(performance.now() - message.t)) });
      }
    };
    socket.onerror = () => {
      this.update({ error: `Cannot reach ${host}:${port}` });
    };
    socket.onclose = () => {
      this.stopPing();
      this.stopAudio();
      const failed = this.state.connecting || this.state.connected;
      this.update({ connected: false, connecting: false });
      if (!this.userClosed && this.autoReconnect && failed) {
        this.reconnectTimer = window.setTimeout(() => this.open(host, port, password), 1500);
      }
    };
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = window.setInterval(() => {
      this.send({ type: "ping", t: performance.now() });
    }, 2000);
  }

  private stopPing(): void {
    if (this.pingTimer) window.clearInterval(this.pingTimer);
    this.pingTimer = 0;
  }

  private startAudio(host: string, port: number): void {
    this.stopAudio();
    const Context = window.AudioContext;
    if (!Context) return;
    if (!this.audioContext) this.audioContext = new Context({ sampleRate: 48000 });
    const ctx = this.audioContext;
    void ctx.resume();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${host}:${port}/audio`);
    socket.binaryType = "arraybuffer";
    this.audioSocket = socket;
    this.audioTime = 0;
    socket.onmessage = (event) => {
      if (!(event.data instanceof ArrayBuffer) || !event.data.byteLength) return;
      const pcm = new Int16Array(event.data);
      const buffer = ctx.createBuffer(1, pcm.length, 48000);
      const channel = buffer.getChannelData(0);
      for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 32768;
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      const now = ctx.currentTime;
      if (this.audioTime < now + 0.02) this.audioTime = now + 0.08;
      source.start(this.audioTime);
      this.audioTime += buffer.duration;
    };
  }

  private stopAudio(): void {
    this.audioSocket?.close();
    this.audioSocket = null;
    this.audioTime = 0;
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = 0;
  }

  private send(payload: Record<string, unknown>): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  pointer(x: number, y: number): void {
    this.send({ type: "pointer", x, y });
  }

  button(button: number, down: boolean, x: number, y: number): void {
    this.send({ type: "button", button, down, x, y });
  }

  wheel(dx: number, dy: number): void {
    this.send({ type: "wheel", dx, dy });
  }

  touch(phase: "down" | "move" | "up", slot: number, x: number, y: number): void {
    if (!this.touchEnabled) return;
    this.send({ type: "touch", phase, slot, x, y });
  }

  key(key: string, down: boolean): void {
    this.send({ type: "key", key, down });
  }
}

export async function fetchStatus(): Promise<ServerStatus | null> {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as ServerStatus;
  } catch {
    return null;
  }
}

export async function saveServerConfig(body: Record<string, unknown>): Promise<ServerStatus> {
  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = (await response.json()) as ServerStatus & { error?: string };
  if (!response.ok) throw new Error(payload.error || "Could not save settings");
  return payload;
}

function isPrivateIPv4(ip: string): boolean {
  return ip.startsWith("10.") || ip.startsWith("192.168.") || /^172\.(1[6-9]|2\d|3[0-1])\./.test(ip);
}

async function localIPv4(): Promise<string[]> {
  return new Promise((resolve) => {
    const found = new Set<string>();
    const pc = new RTCPeerConnection({ iceServers: [] });
    pc.createDataChannel("desklink");
    pc.onicecandidate = (event) => {
      const match = event.candidate?.candidate.match(/(\d+\.\d+\.\d+\.\d+)/);
      if (match && isPrivateIPv4(match[1])) found.add(match[1]);
    };
    pc.createOffer()
      .then((offer) => pc.setLocalDescription(offer))
      .catch(() => undefined);
    window.setTimeout(() => {
      pc.close();
      resolve([...found]);
    }, 800);
  });
}

export async function scanLan(port: number, onFound: (server: FoundServer) => void, signal: AbortSignal): Promise<void> {
  const prefixes = new Set<string>();
  if (isPrivateIPv4(location.hostname)) prefixes.add(location.hostname.split(".").slice(0, 3).join("."));
  for (const ip of await localIPv4()) prefixes.add(ip.split(".").slice(0, 3).join("."));
  const hosts = new Set<string>([location.hostname, "127.0.0.1"]);
  for (const prefix of prefixes) {
    for (let octet = 1; octet < 255; octet += 1) hosts.add(`${prefix}.${octet}`);
  }
  const queue = [...hosts];
  const seen = new Set<string>();

  async function worker(): Promise<void> {
    while (queue.length && !signal.aborted) {
      const ip = queue.shift();
      if (!ip) return;
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 700);
      try {
        const response = await fetch(`http://${ip}:${port}/api/info`, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) continue;
        const info = (await response.json()) as ServerStatus;
        const key = `${ip}:${info.port || port}`;
        if (seen.has(key)) continue;
        seen.add(key);
        onFound({
          ip,
          port: info.port || port,
          hostname: info.hostname || ip,
          tabletPort: info.tabletPort || (info.port || port) + 1,
        });
      } catch {
        // Hosts without Open Deskspace are expected during a scan.
      } finally {
        window.clearTimeout(timer);
      }
    }
  }

  await Promise.all(Array.from({ length: 32 }, () => worker()));
}
