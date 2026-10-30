import { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { FoundServer, ServerStatus, scanLan } from '../lib/session';

interface TabletViewerProps {
  server: ServerStatus | null;
  onConnect: (ip: string, port: number, password?: string) => void;
}

export default function TabletViewer({ server, onConnect }: TabletViewerProps) {
  const [found, setFound] = useState<FoundServer[]>([]);
  const [scanning, setScanning] = useState(false);
  const [ip, setIp] = useState('');
  const viewerUrl = server?.viewerUrl || '';

  const scan = async () => {
    setScanning(true);
    setFound([]);
    const controller = new AbortController();
    window.setTimeout(() => controller.abort(), 8000);
    await scanLan(server?.port || 9090, (item) => {
      setFound((current) => current.some((known) => known.ip === item.ip) ? current : [...current, item]);
    }, controller.signal);
    setScanning(false);
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <h2 className="text-2xl font-bold">Tablet</h2>
      <p className="text-gray-400">
        Android 4.0 and newer use the Open Deskspace app. It finds this PC on the Wi-Fi, connects, and becomes the extra monitor. Choose 1080p, 720p, or 480p on the tablet. A current phone can also open the browser link.
      </p>
      {server && (
        <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 flex flex-col md:flex-row gap-6 items-center">
          <div className="bg-white p-3 rounded-xl">
            <QRCodeSVG value={viewerUrl} size={160} />
          </div>
          <div className="space-y-2 text-sm">
            <p className="font-mono text-teal-300 break-all">{viewerUrl}</p>
            <p>Android app address: {server.addresses[0]}:{server.tabletPort}</p>
            <p className="text-gray-500">Install <code>android/OpenDeskspace.apk</code> with <code>adb install -r android/OpenDeskspace.apk</code></p>
          </div>
        </div>
      )}
      <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 space-y-3">
        <button onClick={scan} className="px-4 py-2 rounded-lg bg-purple-600 text-sm">{scanning ? 'Scanning...' : 'Find Linux PCs'}</button>
        {found.map((item) => (
          <button key={item.ip} onClick={() => onConnect(item.ip, item.port)} className="block w-full text-left p-3 rounded-xl bg-gray-900">
            {item.hostname} · {item.ip}:{item.port}
          </button>
        ))}
        <div className="flex gap-2">
          <input value={ip} onChange={(event) => setIp(event.target.value)} placeholder="192.168.1.20" className="flex-1 px-4 py-3 bg-gray-900 border border-gray-600 rounded-xl" />
          <button onClick={() => ip && onConnect(ip, server?.port || 9090)} className="px-4 rounded-xl bg-teal-600">Connect</button>
        </div>
      </div>
    </div>
  );
}
