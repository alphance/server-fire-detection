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
        except Exception as e: print(f"DHT1 Error: {e}")
    
    if adafruit_dht:
        try:
            dht2 = adafruit_dht.DHT22(DHT_PIN_2, use_pulseio=False)
        except Exception as e: print(f"DHT2 Error: {e}")

    if adafruit_mlx90640:
        try:
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000) 
            mlx = adafruit_mlx90640.MLX90640(i2c)
            mlx.refresh_rate = MLX_REFRESH_RATE
        except Exception as e: print(f"MLX Error: {e}")
            
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

            dht_readings["t1"], dht_readings["h1"] = read_dht(dht1) if dht1 else (None, None)
            dht_readings["t2"], dht_readings["h2"] = read_dht(dht2) if dht2 else (None, None)
            
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

            except ValueError: continue 
            except Exception as e: time.sleep(0.5) 

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
    except Exception: return None

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
        html.H3("⚙️ Alert Configuration", style={'marginTop': 0}),
        
        # 1. ALERT SOURCE (Radio Buttons = Single Select)
        html.Div([
            html.Label("Active Alert Source (Select One):", style={'fontWeight': 'bold'}),
            dcc.RadioItems(
                id='alert-source-selector',
                options=[
                    {'label': ' Monitor DHT Sensors', 'value': 'dht'},
                    {'label': ' Monitor Thermal Camera', 'value': 'thermal'}
                ],
                value='thermal', # Default
                inline=True,
                style={'fontSize': '18px', 'marginBottom': '15px', 'marginLeft': '10px'}
            )
        ]),

        # 2. DYNAMIC INPUTS (These appear/disappear based on selection)
        html.Div(style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap'}, children=[
            
            # Inputs for DHT
            html.Div(id='dht-settings-container', style={'display': 'flex', 'flex': 2, 'gap': '20px'}, children=[
                html.Div([
                    html.Label("Max Temp Limit (°C):"),
                    dcc.Input(id='input-dht-temp', type='number', value=30, style={'width': '100%'})
                ], style={'flex': 1}),
                html.Div([
                    html.Label("Max Humidity Limit (%):"),
                    dcc.Input(id='input-dht-hum', type='number', value=60, style={'width': '100%'})
                ], style={'flex': 1}),
            ]),

            # Inputs for Thermal
            html.Div(id='thermal-settings-container', style={'display': 'none', 'flex': 2, 'gap': '20px'}, children=[
                html.Div([
                    html.Label("Thermal Trigger Mode:"),
                    dcc.Dropdown(
                        id='thermal-mode-select',
                        options=[
                            {'label': 'Trigger on Max Temp (Hotspot)', 'value': 'max'},
                            {'label': 'Trigger on Average Temp', 'value': 'avg'}
                        ],
                        value='max',
                        clearable=False
                    )
                ], style={'flex': 1}),
                html.Div([
                    html.Label("Temperature Limit (°C):"),
                    dcc.Input(id='input-thermal-temp', type='number', value=40, style={'width': '100%'})
                ], style={'flex': 1}),
            ]),

            # Email (Always visible) - NOW WITH DEBOUNCE
            html.Div([
                html.Label("Alert Email Address:"),
                dcc.Input(
                    id='input-email-addr', 
                    type='text', 
                    placeholder='Press Enter to Apply', 
                    style={'width': '100%'},
                    debounce=True  # <--- FIX: Only updates when Enter is pressed
                )
            ], style={'flex': 2}),
        ]),
        
        html.Div(id='alert-status-div', style={'marginTop': '10px', 'color': 'red', 'fontWeight': 'bold'})
    ]),

    # --- MAIN VISUALS ---
    html.Div(style={'display': 'flex', 'flexWrap': 'wrap'}, children=[
        
        # Thermal Left
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Live Thermal Feed"),
            html.Div([
                dcc.Checklist(
                    id='view-options',
                    options=[
                        {'label': ' Show Values', 'value': 'text'},
                        {'label': ' Force Square Pixels', 'value': 'square'}
                    ],
                    value=['square'], # Default to square look
                    inline=True
                )
            ], style={'marginBottom': '5px'}),
            dcc.Graph(id='thermal-heatmap', style={'height': '600px'}),
        ]),
        
        # History Right
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Thermal History"),
            dcc.Graph(id='mlx-history-graph', style={'height': '400px'}),
        ]),
        
        # Status Text & Bar Chart
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("DHT Status"),
            html.Div(id='dht-status-1'),
            html.Div(id='dht-status-2', style={'marginTop': '5px'}),
        ]),

        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Environment"),
            dcc.Graph(id='dht-bar-chart', style={'height': '400px'}),
        ]),
    ])
])

# ---- CALLBACK: UI TOGGLES ----
# This callback handles showing/hiding the inputs based on Radio Selection
@app.callback(
    [Output('dht-settings-container', 'style'),
     Output('thermal-settings-container', 'style')],
    [Input('alert-source-selector', 'value')]
)
def toggle_inputs(selection):
    if selection == 'dht':
        return {'display': 'flex', 'flex': 2, 'gap': '20px'}, {'display': 'none'}
    else:
        return {'display': 'none'}, {'display': 'flex', 'flex': 2, 'gap': '20px'}

