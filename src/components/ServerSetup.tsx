import { ServerStatus } from '../lib/session';

export default function ServerSetup({ server }: { server: ServerStatus | null }) {
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <h2 className="text-2xl font-bold">Linux host</h2>
      <p className="text-gray-400">
        Open Deskspace runs inside your GNOME Wayland session. It asks Mutter for a virtual monitor, captures that monitor, and listens for the Android app. The tablet can ask for 1080p, 720p, or 480p when it connects.
      </p>
      <pre className="bg-gray-900 rounded-xl p-4 text-sm overflow-x-auto border border-gray-700">{`cd linspacedesk
python3 server/desklink_server.py --start --resolution 1920x1080
python3 server/desklink_server.py --status
python3 server/desklink_server.py --list-displays`}</pre>
      <div className="bg-gray-800/50 rounded-2xl border border-gray-700 p-6 text-sm space-y-2">
        <p>Web UI and API: port {server?.port || 9090}</p>
        <p>Android 4+ app: TCP port {server?.tabletPort || 9091}</p>
        <p>Discovery: UDP broadcast on port 53530</p>
        <p>Config file: ~/.config/desklink/config.json</p>
        <p>Allow the tablet through the firewall with <code>sudo ufw allow 9090/tcp</code> and <code>sudo ufw allow 9091/tcp</code>.</p>
      </div>
      <pre className="bg-gray-900 rounded-xl p-4 text-sm overflow-x-auto border border-gray-700">{`python3 server/desklink_server.py --init-config
python3 server/desklink_server.py --set-password "your-password"
python3 server/desklink_server.py --install-service
systemctl --user start desklink.service`}</pre>
      {server && (
        <p className="text-sm text-green-300">This session sees the host {server.hostname} on {server.addresses.join(', ')}.</p>
      )}
    </div>
  );
}
