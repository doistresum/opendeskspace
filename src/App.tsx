import { useEffect, useRef, useState } from 'react';
import ConnectionPage from './components/ConnectionPage';
import DisplayViewer from './components/DisplayViewer';
import Dashboard from './components/Dashboard';
import ServerSetup from './components/ServerSetup';
import Settings from './components/Settings';
import TabletViewer from './components/TabletViewer';
import { DeskLinkClient, LiveState, ServerStatus, fetchStatus } from './lib/session';

export type AppView = 'dashboard' | 'connect' | 'viewer' | 'setup' | 'settings' | 'tablet';

export interface ConnectionConfig {
  hostIP: string;
  port: number;
  resolution: string;
  quality: string;
  fps: number;
  touchEnabled: boolean;
  autoReconnect: boolean;
  keyboardEnabled: boolean;
  audioEnabled: boolean;
}

const storedConfig = (): ConnectionConfig => {
  const fallback: ConnectionConfig = {
    hostIP: '',
    port: 9090,
    resolution: '1920x1080',
    quality: 'high',
    fps: 30,
    touchEnabled: true,
    autoReconnect: true,
    keyboardEnabled: true,
    audioEnabled: true,
  };
  try {
    const raw = localStorage.getItem('desklink-config');
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
};

function App() {
  const clientRef = useRef<DeskLinkClient | null>(null);
  if (!clientRef.current) clientRef.current = new DeskLinkClient();
  const client = clientRef.current;
  const [currentView, setCurrentView] = useState<AppView>('dashboard');
  const [config, setConfig] = useState<ConnectionConfig>(storedConfig);
  const [server, setServer] = useState<ServerStatus | null>(null);
  const [live, setLive] = useState<LiveState>(client.state);
  const autoStarted = useRef(false);

  useEffect(() => client.subscribe(setLive), [client]);

  useEffect(() => {
    client.autoReconnect = config.autoReconnect;
    client.touchEnabled = config.touchEnabled;
    client.audioEnabled = config.audioEnabled;
    localStorage.setItem('desklink-config', JSON.stringify(config));
  }, [client, config]);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      const status = await fetchStatus();
      if (!stop) setServer(status);
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!server) return;
    setConfig((prev) => ({
      ...prev,
      resolution: server.config.resolution || prev.resolution,
      fps: server.config.fps || prev.fps,
      quality: server.config.quality || prev.quality,
      touchEnabled: server.config.touchEnabled,
      keyboardEnabled: server.config.keyboardEnabled,
      audioEnabled: server.config.audioEnabled,
      port: server.port || prev.port,
    }));
  }, [server?.config.resolution, server?.config.fps, server?.config.quality, server?.port]);

  useEffect(() => {
    const requested = new URLSearchParams(location.search).get('view');
    if (requested === 'connect' || requested === 'setup' || requested === 'settings' || requested === 'tablet' || requested === 'dashboard') {
      setCurrentView(requested);
    }
    if (requested !== 'viewer' || !server || autoStarted.current) return;
    if (server.passwordRequired) {
      setCurrentView('connect');
      return;
    }
    autoStarted.current = true;
    setCurrentView('viewer');
    client.connect(location.hostname, Number(location.port || server.port), '');
  }, [client, server]);

  const connectTo = (ip: string, port: number, password = '') => {
    setConfig((prev) => ({ ...prev, hostIP: ip, port }));
    setCurrentView('viewer');
    client.connect(ip, port, password);
  };

  const connectHere = () => {
    const port = Number(location.port || server?.port || config.port);
    connectTo(location.hostname, port, '');
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800/80 backdrop-blur-md border-b border-gray-700 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center">
                <i className="fas fa-desktop text-white text-sm"></i>
              </div>
              <span className="text-xl font-bold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">
                Open Deskspace
              </span>
              <span className="text-xs text-gray-400 ml-1 hidden sm:inline">for Linux</span>
            </div>
            <div className="flex items-center gap-2">
              {([
                ['dashboard', 'fas fa-home', 'Dashboard'],
                ['connect', 'fas fa-plug', 'Connect'],
                ['setup', 'fas fa-server', 'Server'],
                ['tablet', 'fas fa-tablet-alt', 'Tablet'],
                ['settings', 'fas fa-cog', 'Settings'],
              ] as const).map(([view, icon, label]) => (
                <button
                  key={view}
                  onClick={() => setCurrentView(view)}
                  className={`px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                    currentView === view
                      ? view === 'tablet' ? 'bg-teal-600 text-white' : 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:bg-gray-700'
                  }`}
                >
                  <i className={`${icon} mr-1.5`}></i>{label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {currentView === 'dashboard' && (
          <Dashboard
            server={server}
            live={live}
            onConnect={() => setCurrentView('connect')}
            onUseThisPc={connectHere}
            onViewDisplay={() => setCurrentView('viewer')}
            onTablet={() => setCurrentView('tablet')}
          />
        )}
        {currentView === 'connect' && (
          <ConnectionPage config={config} server={server} onConnect={connectTo} onUseThisPc={connectHere} />
        )}
        {currentView === 'viewer' && (
          <DisplayViewer client={client} live={live} onDisconnect={() => { client.disconnect(); setCurrentView('dashboard'); }} />
        )}
        {currentView === 'setup' && <ServerSetup server={server} />}
        {currentView === 'settings' && <Settings config={config} setConfig={setConfig} server={server} />}
        {currentView === 'tablet' && <TabletViewer server={server} onConnect={connectTo} />}
      </main>
    </div>
  );
}

export default App;
