package com.desklink.viewer;

import android.app.Activity;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.HashMap;
import java.util.Map;

public class MainActivity extends Activity {
    private static final int DISCOVERY_PORT = 53530;

    private LinearLayout connectLayout;
    private LinearLayout serverList;
    private TextView statusView;
    private EditText ipInput;
    private EditText portInput;
    private EditText passwordInput;
    private String streamResolution = "1280x720";
    private Button mode1080;
    private Button mode720;
    private Button mode480;
    private ImageView screen;
    private WifiManager.MulticastLock multicastLock;

    private Socket socket;
    private OutputStream output;
    private volatile boolean streaming;
    private int frameWidth = 1;
    private int frameHeight = 1;
    private Bitmap currentFrame;
    private final Map<String, String> discovered = new HashMap<String, String>();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setFlags(
                WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF0F172A);

        connectLayout = new LinearLayout(this);
        connectLayout.setOrientation(LinearLayout.VERTICAL);
        connectLayout.setPadding(48, 48, 48, 48);
        connectLayout.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView title = new TextView(this);
        title.setText("Open Deskspace");
        title.setTextSize(28);
        title.setTextColor(0xFF38BDF8);
        title.setGravity(Gravity.CENTER);
        connectLayout.addView(title);

        statusView = new TextView(this);
        statusView.setText("Looking for a Linux PC...");
        statusView.setTextColor(0xFFCBD5E1);
        statusView.setGravity(Gravity.CENTER);
        statusView.setPadding(0, 16, 0, 24);
        connectLayout.addView(statusView);

        serverList = new LinearLayout(this);
        serverList.setOrientation(LinearLayout.VERTICAL);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(serverList);
        connectLayout.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        ipInput = field("Linux PC address");
        portInput = field("Port");
        portInput.setText("9091");
        portInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        passwordInput = field("Password, if the PC asks");
        passwordInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        connectLayout.addView(ipInput);
        connectLayout.addView(portInput);
        connectLayout.addView(passwordInput);

        TextView resolutionLabel = new TextView(this);
        resolutionLabel.setText("Resolution");
        resolutionLabel.setTextColor(0xFFCBD5E1);
        resolutionLabel.setPadding(0, 20, 0, 0);
        connectLayout.addView(resolutionLabel);

        LinearLayout modes = new LinearLayout(this);
        modes.setOrientation(LinearLayout.HORIZONTAL);
        mode1080 = modeButton("1080p", "1920x1080");
        mode720 = modeButton("720p", "1280x720");
        mode480 = modeButton("480p", "854x480");
        modes.addView(mode1080);
        modes.addView(mode720);
        modes.addView(mode480);
        connectLayout.addView(modes);
        selectResolution(streamResolution);

