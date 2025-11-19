import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
import numpy as np
import collections
import threading
import time
import board
import busio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import io
import matplotlib
# Set backend to Agg to prevent GUI errors on headless Pi
matplotlib.use('Agg') 
import matplotlib.pyplot as plt

# --- Try to import sensor libraries ---
try:
    import adafruit_dht
except ImportError:
    print("WARNING: adafruit_dht library not found. DHT sensors will be disabled.")
    adafruit_dht = None

try:
    import adafruit_mlx90640
except ImportError:
    print("WARNING: adafruit_mlx90640 library not found. Thermal camera will be disabled.")
    adafruit_mlx90640 = None

# ---- CONFIGURATION ----
DHT_PIN_1 = board.D23
DHT_PIN_2 = board.D24
DHT_POLL_INTERVAL = 2.0
MLX_REFRESH_RATE = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ if adafruit_mlx90640 else None
DASH_REFRESH_INTERVAL = 1500 

# MLX sensor dimensions
MLX_WIDTH = 32
MLX_HEIGHT = 24
MAX_HISTORY = 100

# ---- EMAIL CONFIGURATION (GMX) ----
EMAIL_SENDER = "your_email@gmx.com"
EMAIL_PASSWORD = "your_gmx_password" 
SMTP_SERVER = "mail.gmx.com"
SMTP_PORT = 587

# Alert Cooldown (seconds)
ALERT_COOLDOWN = 300 
last_alert_time = 0

# ---- GLOBAL DATA STORE ----
data_lock = threading.Lock()
latest_data = {
    "dht": {"t1": None, "h1": None, "t2": None, "h2": None},
    "mlx_frame": np.zeros((MLX_HEIGHT, MLX_WIDTH)), 
    "mlx_stats": {
        "time": collections.deque(maxlen=MAX_HISTORY),
        "min": collections.deque(maxlen=MAX_HISTORY),
        "max": collections.deque(maxlen=MAX_HISTORY),
        "avg": collections.deque(maxlen=MAX_HISTORY),
    }
}
last_dht_read_time = 0

# ---- SENSOR INITIALIZATION ----
def setup_sensors():
    dht1, dht2, mlx = None, None, None
    if adafruit_dht:
        try:
            dht1 = adafruit_dht.DHT22(DHT_PIN_1, use_pulseio=False)
            print("DHT Sensor 1 initialized.")
        except Exception as e:
            print(f"WARNING: DHT1 Init Failed: {e}")
    
    if adafruit_dht:
        try:
            dht2 = adafruit_dht.DHT22(DHT_PIN_2, use_pulseio=False)
            print("DHT Sensor 2 initialized.")
        except Exception as e:
            print(f"WARNING: DHT2 Init Failed: {e}")

    if adafruit_mlx90640:
        try:
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000) 
            mlx = adafruit_mlx90640.MLX90640(i2c)
            mlx.refresh_rate = MLX_REFRESH_RATE
            print("MLX90640 Thermal Camera initialized.")
        except Exception as e:
            print(f"CRITICAL ERROR: MLX Init Failed: {e}")
            
    return dht1, dht2, mlx

# ---- BACKGROUND SENSOR THREAD ----
def sensor_reading_thread(dht1, dht2, mlx):
    global last_dht_read_time
    raw_frame = [0] * (MLX_WIDTH * MLX_HEIGHT) 

    while True:
        current_time = time.monotonic()
        
        # 1. Read DHT Sensors
        if (current_time - last_dht_read_time) > DHT_POLL_INTERVAL:
            dht_readings = {}
            def read_dht(sensor):
                try: return sensor.temperature, sensor.humidity
                except: return None, None

            if dht1: dht_readings["t1"], dht_readings["h1"] = read_dht(dht1)
            else: dht_readings["t1"], dht_readings["h1"] = None, None
            
            if dht2: dht_readings["t2"], dht_readings["h2"] = read_dht(dht2)
            else: dht_readings["t2"], dht_readings["h2"] = None, None
            
            with data_lock:
                latest_data["dht"].update(dht_readings)
            last_dht_read_time = current_time

        # 2. Read MLX Sensor
        if mlx:
            try:
                mlx.getFrame(raw_frame)
                frame_arr = np.array(raw_frame)
                frame_2d = frame_arr.reshape((MLX_HEIGHT, MLX_WIDTH))

                with data_lock:
                    latest_data["mlx_frame"] = frame_2d
                    latest_data["mlx_stats"]["time"].append(current_time)
                    latest_data["mlx_stats"]["min"].append(np.min(frame_arr))
                    latest_data["mlx_stats"]["max"].append(np.max(frame_arr))
                    latest_data["mlx_stats"]["avg"].append(np.mean(frame_arr))

            except ValueError:
                continue 
            except Exception as e:
                print(f"MLX Error: {e}")
                time.sleep(0.5) 

        if not mlx:
            time.sleep(DASH_REFRESH_INTERVAL / 1000.0)

