import { FormEvent, useState } from 'react';
import { ConnectionConfig } from '../App';
import { FoundServer, ServerStatus, scanLan } from '../lib/session';

interface ConnectionPageProps {
  config: ConnectionConfig;
  server: ServerStatus | null;
  onConnect: (ip: string, port: number, password?: string) => void;
  onUseThisPc: () => void;
}

export default function ConnectionPage({ config, server, onConnect, onUseThisPc }: ConnectionPageProps) {
  const [ip, setIp] = useState(config.hostIP);
  const [port, setPort] = useState(String(config.port || 9090));
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [found, setFound] = useState<FoundServer[]>([]);
  const [scanning, setScanning] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ip.trim()) {
      setError('Enter the Linux PC address');
      return;
    }
    const parsed = Number(port);
    if (!parsed) {
      setError('Enter the Open Deskspace port');
      return;
    }
    setError('');
    onConnect(ip.trim(), parsed, password);
  };

  const scan = async () => {
    setScanning(true);
    setFound([]);
    const controller = new AbortController();
    window.setTimeout(() => controller.abort(), 8000);
    await scanLan(Number(port) || 9090, (serverFound) => {
      setFound((current) => current.some((item) => item.ip === serverFound.ip) ? current : [...current, serverFound]);
    }, controller.signal);
    setScanning(false);
  };

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div className="text-center mb-8">
        <h2 className="text-2xl font-bold mb-2">Connect to the Linux host</h2>
        <p className="text-gray-400">The host adds a real virtual monitor and streams it here.</p>
      </div>

      {server && (
        <button
          onClick={onUseThisPc}
          className="w-full px-6 py-3 bg-blue-600 hover:bg-blue-700 rounded-xl font-semibold"
        >
          Use this computer ({server.hostname})
        </button>
      )}

      <form onSubmit={submit} className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 space-y-4">
        <label className="block text-sm text-gray-300">
          Host IP
          <input
            value={ip}
            onChange={(event) => setIp(event.target.value)}
            placeholder="192.168.1.20"
            className="mt-2 w-full px-4 py-3 bg-gray-900 border border-gray-600 rounded-xl"
          />
        </label>
        <label className="block text-sm text-gray-300">
          Port
          <input
            value={port}
            onChange={(event) => setPort(event.target.value)}
            className="mt-2 w-full px-4 py-3 bg-gray-900 border border-gray-600 rounded-xl"
          />
        </label>
        <label className="block text-sm text-gray-300">
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={server?.passwordRequired ? 'Required' : 'Optional'}
            className="mt-2 w-full px-4 py-3 bg-gray-900 border border-gray-600 rounded-xl"
          />
        </label>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button type="submit" className="w-full px-6 py-3 bg-gray-700 hover:bg-gray-600 rounded-xl font-semibold">
          Connect
        </button>
      </form>

      <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">PCs on this network</h3>
          <button onClick={scan} disabled={scanning} className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-sm">
            {scanning ? 'Scanning...' : 'Scan'}
          </button>
        </div>
        <div className="space-y-2">
          {found.map((item) => (
            <button
              key={`${item.ip}:${item.port}`}
              onClick={() => { setIp(item.ip); setPort(String(item.port)); }}
              className="w-full text-left p-3 rounded-xl bg-gray-900 border border-gray-700 hover:border-blue-500"
            >
              <p className="text-sm">{item.hostname}</p>
              <p className="text-xs text-gray-500">{item.ip}:{item.port} · tablet {item.tabletPort}</p>
            </button>
          ))}
          {!found.length && <p className="text-sm text-gray-500">Scan looks for an Open Deskspace host answering on this port.</p>}
        </div>
      </div>
    </div>
  );
}
