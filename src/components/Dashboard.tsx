import { QRCodeSVG } from 'qrcode.react';
import { LiveState, ServerStatus } from '../lib/session';

interface DashboardProps {
  server: ServerStatus | null;
  live: LiveState;
  onConnect: () => void;
  onUseThisPc: () => void;
  onViewDisplay: () => void;
  onTablet: () => void;
}

export default function Dashboard({ server, live, onConnect, onUseThisPc, onViewDisplay, onTablet }: DashboardProps) {
  const virtual = server?.virtualDisplay;
  return (
    <div className="space-y-8">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-blue-900/50 to-purple-900/50 border border-gray-700 p-8 md:p-12">
        <div className="relative z-10">
          <h1 className="text-3xl md:text-4xl font-bold mb-4">
            Welcome to <span className="bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">Open Deskspace</span>
          </h1>
          <p className="text-gray-300 text-lg max-w-2xl mb-6">
            This Linux PC adds a real GNOME monitor and streams it to a phone or tablet. Drag windows onto that monitor and use the other device as the screen.
          </p>
          <div className="flex flex-wrap gap-3">
            <button onClick={onUseThisPc} disabled={!server} className="px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-xl font-semibold">
              Open the extra monitor
            </button>
            <button onClick={onConnect} className="px-6 py-3 bg-gray-700 hover:bg-gray-600 rounded-xl font-semibold border border-gray-600">
              Connect to another PC
            </button>
          </div>
          {!server && (
            <p className="mt-4 text-sm text-amber-300">
              The host is not running. Start it with <code className="bg-black/30 px-1.5 py-0.5 rounded">python3 server/desklink_server.py --start</code>
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card title="Host" value={server ? server.hostname : 'Offline'} detail={server ? server.addresses[0] : 'Start desklink-server'} active={!!server} />
        <Card title="Stream" value={live.connected ? `${live.fps} FPS` : virtual ? 'Monitor ready' : 'Idle'} detail={live.connected ? `${live.rttMs} ms RTT` : 'No viewer connected'} active={live.connected} />
        <Card title="Resolution" value={virtual ? `${virtual.width}x${virtual.height}` : server?.config.resolution || '--'} detail={virtual ? `${virtual.clients} viewer(s)` : 'Created when a device connects'} active={!!virtual} />
      </div>

      {server && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 flex gap-6 items-center">
            <div className="bg-white p-3 rounded-xl">
              <QRCodeSVG value={server.viewerUrl} size={148} />
            </div>
            <div>
              <h3 className="font-semibold mb-2">Open on a modern browser</h3>
              <p className="text-sm text-gray-400 mb-3">Android 5 and newer can use Chrome. Android 4 uses the Open Deskspace app.</p>
              <p className="font-mono text-xs text-teal-300 break-all">{server.viewerUrl}</p>
              <button onClick={onTablet} className="mt-4 px-4 py-2 rounded-lg bg-teal-600/30 text-teal-200 text-sm">Tablet setup</button>
            </div>
          </div>
          <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6">
            <h3 className="font-semibold mb-3">Monitors GNOME sees</h3>
            <div className="space-y-2">
              {server.displays.map((monitor) => (
                <div key={`${monitor.connector}-${monitor.position}`} className="flex justify-between text-sm bg-gray-900 rounded-lg px-3 py-2">
                  <span>{monitor.connector} {monitor.virtual ? '(Open Deskspace)' : ''}</span>
                  <span className="text-gray-500">{monitor.mode} {monitor.position}</span>
                </div>
              ))}
              {!server.displays.length && <p className="text-sm text-gray-500">No monitor list yet.</p>}
            </div>
          </div>
        </div>
      )}

      {live.connected && (
        <button onClick={onViewDisplay} className="w-full px-5 py-3 bg-green-600 hover:bg-green-700 rounded-xl font-semibold">
          View the extra monitor
        </button>
      )}
    </div>
  );
}

function Card({ title, value, detail, active }: { title: string; value: string; detail: string; active: boolean }) {
  return (
    <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6">
      <div className={`w-2.5 h-2.5 rounded-full mb-4 ${active ? 'bg-green-400' : 'bg-gray-500'}`}></div>
      <p className="text-sm text-gray-400">{title}</p>
      <p className="text-xl font-bold">{value}</p>
      <p className="text-sm text-gray-500 mt-1">{detail}</p>
    </div>
  );
}