# ---- EMAIL HELPER FUNCTIONS ----
def generate_thermal_image_bytes(frame_data):
    buf = io.BytesIO()
    try:
        plt.figure(figsize=(5, 4))
        plt.imshow(frame_data, cmap='inferno')
        plt.colorbar(label='Temp (°C)')
        plt.title("Snapshot at Alert Time")
        plt.axis('off')
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.read()
    except Exception as e:
        print(f"Error generating image: {e}")
        return None

def send_alert_email_thread(target_email, subject, body, thermal_frame):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = target_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        img_data = generate_thermal_image_bytes(thermal_frame)
        if img_data:
            image = MIMEImage(img_data, name="thermal_snapshot.png")
            msg.attach(image)

        print(f"--- Connecting to GMX ({SMTP_SERVER}) ---")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"--- ALERT EMAIL SENT TO {target_email} ---")
    except Exception as e:
        print(f"--- FAILED TO SEND EMAIL: {e} ---")

# ---- DASH APP SETUP ----
app = dash.Dash(__name__)
app.title = "Sensor Dashboard"

app.layout = html.Div(style={'fontFamily': 'Arial', 'maxWidth': '1200px', 'margin': '0 auto'}, children=[
    html.H1("Server Room Monitor", style={'textAlign': 'center'}),
    
    dcc.Interval(id='interval-component', interval=DASH_REFRESH_INTERVAL, n_intervals=0),
    
    # --- SETTINGS PANEL ---
    html.Div(style={'backgroundColor': '#f0f0f0', 'padding': '15px', 'borderRadius': '10px', 'marginBottom': '20px'}, children=[
        html.H3("⚙️ Alert Settings", style={'marginTop': 0}),
        
        # ALERT SOURCES TOGGLE
        html.Div([
            html.Label("Active Alert Sources:", style={'fontWeight': 'bold'}),
            dcc.Checklist(
                id='alert-sources',
                options=[
                    {'label': ' Monitor DHT Sensors', 'value': 'dht'},
                    {'label': ' Monitor Thermal Camera', 'value': 'thermal'}
                ],
                value=['thermal'], # Default to JUST thermal
                inline=True,
                style={'fontSize': '18px', 'marginBottom': '15px'}
            )
        ]),

        html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap'}, children=[
            # Names
            html.Div([html.Label("Sensor 1 Name:"), dcc.Input(id='input-name-1', value='Sensor 1', style={'width': '100%'})], style={'flex': 1}),
            html.Div([html.Label("Sensor 2 Name:"), dcc.Input(id='input-name-2', value='Sensor 2', style={'width': '100%'})], style={'flex': 1}),
            
            # Thresholds (Shared)
            html.Div([
                html.Label("Max Temp Limit (°C):"),
                dcc.Input(id='input-thresh-temp', type='number', value=30, style={'width': '100%'})
            ], style={'flex': 1}),
            html.Div([
                html.Label("Max Humidity Limit (%):"),
                dcc.Input(id='input-thresh-hum', type='number', value=60, style={'width': '100%'})
            ], style={'flex': 1}),

            # Email
            html.Div([
                html.Label("Alert Email Address:"),
                dcc.Input(id='input-email-addr', type='text', placeholder='you@example.com', style={'width': '100%'})
            ], style={'flex': 2}),
        ]),
        html.Div(id='alert-status-div', style={'marginTop': '10px', 'color': 'red', 'fontWeight': 'bold'})
    ]),

    # --- MAIN VISUALS ---
    html.Div(style={'display': 'flex', 'flexWrap': 'wrap'}, children=[
        
        # Thermal Left
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Live Thermal Feed"),
            # Re-added the Show Values Toggle
            dcc.Checklist(
                id='mlx-text-overlay-toggle',
                options=[{'label': ' Show Values', 'value': 'show'}],
                value=[], inline=True
            ),
            dcc.Graph(id='thermal-heatmap', style={'height': '600px'}),
        ]),
        
        # History Right
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Thermal History"),
            dcc.Graph(id='mlx-history-graph', style={'height': '400px'}),
        ]),
        
        # Status Text
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("DHT Status"),
            html.Div(id='dht-status-1', style={'fontSize': 18, 'fontWeight': 'bold', 'padding': '10px', 'border': '1px solid #ccc'}),
            html.Div(id='dht-status-2', style={'fontSize': 18, 'fontWeight': 'bold', 'padding': '10px', 'border': '1px solid #ccc', 'marginTop': '5px'}),
        ]),

        # Bar Chart
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Environment Comparison"),
            dcc.Graph(id='dht-bar-chart', style={'height': '400px'}),
        ]),
    ])
])

