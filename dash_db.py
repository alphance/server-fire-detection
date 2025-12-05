"""
secure_dashboard.py

Secure local-only Dash dashboard with SQLite History.
Supports:
- 4 DHT22 Sensors + 1 MLX90640 Thermal Camera
- SQLite Database logging
- History View & Live View Tabs
- Alert Logging
"""

import os
import logging
import threading
import time
import datetime
import io
import collections
import sqlite3
import base64

from dotenv import load_dotenv
load_dotenv()

import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output, State
import plotly.graph_objs as go
import pandas as pd
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
DB_FILE = "sensor_data.db"

# --- DEFAULT THRESHOLDS ---
DEFAULT_DHT_TEMP_THRESHOLD = 45     
DEFAULT_DHT_HUM_MAX_THRESHOLD = 60  
DEFAULT_DHT_HUM_MIN_THRESHOLD = 30  
DEFAULT_THERMAL_TEMP_THRESHOLD = 50 

MLX_WIDTH = 32
MLX_HEIGHT = 24
MAX_HISTORY = 100 
DHT_POLL_INTERVAL = float(os.getenv("DHT_POLL_INTERVAL", "2.0"))

# --- SENSOR PIN CONFIGURATION ---
# UPDATE THIS TO MATCH YOUR LIVE WIRING
DHT_PIN_1 = getattr(board, "D23", None) if board else None
DHT_PIN_2 = getattr(board, "D24", None) if board else None
DHT_PIN_3 = getattr(board, "D17", None) if board else None 
DHT_PIN_4 = getattr(board, "D27", None) if board else None 

# DEMO MODE (All None)
#DHT_PIN_1 = None
#DHT_PIN_2 = None
#DHT_PIN_3 = None
#DHT_PIN_4 = None

MLX_REFRESH_RATE = None
if adafruit_mlx90640 and hasattr(adafruit_mlx90640, "RefreshRate"):
    MLX_REFRESH_RATE = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ

# ---------------------------
# Logging & Database Init
# ---------------------------
LOGFILE = os.getenv("DASH_LOGFILE", "sensor_dashboard.log")
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(),
                              logging.FileHandler(LOGFILE)])
logger = logging.getLogger("sensor_dashboard")

