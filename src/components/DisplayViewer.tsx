import { useEffect, useRef } from 'react';
import { DeskLinkClient, LiveState } from '../lib/session';

interface DisplayViewerProps {
  client: DeskLinkClient;
  live: LiveState;
  onDisconnect: () => void;
}

export default function DisplayViewer({ client, live, onDisconnect }: DisplayViewerProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const urlRef = useRef<string>('');
  const slots = useRef<Map<number, number>>(new Map());

  useEffect(() => {
    const paint = (frame: ArrayBuffer) => {
      const image = imageRef.current;
      if (!image) return;
      const blob = new Blob([frame], { type: 'image/jpeg' });
      const url = URL.createObjectURL(blob);
      const previous = urlRef.current;
      urlRef.current = url;
      image.src = url;
      if (previous) URL.revokeObjectURL(previous);
    };
    client.frameHandler = paint;
    if (client.lastFrame) paint(client.lastFrame);
    return () => {
      client.frameHandler = null;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, [client]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      client.wheel(event.deltaX, event.deltaY);
    };
    stage.addEventListener('wheel', onWheel, { passive: false });
    return () => stage.removeEventListener('wheel', onWheel);
  }, [client]);

  const point = (clientX: number, clientY: number) => {
    const image = imageRef.current;
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    const naturalWidth = image.naturalWidth || live.width || 1;
    const naturalHeight = image.naturalHeight || live.height || 1;
    const scale = Math.min(rect.width / naturalWidth, rect.height / naturalHeight);
    const width = naturalWidth * scale;
    const height = naturalHeight * scale;
    const left = rect.left + (rect.width - width) / 2;
    const top = rect.top + (rect.height - height) / 2;
    const x = (clientX - left) / width;
    const y = (clientY - top) / height;
    if (x < 0 || y < 0 || x > 1 || y > 1) return null;
    return { x, y };
  };

  const slotFor = (pointerId: number) => {
    if (!slots.current.has(pointerId)) slots.current.set(pointerId, slots.current.size);
    return slots.current.get(pointerId) || 0;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between bg-gray-800/50 rounded-xl border border-gray-700 p-3">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className={`w-2.5 h-2.5 rounded-full ${live.connected ? 'bg-green-400 animate-pulse' : 'bg-yellow-400'}`}></div>
            <span className="text-sm text-gray-300">
              {live.connecting ? 'Creating virtual monitor...' : live.connected ? `Monitor on ${live.host}` : live.error || 'Not connected'}
            </span>
          </div>
          {live.connected && (
            <div className="hidden sm:flex items-center gap-3 text-xs text-gray-500">
              <span>{live.fps} FPS</span>
              <span>{live.rttMs} ms RTT</span>
              <span>{live.bandwidthMbps.toFixed(1)} Mbps</span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => stageRef.current?.requestFullscreen()}
            className="p-2 rounded-lg bg-gray-700 text-gray-400 hover:bg-gray-600 transition-all"
          >
            <i className="fas fa-expand text-sm"></i>
          </button>
          <button
            onClick={onDisconnect}
            className="px-3 py-2 rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 transition-all text-sm font-medium"
          >
            <i className="fas fa-times mr-1.5"></i>Disconnect
          </button>
        </div>
      </div>

      <div
        ref={stageRef}
        className="relative w-full rounded-2xl overflow-hidden border border-gray-700 bg-black touch-none select-none"
        style={{ height: 'calc(100vh - 200px)', minHeight: '400px' }}
        onPointerDown={(event) => {
          const where = point(event.clientX, event.clientY);
          if (!where) return;
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          if (event.pointerType === 'touch') client.touch('down', slotFor(event.pointerId), where.x, where.y);
          else client.button(event.button, true, where.x, where.y);
        }}
        onPointerMove={(event) => {
          const where = point(event.clientX, event.clientY);
          if (!where) return;
          if (event.pointerType === 'touch') {
            if (slots.current.has(event.pointerId)) client.touch('move', slotFor(event.pointerId), where.x, where.y);
          } else {
            client.pointer(where.x, where.y);
          }
        }}
        onPointerUp={(event) => {
          const where = point(event.clientX, event.clientY) || { x: 0, y: 0 };
          if (event.pointerType === 'touch') {
            client.touch('up', slotFor(event.pointerId), where.x, where.y);
            slots.current.delete(event.pointerId);
          } else {
            client.button(event.button, false, where.x, where.y);
          }
        }}
        onKeyDown={(event) => {
          event.preventDefault();
          client.key(event.key, true);
        }}
        onKeyUp={(event) => {
          event.preventDefault();
          client.key(event.key, false);
        }}
        tabIndex={0}
      >
        <img ref={imageRef} alt="" className="w-full h-full object-contain pointer-events-none" />
        {!live.connected && (
          <div className="absolute inset-0 flex items-center justify-center text-gray-400 text-sm px-6 text-center">
            {live.connecting
              ? 'Asking GNOME for a virtual monitor. It will appear to the side of your current displays.'
              : live.error || 'Connect to the Open Deskspace host to use this screen as a monitor.'}
          </div>
        )}
        {live.connected && (
          <div className="absolute top-3 right-3 bg-black/60 backdrop-blur-sm rounded-lg px-3 py-1.5 text-xs text-gray-300">
            {live.width}x{live.height} · {live.codec}
          </div>
        )}
      </div>
      <p className="text-sm text-gray-500">
        Drag windows onto the new monitor named Virtual remote monitor. Touch and clicks on this picture are sent to that monitor.
      </p>
    </div>
  );
}
