"""
secure_dashboard.py

Secure local-only Dash dashboard with Gmail App Password alerts.
Supports 4 DHT22 Sensors + 1 MLX90640 Thermal Camera.
Features: 
- Radio Button Selection (Single Source)
- Humidity & Temperature Logic
- 4-Sensor Array
- Centralized Configuration Variables
"""

import os
import logging
import threading
import time
import datetime
import io
import collections

from dotenv import load_dotenv
load_dotenv()

import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Optional sensor libs
try:
    import board, busio
except Exception:
    board, busio = None, None

try:
    import adafruit_dht
except Exception:
    adafruit_dht = None

try:
    import adafruit_mlx90640
except Exception:
    adafruit_mlx90640 = None

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

# ---------------------------
# Config & Thresholds
# ---------------------------
GMAIL_EMAIL = os.getenv("EMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "300"))
DASH_REFRESH_INTERVAL = int(os.getenv("DASH_REFRESH_INTERVAL_MS", "1500"))

# --- DEFAULT THRESHOLDS (Change these numbers to adjust defaults) ---
DEFAULT_DHT_TEMP_THRESHOLD = 30   # Max ambient temp in °C
DEFAULT_DHT_HUM_THRESHOLD = 60    # Max humidity in %
DEFAULT_THERMAL_TEMP_THRESHOLD = 40 # Max surface temp in °C

MLX_WIDTH = 32
MLX_HEIGHT = 24
MAX_HISTORY = 200
DHT_POLL_INTERVAL = float(os.getenv("DHT_POLL_INTERVAL", "2.0"))

# --- SENSOR PIN CONFIGURATION ---

# OPTION A: LIVE MODE (Uncomment these 4 lines when sensors are connected!)
# DHT_PIN_1 = getattr(board, "D23", None) if board else None
# DHT_PIN_2 = getattr(board, "D24", None) if board else None
# DHT_PIN_3 = getattr(board, "D17", None) if board else None 
# DHT_PIN_4 = getattr(board, "D27", None) if board else None 

# OPTION B: DEMO MODE (Use this right now for speed/testing)
DHT_PIN_1 = None
DHT_PIN_2 = None
DHT_PIN_3 = None
DHT_PIN_4 = None

MLX_REFRESH_RATE = None
if adafruit_mlx90640 and hasattr(adafruit_mlx90640, "RefreshRate"):
    MLX_REFRESH_RATE = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ

# ---------------------------
# Logging
# ---------------------------
LOGFILE = os.getenv("DASH_LOGFILE", "sensor_dashboard.log")
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(),
                              logging.FileHandler(LOGFILE)])
logger = logging.getLogger("sensor_dashboard")

# ---------------------------
# Global Data
# ---------------------------
data_lock = threading.Lock()
latest_data = {
    "dht": {
        "t1": None, "h1": None,
        "t2": None, "h2": None,
        "t3": None, "h3": None,
        "t4": None, "h4": None
    },
    "mlx_frame": np.zeros((MLX_HEIGHT, MLX_WIDTH)),
    "mlx_stats": {
        "time": collections.deque(maxlen=MAX_HISTORY),
        "min": collections.deque(maxlen=MAX_HISTORY),
        "max": collections.deque(maxlen=MAX_HISTORY),
        "avg": collections.deque(maxlen=MAX_HISTORY),
    }
}
last_dht_read_time = 0
last_alert_time = 0

# ---------------------------
# Sensor init
# ---------------------------
def setup_sensors():
    sensors = [None, None, None, None]
    mlx = None
    
    def init_dht(pin, name):
        if adafruit_dht and pin:
            try:
                s = adafruit_dht.DHT22(pin, use_pulseio=False)
                logger.info(f"{name} initialized")
                return s
            except Exception as e:
                logger.warning(f"{name} init failed: {e}")
        return None

    sensors[0] = init_dht(DHT_PIN_1, "DHT1")
    sensors[1] = init_dht(DHT_PIN_2, "DHT2")
    sensors[2] = init_dht(DHT_PIN_3, "DHT3")
    sensors[3] = init_dht(DHT_PIN_4, "DHT4")

    if adafruit_mlx90640 and board:
        try:
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
            mlx = adafruit_mlx90640.MLX90640(i2c)
            if MLX_REFRESH_RATE:
                mlx.refresh_rate = MLX_REFRESH_RATE
            logger.info("MLX initialized")
        except Exception as e:
            logger.warning(f"MLX init failed: {e}")
    
    return sensors, mlx