def init_db():
    """Create the tables if they don't exist"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Table for DHT Readings
    c.execute('''CREATE TABLE IF NOT EXISTS dht_readings 
                 (timestamp TEXT, sensor_id INTEGER, temp REAL, humidity REAL)''')
    # Table for Thermal Stats (Storing full video is too heavy, we store stats)
    c.execute('''CREATE TABLE IF NOT EXISTS thermal_stats 
                 (timestamp TEXT, max_temp REAL, avg_temp REAL, min_temp REAL)''')
    # Table for Alerts
    c.execute('''CREATE TABLE IF NOT EXISTS alerts 
                 (timestamp TEXT, alert_type TEXT, message TEXT)''')
    conn.commit()
    conn.close()

init_db()

# ---------------------------
# Global Data
# ---------------------------
data_lock = threading.Lock()

def create_history():
    return {
        "time": collections.deque(maxlen=MAX_HISTORY),
        "temp": collections.deque(maxlen=MAX_HISTORY),
        "hum": collections.deque(maxlen=MAX_HISTORY)
    }

latest_data = {
    "dht": {
        "t1": None, "h1": None,
        "t2": None, "h2": None,
        "t3": None, "h3": None,
        "t4": None, "h4": None
    },
    "dht_history": {
        1: create_history(), 2: create_history(), 3: create_history(), 4: create_history()
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
# DB Helper
# ---------------------------
def log_to_db(timestamp, dht_results, thermal_stats):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Log DHT
        for i, (t, h) in enumerate(dht_results):
            if t is not None:
                c.execute("INSERT INTO dht_readings VALUES (?, ?, ?, ?)", 
                          (timestamp, i+1, t, h))
        
        # Log Thermal
        if thermal_stats:
            c.execute("INSERT INTO thermal_stats VALUES (?, ?, ?, ?)",
                      (timestamp, thermal_stats['max'], thermal_stats['avg'], thermal_stats['min']))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Write Error: {e}")

def log_alert_to_db(alert_type, message):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO alerts VALUES (?, ?, ?)", (timestamp, alert_type, message))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Alert DB Log Error: {e}")

# ---------------------------
# Background sensor reading
# ---------------------------
def sensor_reading_thread(dht_sensors, mlx):
    global last_dht_read_time
    raw_frame = [0.0] * (MLX_WIDTH * MLX_HEIGHT)
    
    while True:
        current_time = time.monotonic()
        db_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
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
            
            # Update Live Data
            with data_lock:
                time_str = datetime.datetime.now().strftime("%H:%M:%S")
                for i in range(4):
                    t, h = results[i]
                    idx = i + 1
                    latest_data["dht"][f"t{idx}"] = t
                    latest_data["dht"][f"h{idx}"] = h
                    if t is not None:
                        latest_data["dht_history"][idx]["time"].append(time_str)
                        latest_data["dht_history"][idx]["temp"].append(t)
                        latest_data["dht_history"][idx]["hum"].append(h)
            
            # Log to Database
            thermal_stats_db = None
            # (We will grab thermal stats in the thermal block, but log DHT now)
            # For simplicity, we just log DHT here.
            log_to_db(db_timestamp, results, None)

            last_dht_read_time = current_time

        # --- READ THERMAL CAMERA ---
        if mlx:
            try:
                mlx.getFrame(raw_frame)
                frame_arr = np.array(raw_frame).reshape((MLX_HEIGHT, MLX_WIDTH))
                
                if np.max(frame_arr) > 150:
                    time.sleep(0.1)
                    continue

                t_stats = {
                    'max': float(np.max(frame_arr)),
                    'avg': float(np.mean(frame_arr)),
                    'min': float(np.min(frame_arr))
                }

                # Log Thermal Stats to DB (separate write for simplicity)
                log_to_db(db_timestamp, [], t_stats)

                with data_lock:
                    latest_data["mlx_frame"] = frame_arr.copy()
                    time_str = datetime.datetime.now().strftime("%H:%M:%S")
                    latest_data["mlx_stats"]["time"].append(time_str)
                    latest_data["mlx_stats"]["min"].append(t_stats['min'])
                    latest_data["mlx_stats"]["max"].append(t_stats['max'])
                    latest_data["mlx_stats"]["avg"].append(t_stats['avg'])
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
        plt.title('Thermal Snapshot')
        plt.axis('off')
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.exception(f"Thermal generation failed: {e}")
        return None

def generate_dht_history_image(sensor_idx, sensor_name):
    buf = io.BytesIO()
    try:
        with data_lock:
            times = list(latest_data["dht_history"][sensor_idx]["time"])
            temps = list(latest_data["dht_history"][sensor_idx]["temp"])
            hums = list(latest_data["dht_history"][sensor_idx]["hum"])
        
        if not times: return None

        plt.figure(figsize=(6,3))
        plt.plot(times, temps, color='red', label='Temp (°C)')
        plt.plot(times, hums, color='blue', label='Humidity (%)')
        plt.title(f'History: {sensor_name}')
        plt.legend()
        plt.grid(True)
        if len(times) > 5:
            plt.xticks([times[0], times[-1]]) 
        
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.exception(f"DHT graph generation failed: {e}")
        return None

def send_alert_email_thread(target_email, subject, body, frame, failed_sensors=None):
    def runner():
        # Log to DB first
        log_alert_to_db("EMAIL_SENT", subject)
        
        if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
            return
        try:
            msg = MIMEMultipart()
            msg['From'] = GMAIL_EMAIL
            msg['To'] = target_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            if frame is not None:
                img_data = generate_thermal_image_bytes(frame)
                if img_data:
                    msg.attach(MIMEImage(img_data, name='thermal_snapshot.png'))
            
            if failed_sensors:
                for idx, name in failed_sensors:
                    dht_img_data = generate_dht_history_image(idx, name)
                    if dht_img_data:
                        filename = f'{name.replace(" ","_")}_history.png'
                        msg.attach(MIMEImage(dht_img_data, name=filename))

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

# --- LAYOUT COMPONENTS ---

live_tab_content = html.Div([
    html.Div(style={'backgroundColor':'#f0f0f0','padding':'15px','borderRadius':'10px','marginBottom':'20px'}, children=[
        html.H3("⚙️ Alert Configuration"),
        html.Div([html.Label("Sensor Labels:"),
            html.Div([
                dcc.Input(id='name-s1', type='text', value='Sensor 1', debounce=True, style={'marginRight':'10px', 'padding':'5px'}),
                dcc.Input(id='name-s2', type='text', value='Sensor 2', debounce=True, style={'marginRight':'10px', 'padding':'5px'}),
                dcc.Input(id='name-s3', type='text', value='Sensor 3', debounce=True, style={'marginRight':'10px', 'padding':'5px'}),
                dcc.Input(id='name-s4', type='text', value='Sensor 4', debounce=True, style={'marginRight':'10px', 'padding':'5px'}),
            ], style={'display':'flex', 'flexWrap':'wrap', 'marginTop':'5px', 'marginBottom':'15px'})
        ]),
        html.Div([html.Label("Active Alert Source:"), 
                  dcc.RadioItems(id='alert-source-selector',
                                options=[{'label':' Monitor DHT Sensors','value':'dht'},
                                         {'label':' Monitor Thermal Camera','value':'thermal'}],
                                value='thermal', inline=True, inputStyle={"margin-right": "5px", "margin-left": "20px"})
                 ]),
        html.Div(style={'display':'flex','gap':'20px','flexWrap':'wrap', 'marginTop':'15px'}, children=[
            html.Div(id='dht-settings-container', style={'display':'flex','flex':3,'gap':'20px','borderRight':'2px solid #ccc', 'paddingRight':'10px'}, children=[
                html.Div([html.Label("Max Exhaust Temp (°C):"), dcc.Input(id='input-dht-temp', type='number', value=DEFAULT_DHT_TEMP_THRESHOLD, style={'width':'100%'})], style={'flex':1}),
                html.Div([html.Label("Min Humidity (%):"), dcc.Input(id='input-dht-hum-min', type='number', value=DEFAULT_DHT_HUM_MIN_THRESHOLD, style={'width':'100%'})], style={'flex':1}),
                html.Div([html.Label("Max Humidity (%):"), dcc.Input(id='input-dht-hum-max', type='number', value=DEFAULT_DHT_HUM_MAX_THRESHOLD, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div(id='thermal-settings-container', style={'display':'none','flex':2,'gap':'20px'}, children=[
                html.Div([html.Label("Thermal Trigger Mode:"), dcc.Dropdown(id='thermal-mode-select', options=[{'label':'Max Temp (Hotspot)','value':'max'},{'label':'Avg Temp','value':'avg'}], value='max', clearable=False)], style={'flex':1}),
                html.Div([html.Label("Surface Hotspot Limit (°C):"), dcc.Input(id='input-thermal-temp', type='number', value=DEFAULT_THERMAL_TEMP_THRESHOLD, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div([html.Label("Alert Email Address:"), dcc.Input(id='input-email-addr', type='text', placeholder='Press Enter to Apply', debounce=True)], style={'flex':2}),
        ]),
        html.Div(id='alert-status-div', style={'marginTop':'10px','color':'red','fontWeight':'bold'})
    ]),
    html.Div(style={'display':'flex','flexWrap':'wrap'}, children=[
        html.Div(style={'flex':'50%','padding':10}, children=[
            html.H3("Thermal Feed"), 
            dcc.Checklist(id='view-options', options=[{'label':' Show Values','value':'text'},{'label':' Force Square Pixels','value':'square'}], value=['square'], inline=True), 
            dcc.Graph(id='thermal-heatmap', style={'height':'500px'}),
            html.H3("Thermal History (Live)", style={'marginTop':'20px'}),
            dcc.Graph(id='mlx-history-graph', style={'height':'300px'})
        ]),
        html.Div(style={'flex':'50%','padding':10}, children=[
            html.H3("DHT Sensors (Temp & Hum History)"), 
            html.Div(style={'display':'flex', 'flexWrap':'wrap'}, children=[
                html.Div([dcc.Graph(id='dht-graph-1', style={'height':'200px'})], style={'width':'50%'}),
                html.Div([dcc.Graph(id='dht-graph-2', style={'height':'200px'})], style={'width':'50%'}),
                html.Div([dcc.Graph(id='dht-graph-3', style={'height':'200px'})], style={'width':'50%'}),
                html.Div([dcc.Graph(id='dht-graph-4', style={'height':'200px'})], style={'width':'50%'})
            ]),
            html.Div(id='dht-status-display', style={'marginTop':'10px', 'fontWeight':'bold'})
        ]),
    ])
])

history_tab_content = html.Div([
    html.H2("Historical Data Browser"),
    html.Div([
        html.Label("Select Date Range:"),
        dcc.DatePickerRange(
            id='history-date-picker',
            min_date_allowed=datetime.date(2024, 1, 1),
            max_date_allowed=datetime.date(2025, 12, 31),
            start_date=datetime.date.today(),
            end_date=datetime.date.today()
        ),
        html.Button("Load Data", id='btn-load-history', style={'marginLeft':'10px', 'height':'40px'}),
    ], style={'marginBottom':'20px'}),
    
    html.H4("Sensor History"),
    dcc.Graph(id='history-long-graph'),
    
    html.H4("Alert Log"),
    dash_table.DataTable(
        id='alert-table',
        columns=[{"name": i, "id": i} for i in ["timestamp", "alert_type", "message"]],
        data=[],
        style_cell={'textAlign': 'left'},
        style_header={'backgroundColor': 'rgb(230, 230, 230)', 'fontWeight': 'bold'}
    )
])

app.layout = html.Div(style={'fontFamily':'Arial','maxWidth':'1200px','margin':'0 auto'}, children=[
    dcc.Tabs([
        dcc.Tab(label='Live Dashboard', children=live_tab_content),
        dcc.Tab(label='Data History', children=history_tab_content),
    ]),
    dcc.Interval(id='interval-component', interval=DASH_REFRESH_INTERVAL, n_intervals=0),
])

# --- CALLBACKS ---

@app.callback([Output('dht-settings-container','style'), Output('thermal-settings-container','style')], [Input('alert-source-selector','value')])
def toggle_inputs(selection):
    if selection == 'dht': return {'display':'flex','flex':3,'gap':'20px','borderRight':'2px solid #ccc', 'paddingRight':'10px'}, {'display':'none'}
    else: return {'display':'none'}, {'display':'flex','flex':2,'gap':'20px'}

# Live Dashboard Update
@app.callback([Output('dht-status-display','children'),
               Output('thermal-heatmap','figure'), Output('mlx-history-graph','figure'),
               Output('dht-graph-1','figure'), Output('dht-graph-2','figure'),
               Output('dht-graph-3','figure'), Output('dht-graph-4','figure'),
               Output('alert-status-div','children')],
              [Input('interval-component','n_intervals'),
               Input('alert-source-selector','value'),
               Input('view-options','value'),
               Input('input-dht-temp','value'),
               Input('input-dht-hum-min','value'),
               Input('input-dht-hum-max','value'),
               Input('input-thermal-temp','value'),
               Input('thermal-mode-select','value'),
               Input('input-email-addr','value'),
               Input('name-s1','value'), Input('name-s2','value'), 
               Input('name-s3','value'), Input('name-s4','value')])
def update_dashboard(n, alert_source, view_opts, dht_temp_lim, dht_hum_min, dht_hum_max, thermal_lim, thermal_mode, email_addr, ns1, ns2, ns3, ns4):
    global last_alert_time
    with data_lock:
        dht = latest_data["dht"].copy()
        dht_hist = {k: {nk: list(nv) for nk, nv in v.items()} for k, v in latest_data["dht_history"].items()}
        frame = latest_data["mlx_frame"].copy()
        stats = {k: list(v) for k,v in latest_data["mlx_stats"].items()}

    sensor_names = {1: ns1 or "S1", 2: ns2 or "S2", 3: ns3 or "S3", 4: ns4 or "S4"}
    alert_msg = ""
    triggers = []
    failed_sensors = [] 
    
    is_dht_temp_alert = False
    is_dht_hum_alert = False
    is_thermal_alert = False
    current_time = time.time()
    valid_email = email_addr and "@" in email_addr and "." in email_addr

    if valid_email:
        if alert_source == 'dht' and dht_temp_lim is not None:
            for i in range(1, 5):
                t_val = dht[f't{i}']
                h_val = dht[f'h{i}']
                s_name = sensor_names[i]
                sensor_failed = False
                if t_val is not None and t_val > dht_temp_lim: 
                    triggers.append(f"{s_name} Exhaust Temp: {t_val:.1f}C")
                    is_dht_temp_alert = True
                    sensor_failed = True
                if h_val is not None and dht_hum_min is not None and dht_hum_max is not None:
                    if h_val < dht_hum_min:
                        triggers.append(f"{s_name} Low Humidity: {h_val:.1f}%")
                        is_dht_hum_alert = True
                        sensor_failed = True
                    elif h_val > dht_hum_max:
                        triggers.append(f"{s_name} High Humidity: {h_val:.1f}%")
                        is_dht_hum_alert = True
                        sensor_failed = True
                if sensor_failed:
                    failed_sensors.append((i, s_name))
        
        if alert_source == 'thermal' and thermal_lim is not None:
            val = float(np.max(frame)) if thermal_mode == 'max' else float(np.mean(frame))
            if val > thermal_lim: 
                triggers.append(f"Thermal {thermal_mode.upper()}: {val:.1f}C")
                is_thermal_alert = True
        
        if triggers:
            alert_msg = f"⚠️ Alert: {', '.join(triggers)}"
            if (current_time - last_alert_time) > ALERT_COOLDOWN:
                subject_parts = []
                if is_dht_temp_alert: subject_parts.append("AIRFLOW OVERHEAT")
                if is_thermal_alert:  subject_parts.append("THERMAL HOTSPOT")
                if is_dht_hum_alert:  subject_parts.append("HUMIDITY OUT OF RANGE")
                if not subject_parts: subject = "SENSOR ALERT"
                else: subject = "CRITICAL: " + " + ".join(subject_parts)
                body = "The following limits were breached:\n\n" + "\n".join(triggers)
                send_alert_email_thread(email_addr, subject, body, frame, failed_sensors)
                last_alert_time = current_time
                alert_msg += " (Email Sent)"
            else:
                alert_msg += f" (Cooldown: {int(ALERT_COOLDOWN - (current_time - last_alert_time))}s)"
    elif email_addr:
        alert_msg = "⚠️ Invalid Email Address format"

    # Heatmap
    try: t_min = float(np.min(frame)); t_max = float(np.max(frame))
    except Exception: frame = np.zeros((MLX_HEIGHT, MLX_WIDTH)); t_min, t_max = 0.0, 1.0
    if t_min == t_max: t_max = t_min + 1.0
    text_data = frame.round(0).astype(int) if 'text' in view_opts else None
    heatmap_fig = go.Figure(data=[go.Heatmap(z=frame, zmin=t_min, zmax=t_max, colorscale='Inferno', text=text_data, texttemplate="%{text}", textfont={"size":10})])
    layout_args = dict(title=f'Max: {t_max:.1f}°C', yaxis=dict(autorange='reversed'))
    if 'square' in view_opts: layout_args['yaxis']['scaleanchor'] = 'x'
    heatmap_fig.update_layout(**layout_args)

    # Thermal History
    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('max',[]), name='Max'))
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('avg',[]), name='Avg'))
    history_fig.update_layout(title='Thermal Trends', margin=dict(l=20, r=20, t=30, b=20))

    # DHT Graphs
    dht_figs = []
    for i in range(1, 5):
        dh = dht_hist[i]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dh['time'], y=dh['temp'], name='Temp', line=dict(color='red')))
        fig.add_trace(go.Scatter(x=dh['time'], y=dh['hum'], name='Hum', line=dict(color='blue')))
        current_t = dht[f't{i}']
        name = sensor_names[i]
        title_str = f"{name}: {current_t:.1f}°C" if current_t else f"{name}"
        fig.update_layout(title=title_str, margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        dht_figs.append(fig)

    status_lines = []
    for i in range(1, 5):
        t = dht[f't{i}']
        h = dht[f'h{i}']
        name = sensor_names[i]
        s_text = f"{name}: {t:.1f}°C / {h:.1f}% | " if t is not None else f"{name}: -- | "
        status_lines.append(html.Span(s_text))
    
    return [html.Div(status_lines)], heatmap_fig, history_fig, dht_figs[0], dht_figs[1], dht_figs[2], dht_figs[3], alert_msg

# History Callback
@app.callback([Output('history-long-graph', 'figure'), Output('alert-table', 'data')],
              [Input('btn-load-history', 'n_clicks')],
              [State('history-date-picker', 'start_date'), State('history-date-picker', 'end_date')])
def update_history_tab(n, start, end):
    if n is None: return go.Figure(), []
    
    conn = sqlite3.connect(DB_FILE)
    
    # 1. Fetch Sensor Data
    query = f"SELECT timestamp, sensor_id, temp, humidity FROM dht_readings WHERE timestamp BETWEEN '{start}' AND '{end} 23:59:59'"
    df = pd.read_sql_query(query, conn)
    
    # 2. Fetch Alerts
    alert_query = f"SELECT timestamp, alert_type, message FROM alerts WHERE timestamp BETWEEN '{start}' AND '{end} 23:59:59' ORDER BY timestamp DESC"
    alerts_df = pd.read_sql_query(alert_query, conn)
    
    conn.close()
    
    # Build Graph
    fig = go.Figure()
    if not df.empty:
        for i in range(1, 5):
            sensor_data = df[df['sensor_id'] == i]
            if not sensor_data.empty:
                fig.add_trace(go.Scatter(x=sensor_data['timestamp'], y=sensor_data['temp'], name=f'S{i} Temp'))
                # Optional: Add Humidity traces here if desired
    
    fig.update_layout(title="Historical Temperature Data")
    
    return fig, alerts_df.to_dict('records')

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