# ---- DASH CALLBACK ----
@app.callback(
    [Output('dht-status-1', 'children'),
     Output('dht-status-2', 'children'),
     Output('thermal-heatmap', 'figure'),
     Output('mlx-history-graph', 'figure'),
     Output('dht-bar-chart', 'figure'),
     Output('alert-status-div', 'children')],
    [Input('interval-component', 'n_intervals'),
     Input('alert-sources', 'value'),
     Input('mlx-text-overlay-toggle', 'value'),
     Input('input-name-1', 'value'),
     Input('input-name-2', 'value'),
     Input('input-thresh-temp', 'value'),
     Input('input-thresh-hum', 'value'),
     Input('input-email-addr', 'value')]
)
def update_dashboard(n, alert_sources, text_overlay, name1, name2, thresh_temp, thresh_hum, email_addr):
    global last_alert_time
    
    with data_lock:
        dht = latest_data["dht"].copy()
        frame = latest_data["mlx_frame"].copy()
        stats = {k: list(v) for k, v in latest_data["mlx_stats"].items()}

    # --- ALERT LOGIC ---
    alert_msg = ""
    triggers = []
    current_time = time.time()
    
    if email_addr and thresh_temp:
        
        # 1. DHT CHECKS (Only if 'dht' is checked in UI)
        if 'dht' in alert_sources:
            if dht['t1'] and dht['t1'] > thresh_temp: triggers.append(f"{name1} Temp: {dht['t1']:.1f}C")
            if dht['h1'] and dht['h1'] > thresh_hum: triggers.append(f"{name1} Hum: {dht['h1']:.1f}%")
            if dht['t2'] and dht['t2'] > thresh_temp: triggers.append(f"{name2} Temp: {dht['t2']:.1f}C")
            if dht['h2'] and dht['h2'] > thresh_hum: triggers.append(f"{name2} Hum: {dht['h2']:.1f}%")

        # 2. THERMAL CHECKS (Only if 'thermal' is checked in UI)
        if 'thermal' in alert_sources:
            t_max_now = np.max(frame)
            if t_max_now > thresh_temp:
                triggers.append(f"Thermal Hotspot: {t_max_now:.1f}C")

        # 3. SEND EMAIL
        if triggers:
            alert_msg = f"⚠️ Alert: {', '.join(triggers)}"
            if (current_time - last_alert_time) > ALERT_COOLDOWN:
                subject = f"SENSOR ALERT: {len(triggers)} Warnings"
                body = f"The following limits were breached:\n\n" + "\n".join(triggers)
                
                threading.Thread(
                    target=send_alert_email_thread,
                    args=(email_addr, subject, body, frame)
                ).start()
                
                last_alert_time = current_time
                alert_msg += " (Email Sent)"
            else:
                alert_msg += f" (Cooldown: {int(ALERT_COOLDOWN - (current_time - last_alert_time))}s)"

    # --- VISUALIZATIONS ---
    # Heatmap
    t_min, t_max = np.min(frame), np.max(frame)
    if t_min == t_max: t_max += 1
    
    # Show text values if toggle is checked
    text_data = frame.round(0).astype(int) if 'show' in text_overlay else None

    heatmap_fig = go.Figure(data=[go.Heatmap(
        z=frame, zmin=t_min, zmax=t_max, colorscale='Inferno',
        text=text_data, texttemplate="%{text}", textfont={"size":10}
    )])
    heatmap_fig.update_layout(title=f'Max: {t_max:.1f}°C', yaxis=dict(autorange='reversed'))

    # History
    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['max'], name='Max Temp'))
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['avg'], name='Avg Temp'))
    history_fig.update_layout(title='Thermal Trends')

    # Bar Chart
    dht_fig = go.Figure(data=[
        go.Bar(name='Temp', x=[name1, name2], y=[dht['t1'] or 0, dht['t2'] or 0]),
        go.Bar(name='Hum', x=[name1, name2], y=[dht['h1'] or 0, dht['h2'] or 0])
    ])

    s1 = f"{name1}: {dht['t1']:.1f}°C" if dht['t1'] else "No Data (Disabled)"
    s2 = f"{name2}: {dht['t2']:.1f}°C" if dht['t2'] else "No Data (Disabled)"

    return s1, s2, heatmap_fig, history_fig, dht_fig, alert_msg

if __name__ == "__main__":
    print("--- Initializing Sensors ---")
    dht1, dht2, mlx = setup_sensors()
    
    # Start sensor thread
    threading.Thread(target=sensor_reading_thread, args=(dht1, dht2, mlx), daemon=True).start()
    
    print("--- Dashboard running on http://0.0.0.0:8050 ---")
    app.run(host='0.0.0.0', port=8050, debug=True, use_reloader=False)
