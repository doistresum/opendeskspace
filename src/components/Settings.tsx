import { useState } from 'react';
import { ConnectionConfig } from '../App';
import { ServerStatus, saveServerConfig } from '../lib/session';

interface SettingsProps {
  config: ConnectionConfig;
  setConfig: (config: ConnectionConfig) => void;
  server: ServerStatus | null;
}

export default function Settings({ config, setConfig, server }: SettingsProps) {
  const [message, setMessage] = useState('');
  const [password, setPassword] = useState('');

  const update = async (patch: Partial<ConnectionConfig>, serverPatch: Record<string, unknown>) => {
    const next = { ...config, ...patch };
    setConfig(next);
    if (!server) {
      setMessage('Saved on this device. The host is not running, so the monitor was not changed.');
      return;
    }
    try {
      const saved = await saveServerConfig({ ...serverPatch, password });
      setMessage(saved && 'restartRequired' in saved ? 'Saved. Restart the host to apply the port.' : 'Saved. The virtual monitor uses these settings.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save');
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <h2 className="text-2xl font-bold">Settings</h2>
      <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 space-y-4">
        <label className="block text-sm">
          Resolution
          <select
            value={config.resolution}
            onChange={(event) => update({ resolution: event.target.value }, { resolution: event.target.value })}
            className="mt-2 w-full px-4 py-2.5 bg-gray-900 border border-gray-600 rounded-xl"
          >
            {[['1920x1080', '1080p'], ['1280x720', '720p'], ['854x480', '480p']].map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          Frame rate: {config.fps} FPS
          <input
            type="range"
            min={15}
            max={60}
            step={5}
            value={config.fps}
            onChange={(event) => update({ fps: Number(event.target.value) }, { fps: Number(event.target.value) })}
            className="mt-2 w-full"
          />
        </label>
        <div className="grid grid-cols-3 gap-3">
          {['low', 'medium', 'high'].map((quality) => (
            <button
              key={quality}
              onClick={() => update({ quality }, { quality })}
              className={`p-3 rounded-xl border capitalize ${config.quality === quality ? 'border-blue-500 text-blue-300' : 'border-gray-700 text-gray-400'}`}
            >
              {quality}
            </button>
          ))}
        </div>
      </div>
      <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 space-y-3">
        <Toggle label="Touch input" enabled={config.touchEnabled} onChange={(value) => update({ touchEnabled: value }, { touchEnabled: value })} />
        <Toggle label="Keyboard" enabled={config.keyboardEnabled} onChange={(value) => update({ keyboardEnabled: value }, { keyboardEnabled: value })} />
        <Toggle label="Desktop audio" enabled={config.audioEnabled} onChange={(value) => update({ audioEnabled: value }, { audioEnabled: value })} />
        <Toggle label="Reconnect automatically" enabled={config.autoReconnect} onChange={(value) => setConfig({ ...config, autoReconnect: value })} />
      </div>
      <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 space-y-3">
        <label className="block text-sm">
          Server password
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" className="mt-2 w-full px-4 py-2.5 bg-gray-900 border border-gray-600 rounded-xl" />
        </label>
        <div className="flex gap-2">
          <button onClick={() => update({}, { password })} className="px-4 py-2 rounded-lg bg-blue-600 text-sm">Save password</button>
          <button onClick={() => { setPassword(''); update({}, { password: '' }); }} className="px-4 py-2 rounded-lg bg-gray-700 text-sm">Remove password</button>
        </div>
        <p className="text-xs text-gray-500">Current host port: {server ? server.port : 'not running'}. Android tablets connect to port {server ? server.tabletPort : 9091}.</p>
      </div>
      {message && <p className="text-sm text-gray-300">{message}</p>}
    </div>
  );
}

function Toggle({ label, enabled, onChange }: { label: string; enabled: boolean; onChange: (value: boolean) => void }) {
  return (
    <button onClick={() => onChange(!enabled)} className="w-full flex items-center justify-between p-3 bg-gray-900/50 rounded-xl border border-gray-700">
      <span className="text-sm">{label}</span>
      <span className={`w-12 h-6 rounded-full relative ${enabled ? 'bg-blue-600' : 'bg-gray-600'}`}>
        <span className="absolute top-0.5 w-5 h-5 rounded-full bg-white" style={{ left: enabled ? 26 : 2 }}></span>
      </span>
    </button>
  );
}