# ---------------------------
# Background sensor reading
# ---------------------------
def sensor_reading_thread(dht_sensors, mlx):
    global last_dht_read_time
    raw_frame = [0.0] * (MLX_WIDTH * MLX_HEIGHT)
    
    while True:
        current_time = time.monotonic()

        # --- READ DHTs ---
        if (current_time - last_dht_read_time) > DHT_POLL_INTERVAL:
            def read_dht(sensor):
                try:
                    if sensor:
                        return sensor.temperature, sensor.humidity
                except Exception as e:
                    logger.debug(f"DHT read error: {e}")
                return None, None
            
            results = []
            for s in dht_sensors:
                results.append(read_dht(s))
            
            with data_lock:
                latest_data["dht"]["t1"], latest_data["dht"]["h1"] = results[0]
                latest_data["dht"]["t2"], latest_data["dht"]["h2"] = results[1]
                latest_data["dht"]["t3"], latest_data["dht"]["h3"] = results[2]
                latest_data["dht"]["t4"], latest_data["dht"]["h4"] = results[3]

            last_dht_read_time = current_time

        # --- READ THERMAL CAMERA ---
        if mlx:
            try:
                mlx.getFrame(raw_frame)
                frame_arr = np.array(raw_frame).reshape((MLX_HEIGHT, MLX_WIDTH))
                
                # Filter ghost noise
                if np.max(frame_arr) > 150:
                    time.sleep(0.1)
                    continue

                with data_lock:
                    latest_data["mlx_frame"] = frame_arr.copy()
                    time_str = datetime.datetime.now().strftime("%H:%M:%S")
                    latest_data["mlx_stats"]["time"].append(time_str)
                    latest_data["mlx_stats"]["min"].append(float(np.min(frame_arr)))
                    latest_data["mlx_stats"]["max"].append(float(np.max(frame_arr)))
                    latest_data["mlx_stats"]["avg"].append(float(np.mean(frame_arr)))
            except Exception as e:
                logger.debug(f"MLX read error: {e}")
                time.sleep(0.2)
        else:
            time.sleep(DASH_REFRESH_INTERVAL / 1000.0)

# ---------------------------
# Email helpers
# ---------------------------
def generate_thermal_image_bytes(frame):
    buf = io.BytesIO()
    try:
        plt.figure(figsize=(5,4))
        plt.imshow(frame, cmap='inferno')
        plt.colorbar(label='Temp (°C)')
        plt.title('Snapshot at Alert Time')
        plt.axis('off')
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.exception(f"Image generation failed: {e}")
        return None

def send_alert_email_thread(target_email, subject, body, frame):
    def runner():
        if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
            return
        try:
            msg = MIMEMultipart()
            msg['From'] = GMAIL_EMAIL
            msg['To'] = target_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            img_data = generate_thermal_image_bytes(frame)
            if img_data:
                msg.attach(MIMEImage(img_data, name='thermal_snapshot.png'))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
            server.starttls()
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.send_message(msg)
            server.quit()
            logger.info(f"Email sent to {target_email}")
        except Exception as e:
            logger.error(f"Email failed: {e}")

    t = threading.Thread(target=runner, daemon=True)
    t.start()

# ---------------------------
# Dash app
# ---------------------------
app = dash.Dash(__name__)
app.title = "Server Room Monitor"