# ---- CALLBACK: MAIN UPDATE ----
@app.callback(
    [Output('dht-status-1', 'children'),
     Output('dht-status-2', 'children'),
     Output('thermal-heatmap', 'figure'),
     Output('mlx-history-graph', 'figure'),
     Output('dht-bar-chart', 'figure'),
     Output('alert-status-div', 'children')],
    [Input('interval-component', 'n_intervals'),
     Input('alert-source-selector', 'value'),
     Input('view-options', 'value'),
     Input('input-dht-temp', 'value'),
     Input('input-dht-hum', 'value'),
     Input('input-thermal-temp', 'value'),
     Input('thermal-mode-select', 'value'),
     Input('input-email-addr', 'value')]
)
def update_dashboard(n, alert_source, view_opts, dht_temp_lim, dht_hum_lim, thermal_lim, thermal_mode, email_addr):
    global last_alert_time
    
    with data_lock:
        dht = latest_data["dht"].copy()
        frame = latest_data["mlx_frame"].copy()
        stats = {k: list(v) for k, v in latest_data["mlx_stats"].items()}

    # --- ALERT LOGIC ---
    alert_msg = ""
    triggers = []
    current_time = time.time()
    
    # FIX: Validate email structure before proceeding
    if email_addr and "@" in email_addr and "." in email_addr:
        
        # 1. DHT Logic
        if alert_source == 'dht' and dht_temp_lim and dht_hum_lim:
            if dht['t1'] and dht['t1'] > dht_temp_lim: triggers.append(f"S1 Temp: {dht['t1']:.1f}C")
            if dht['h1'] and dht['h1'] > dht_hum_lim: triggers.append(f"S1 Hum: {dht['h1']:.1f}%")
            if dht['t2'] and dht['t2'] > dht_temp_lim: triggers.append(f"S2 Temp: {dht['t2']:.1f}C")
            if dht['h2'] and dht['h2'] > dht_hum_lim: triggers.append(f"S2 Hum: {dht['h2']:.1f}%")

        # 2. Thermal Logic
        if alert_source == 'thermal' and thermal_lim:
            val = np.max(frame) if thermal_mode == 'max' else np.mean(frame)
            if val > thermal_lim:
                triggers.append(f"Thermal {thermal_mode.upper()}: {val:.1f}C")

        # 3. Send Email
        if triggers:
            alert_msg = f"⚠️ Alert: {', '.join(triggers)}"
            if (current_time - last_alert_time) > ALERT_COOLDOWN:
                subject = f"SENSOR ALERT: {len(triggers)} Warnings"
                body = f"Limits breached:\n" + "\n".join(triggers)
                threading.Thread(target=send_alert_email_thread, args=(email_addr, subject, body, frame)).start()
                last_alert_time = current_time
                alert_msg += " (Email Sent)"
            else:
                alert_msg += f" (Cooldown: {int(ALERT_COOLDOWN - (current_time - last_alert_time))}s)"
    elif email_addr:
        # Warn if email is invalid (but don't trigger cooldown)
        alert_msg = "⚠️ Invalid Email Address format"

    # --- VISUALIZATIONS ---
    
    # Heatmap
    t_min, t_max = np.min(frame), np.max(frame)
    if t_min == t_max: t_max += 1
    
    text_data = frame.round(0).astype(int) if 'text' in view_opts else None
    
    heatmap_fig = go.Figure(data=[go.Heatmap(
        z=frame, zmin=t_min, zmax=t_max, colorscale='Inferno',
        text=text_data, texttemplate="%{text}", textfont={"size":10}
    )])
    
    # Check aspect ratio setting
    layout_args = dict(title=f'Max: {t_max:.1f}°C', yaxis=dict(autorange='reversed'))
    if 'square' in view_opts:
        layout_args['yaxis']['scaleanchor'] = 'x'
    
    heatmap_fig.update_layout(**layout_args)

    # History
    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['max'], name='Max'))
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['avg'], name='Avg'))
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['min'], name='Min')) 
    history_fig.update_layout(title='Thermal Trends')

    # Bar Chart
    dht_fig = go.Figure(data=[
        go.Bar(name='Temp', x=['S1', 'S2'], y=[dht['t1'] or 0, dht['t2'] or 0]),
        go.Bar(name='Hum', x=['S1', 'S2'], y=[dht['h1'] or 0, dht['h2'] or 0])
    ])

    s1 = f"S1: {dht['t1']:.1f}°C" if dht['t1'] else "S1: No Data"
    s2 = f"S2: {dht['t2']:.1f}°C" if dht['t2'] else "S2: No Data"

    return s1, s2, heatmap_fig, history_fig, dht_fig, alert_msg

if __name__ == "__main__":
    dht1, dht2, mlx = setup_sensors()
    threading.Thread(target=sensor_reading_thread, args=(dht1, dht2, mlx), daemon=True).start()
    app.run(host='0.0.0.0', port=8050, debug=True, use_reloader=False)