        Button connect = button("Connect");
        connect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                connectTo(ipInput.getText().toString().trim(), portInput.getText().toString().trim());
            }
        });
        connectLayout.addView(connect);

        screen = new ImageView(this);
        screen.setBackgroundColor(0xFF000000);
        screen.setScaleType(ImageView.ScaleType.FIT_CENTER);
        screen.setVisibility(View.GONE);
        screen.setOnTouchListener(new View.OnTouchListener() {
            @Override
            public boolean onTouch(View v, MotionEvent event) {
                sendTouch(event);
                return true;
            }
        });

        root.addView(connectLayout, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(screen, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);
        acquireMulticast();
        startDiscovery();
    }

    private EditText field(String hint) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setTextColor(0xFFFFFFFF);
        input.setHintTextColor(0xFF64748B);
        input.setBackgroundColor(0xFF1E293B);
        input.setPadding(24, 20, 24, 20);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 12, 0, 0);
        input.setLayoutParams(params);
        return input;
    }

    private Button button(String label) {
        Button view = new Button(this);
        view.setText(label);
        view.setTextColor(0xFFFFFFFF);
        view.setBackgroundColor(0xFF0D9488);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 16, 0, 0);
        view.setLayoutParams(params);
        return view;
    }

    private Button modeButton(final String label, final String value) {
        Button view = new Button(this);
        view.setText(label);
        view.setTextColor(0xFFFFFFFF);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        params.setMargins(6, 8, 6, 0);
        view.setLayoutParams(params);
        view.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                selectResolution(value);
            }
        });
        return view;
    }

    private void selectResolution(String value) {
        streamResolution = value;
        paintMode(mode1080, "1920x1080".equals(value));
        paintMode(mode720, "1280x720".equals(value));
        paintMode(mode480, "854x480".equals(value));
    }

    private void paintMode(Button view, boolean selected) {
        if (view == null) return;
        view.setBackgroundColor(selected ? 0xFF0D9488 : 0xFF1E293B);
    }

    private void acquireMulticast() {
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi != null) {
                multicastLock = wifi.createMulticastLock("desklink");
                multicastLock.setReferenceCounted(false);
                multicastLock.acquire();
            }
        } catch (Exception ignored) {
        }
    }

    private void startDiscovery() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                DatagramSocket udp = null;
                try {
                    udp = new DatagramSocket(null);
                    udp.setReuseAddress(true);
                    udp.setBroadcast(true);
                    udp.bind(new InetSocketAddress(DISCOVERY_PORT));
                    udp.setSoTimeout(1000);
                    byte[] buffer = new byte[2048];
                    while (!isFinishing()) {
                        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                        try {
                            udp.receive(packet);
                        } catch (Exception timeout) {
                            continue;
                        }
                        String text = new String(packet.getData(), 0, packet.getLength(), "UTF-8");
                        if (!text.startsWith("DESKLINK ")) continue;
                        JSONObject json = new JSONObject(text.substring(9));
                        final String hostname = json.optString("hostname", "Linux PC");
                        final int tabletPort = json.optInt("tabletPort", 9091);
                        String address = packet.getAddress().getHostAddress();
                        JSONArray addresses = json.optJSONArray("addresses");
                        if (addresses != null && addresses.length() > 0) {
                            address = addresses.getString(0);
                        }
                        final String host = address;
                        final String key = host + ":" + tabletPort;
                        if (discovered.containsKey(key)) continue;
                        discovered.put(key, hostname);
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                addServer(hostname, host, tabletPort);
                            }
                        });
                    }
                } catch (Exception ignored) {
                } finally {
                    if (udp != null) udp.close();
                }
            }
        }).start();
    }

    private void addServer(final String hostname, final String host, final int port) {
        statusView.setText("Found an Open Deskspace PC");
        Button item = button(hostname + "  " + host + ":" + port);
        item.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                ipInput.setText(host);
                portInput.setText(String.valueOf(port));
                connectTo(host, String.valueOf(port));
            }
        });
        serverList.addView(item);
    }

    private void connectTo(final String host, final String portText) {
        if (host.length() == 0) {
            Toast.makeText(this, "Enter the Linux PC address", Toast.LENGTH_SHORT).show();
            return;
        }
        final int port;
        try {
            port = Integer.parseInt(portText);
        } catch (NumberFormatException error) {
            Toast.makeText(this, "Enter the tablet port", Toast.LENGTH_SHORT).show();
            return;
        }
        statusView.setText("Connecting to " + host + ":" + port);
        final String password = passwordInput.getText().toString().replace("\n", "").replace("\r", "");
        final String resolution = streamResolution;
        new Thread(new Runnable() {
            @Override
            public void run() {
                runSession(host, port, password, resolution);
            }
        }).start();
    }

    private void runSession(String host, int port, String password, String resolution) {
        Socket next = new Socket();
        try {
            next.connect(new InetSocketAddress(host, port), 8000);
            next.setTcpNoDelay(true);
            OutputStream out = next.getOutputStream();
            InputStream in = next.getInputStream();
            String hello = "HELLO";
            if (password.length() > 0) hello += " " + password;
            hello += " resolution=" + resolution + "\n";
            out.write(hello.getBytes("UTF-8"));
            out.flush();
            String response = readLine(in);
            if (!response.startsWith("OK ")) {
                showStatus(response.startsWith("ERR") ? response : "The PC refused the connection");
                next.close();
                return;
            }
            String[] parts = response.split(" ");
            frameWidth = Integer.parseInt(parts[1]);
            frameHeight = Integer.parseInt(parts[2]);
            socket = next;
            output = out;
            streaming = true;
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    connectLayout.setVisibility(View.GONE);
                    screen.setVisibility(View.VISIBLE);
                }
            });
            byte[] header = new byte[4];
            while (streaming) {
                readFully(in, header);
                int length = ((header[0] & 0xff) << 24)
                        | ((header[1] & 0xff) << 16)
                        | ((header[2] & 0xff) << 8)
                        | (header[3] & 0xff);
                if (length <= 0 || length > 8000000) break;
                byte[] jpeg = new byte[length];
                readFully(in, jpeg);
                final Bitmap bitmap = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length);
                if (bitmap == null) continue;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        Bitmap previous = currentFrame;
                        currentFrame = bitmap;
                        screen.setImageBitmap(bitmap);
                        if (previous != null && previous != bitmap) previous.recycle();
                    }
                });
            }
        } catch (Exception error) {
            showStatus("Connection failed: " + error.getMessage());
        } finally {
            streaming = false;
            closeSocket();
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    screen.setVisibility(View.GONE);
                    connectLayout.setVisibility(View.VISIBLE);
                }
            });
        }
    }

    private void sendTouch(MotionEvent event) {
        if (!streaming || output == null || currentFrame == null) return;
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_MOVE) {
            for (int i = 0; i < event.getPointerCount(); i++) {
                float[] point = normalize(event.getX(i), event.getY(i));
                if (point != null) send("TOUCH move " + event.getPointerId(i) + " " + point[0] + " " + point[1]);
            }
            return;
        }
        int index = event.getActionIndex();
        float[] point = normalize(event.getX(index), event.getY(index));
        if (point == null) return;
        int slot = event.getPointerId(index);
        if (action == MotionEvent.ACTION_DOWN || action == MotionEvent.ACTION_POINTER_DOWN) {
            send("TOUCH down " + slot + " " + point[0] + " " + point[1]);
        } else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_POINTER_UP
                || action == MotionEvent.ACTION_CANCEL) {
            send("TOUCH up " + slot + " " + point[0] + " " + point[1]);
        }
    }

    private float[] normalize(float x, float y) {
        int viewWidth = screen.getWidth();
        int viewHeight = screen.getHeight();
        if (viewWidth == 0 || viewHeight == 0 || frameWidth == 0 || frameHeight == 0) return null;
        float scale = Math.min(viewWidth / (float) frameWidth, viewHeight / (float) frameHeight);
        float drawnWidth = frameWidth * scale;
        float drawnHeight = frameHeight * scale;
        float left = (viewWidth - drawnWidth) / 2f;
        float top = (viewHeight - drawnHeight) / 2f;
        float nx = (x - left) / drawnWidth;
        float ny = (y - top) / drawnHeight;
        if (nx < 0f || ny < 0f || nx > 1f || ny > 1f) return null;
        return new float[]{nx, ny};
    }

    private void send(String line) {
        OutputStream out = output;
        if (out == null) return;
        try {
            synchronized (out) {
                out.write((line + "\n").getBytes("UTF-8"));
                out.flush();
            }
        } catch (Exception ignored) {
        }
    }

    private void showStatus(final String text) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                statusView.setText(text);
            }
        });
    }

    private static String readLine(InputStream in) throws Exception {
        StringBuilder line = new StringBuilder();
        while (true) {
            int value = in.read();
            if (value < 0) throw new Exception("PC closed the connection");
            if (value == '\n') break;
            if (value != '\r') line.append((char) value);
        }
        return line.toString();
    }

    private static void readFully(InputStream in, byte[] buffer) throws Exception {
        int offset = 0;
        while (offset < buffer.length) {
            int count = in.read(buffer, offset, buffer.length - offset);
            if (count < 0) throw new Exception("PC closed the connection");
            offset += count;
        }
    }

    private void closeSocket() {
        streaming = false;
        Socket current = socket;
        socket = null;
        output = null;
        if (current != null) {
            try {
                current.close();
            } catch (Exception ignored) {
            }
        }
    }

    @Override
    public void onBackPressed() {
        if (streaming) {
            closeSocket();
            screen.setVisibility(View.GONE);
            connectLayout.setVisibility(View.VISIBLE);
            statusView.setText("Disconnected");
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        closeSocket();
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
        super.onDestroy();
    }
}