app.layout = html.Div(style={'fontFamily':'Arial','maxWidth':'1200px','margin':'0 auto'}, children=[
    html.H1("Server Room Monitor (4x Sensor Array)", style={'textAlign':'center'}),
    dcc.Interval(id='interval-component', interval=DASH_REFRESH_INTERVAL, n_intervals=0),
    
    html.Div(style={'backgroundColor':'#f0f0f0','padding':'15px','borderRadius':'10px','marginBottom':'20px'}, children=[
        html.H3("⚙️ Alert Configuration"),
        
        # RadioItems for Single Selection
        html.Div([html.Label("Active Alert Source:"), 
                  dcc.RadioItems(id='alert-source-selector',
                                options=[{'label':' Monitor DHT Sensors (Temp & Hum)','value':'dht'},
                                         {'label':' Monitor Thermal Camera (Surface Temp)','value':'thermal'}],
                                value='thermal', # Default selected
                                inline=True,
                                inputStyle={"margin-right": "5px", "margin-left": "20px"})
                 ]),
        
        html.Div(style={'display':'flex','gap':'20px','flexWrap':'wrap', 'marginTop':'15px'}, children=[
            # DHT Container (Always exists, visibility toggled)
            html.Div(id='dht-settings-container', style={'display':'flex','flex':2,'gap':'20px','borderRight':'2px solid #ccc', 'paddingRight':'10px'}, children=[
                html.Div([html.Label("Max Ambient Temp (°C):"), 
                          dcc.Input(id='input-dht-temp', type='number', value=DEFAULT_DHT_TEMP_THRESHOLD, style={'width':'100%'})], style={'flex':1}),
                html.Div([html.Label("Max Humidity (%):"), 
                          dcc.Input(id='input-dht-hum', type='number', value=DEFAULT_DHT_HUM_THRESHOLD, style={'width':'100%'})], style={'flex':1})
            ]),
            # Thermal Container
            html.Div(id='thermal-settings-container', style={'display':'none','flex':2,'gap':'20px'}, children=[
                html.Div([html.Label("Thermal Trigger Mode:"), dcc.Dropdown(id='thermal-mode-select',
                      options=[{'label':'Max Temp (Hotspot)','value':'max'},{'label':'Avg Temp','value':'avg'}],
                      value='max', clearable=False)], style={'flex':1}),
                html.Div([html.Label("Surface Temp Limit (°C):"), 
                          dcc.Input(id='input-thermal-temp', type='number', value=DEFAULT_THERMAL_TEMP_THRESHOLD, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div([html.Label("Alert Email Address:"), dcc.Input(id='input-email-addr', type='text', placeholder='Press Enter to Apply', debounce=True)], style={'flex':2}),
        ]),
        html.Div(id='alert-status-div', style={'marginTop':'10px','color':'red','fontWeight':'bold'})
    ]),
    
    html.Div(style={'display':'flex','flexWrap':'wrap'}, children=[
        html.Div(style={'flex':'50%','padding':10}, children=[
            html.H3("Live Thermal Feed"), 
            dcc.Checklist(id='view-options', options=[{'label':' Show Values','value':'text'},{'label':' Force Square Pixels','value':'square'}], value=['square'], inline=True), 
            dcc.Graph(id='thermal-heatmap', style={'height':'600px'})
        ]),
        html.Div(style={'flex':'50%','padding':10}, children=[
            html.H3("Thermal History"), 
            dcc.Graph(id='mlx-history-graph', style={'height':'400px'})
        ]),
        html.Div(style={'flex':'50%','padding':10}, children=[
            html.H3("DHT Status (4 Sensors)"), 
            html.Div(id='dht-status-display')
        ]),
        html.Div(style={'flex':'50%','padding':10}, children=[
            html.H3("Environment"), 
            dcc.Graph(id='dht-bar-chart', style={'height':'400px'})
        ]),
    ])
])

# Callback to show/hide settings based on the Radio Selection
@app.callback([Output('dht-settings-container','style'), Output('thermal-settings-container','style')], [Input('alert-source-selector','value')])
def toggle_inputs(selection):
    if selection == 'dht':
        return {'display':'flex','flex':2,'gap':'20px','borderRight':'2px solid #ccc', 'paddingRight':'10px'}, {'display':'none'}
    else: # selection == 'thermal'
        return {'display':'none'}, {'display':'flex','flex':2,'gap':'20px'}

@app.callback([Output('dht-status-display','children'),
               Output('thermal-heatmap','figure'), Output('mlx-history-graph','figure'),
               Output('dht-bar-chart','figure'), Output('alert-status-div','children')],
              [Input('interval-component','n_intervals'),
               Input('alert-source-selector','value'),
               Input('view-options','value'),
               Input('input-dht-temp','value'),
               Input('input-dht-hum','value'),
               Input('input-thermal-temp','value'),
               Input('thermal-mode-select','value'),
               Input('input-email-addr','value')])
def update_dashboard(n, alert_source, view_opts, dht_temp_lim, dht_hum_lim, thermal_lim, thermal_mode, email_addr):
    global last_alert_time
    with data_lock:
        dht = latest_data["dht"].copy()
        frame = latest_data["mlx_frame"].copy()
        stats = {k: list(v) for k,v in latest_data["mlx_stats"].items()}

    alert_msg = ""
    triggers = []
    
    # Flags for email subject line
    has_temp_alert = False
    has_hum_alert = False

    current_time = time.time()

    valid_email = email_addr and "@" in email_addr and "." in email_addr
    if valid_email:
        # 1. Check DHT Alerts (If Selected)
        if alert_source == 'dht' and dht_temp_lim is not None and dht_hum_lim is not None:
            for i in range(1, 5):
                t_val = dht[f't{i}']
                h_val = dht[f'h{i}']
                # Temp Check
                if t_val is not None and t_val > dht_temp_lim: 
                    triggers.append(f"S{i} High Temp: {t_val:.1f}C")
                    has_temp_alert = True
                # Humidity Check
                if h_val is not None and h_val > dht_hum_lim: 
                    triggers.append(f"S{i} High Humidity: {h_val:.1f}%")
                    has_hum_alert = True
        
        # 2. Check Thermal Alerts (If Selected)
        if alert_source == 'thermal' and thermal_lim is not None:
            val = float(np.max(frame)) if thermal_mode == 'max' else float(np.mean(frame))
            if val > thermal_lim: 
                triggers.append(f"Thermal {thermal_mode.upper()}: {val:.1f}C")
                has_temp_alert = True
        
        # 3. Send Email if any triggers exist
        if triggers:
            alert_msg = f"⚠️ Alert: {', '.join(triggers)}"
            if (current_time - last_alert_time) > ALERT_COOLDOWN:
                # Dynamic Subject Line
                if has_temp_alert and has_hum_alert:
                    subject = f"CRITICAL: Temp & Humidity Alert ({len(triggers)} Issues)"
                elif has_hum_alert:
                    subject = f"WARNING: High Humidity Detected ({len(triggers)} Issues)"
                else:
                    subject = f"WARNING: High Temperature Detected ({len(triggers)} Issues)"

                body = "The following limits were breached:\n\n" + "\n".join(triggers)
                send_alert_email_thread(email_addr, subject, body, frame)
                last_alert_time = current_time
                alert_msg += " (Email Sent)"
            else:
                alert_msg += f" (Cooldown: {int(ALERT_COOLDOWN - (current_time - last_alert_time))}s)"
    elif email_addr:
        alert_msg = "⚠️ Invalid Email Address format"

    # Heatmap Logic
    try:
        t_min = float(np.min(frame)); t_max = float(np.max(frame))
    except Exception:
        frame = np.zeros((MLX_HEIGHT, MLX_WIDTH)); t_min, t_max = 0.0, 1.0
    if t_min == t_max: t_max = t_min + 1.0
    text_data = frame.round(0).astype(int) if 'text' in view_opts else None

    heatmap_fig = go.Figure(data=[go.Heatmap(z=frame, zmin=t_min, zmax=t_max, colorscale='Inferno',
                                            text=text_data, texttemplate="%{text}", textfont={"size":10})])
    layout_args = dict(title=f'Max: {t_max:.1f}°C', yaxis=dict(autorange='reversed'))
    if 'square' in view_opts: layout_args['yaxis']['scaleanchor'] = 'x'
    heatmap_fig.update_layout(**layout_args)

    # History Graph
    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('max',[]), name='Max'))
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('avg',[]), name='Avg'))
    history_fig.update_layout(title='Thermal Trends')

    # DHT Bar Chart
    temps = [dht[f't{i}'] or 0 for i in range(1,5)]
    hums = [dht[f'h{i}'] or 0 for i in range(1,5)]
    names = ['S1', 'S2', 'S3', 'S4']
    
    dht_fig = go.Figure(data=[
        go.Bar(name='Temp (°C)', x=names, y=temps),
        go.Bar(name='Humidity (%)', x=names, y=hums)
    ])
    dht_fig.update_layout(title="Sensor Readings")

    # Text Status
    status_lines = []
    for i in range(1, 5):
        t = dht[f't{i}']
        h = dht[f'h{i}']
        s_text = f"Sensor {i}: {t:.1f}°C / {h:.1f}%" if t is not None else f"Sensor {i}: No Data"
        status_lines.append(html.Div(s_text))
    
    return status_lines, heatmap_fig, history_fig, dht_fig, alert_msg

# ---------------------------
# Entrypoint
# ---------------------------
if __name__ == "__main__":
    logger.info("Starting secure_sensor_dashboard...")
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        logger.warning("Email creds missing. Email disabled.")
    
    dht_sensors, mlx = setup_sensors()
    threading.Thread(target=sensor_reading_thread, args=(dht_sensors, mlx), daemon=True).start()
    
    app.run(host='0.0.0.0', port=8050, debug=False, use_reloader=False)
