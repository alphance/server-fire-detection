"""
secure_dashboard.py

Secure local-only Dash dashboard with SQLite History, Aggregation & Replay.
Features:
- Live Dashboard (4 DHTs + Thermal)
- History "Replay" (View past thermal images)
- Data Aggregation (Raw, 1 min, 5 min, 10 min, Daily)
- Time & Date Filtering
- Side-by-Side Replay UI with Full Stats
- GLOBAL CONFIG: Settings stored in DB, synced to all users.
- ADMIN MODE: Only Admin can edit names/thresholds.
- FIX: Thermal Feed Options (Values/Square Pixels) restored.
- FIX: Auto-Refresh added to History Tab.
"""

import os
import logging
import threading
import time
import datetime
import io
import collections
import sqlite3
import json 
import zlib

from dotenv import load_dotenv
load_dotenv()

import dash
from dash import dcc, html, dash_table, ctx, no_update
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
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "capstone2025") 
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "300"))
DASH_REFRESH_INTERVAL = int(os.getenv("DASH_REFRESH_INTERVAL_MS", "1500"))
CONFIG_SYNC_INTERVAL = 5000 
DB_FILE = "sensor_data.db"
DB_LOG_INTERVAL = 2.0  

MLX_WIDTH = 32
MLX_HEIGHT = 24
MAX_HISTORY = 100 
DHT_POLL_INTERVAL = float(os.getenv("DHT_POLL_INTERVAL", "2.0"))

# --- SENSOR PIN CONFIGURATION ---
# OPTION A: LIVE MODE
DHT_PIN_1 = getattr(board, "D23", None) if board else None
DHT_PIN_2 = getattr(board, "D24", None) if board else None
DHT_PIN_3 = getattr(board, "D17", None) if board else None 
DHT_PIN_4 = getattr(board, "D27", None) if board else None 

# OPTION B: DEMO MODE
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
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS dht_readings (timestamp TEXT, sensor_id INTEGER, temp REAL, humidity REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS thermal_data (timestamp TEXT, max_temp REAL, avg_temp REAL, min_temp REAL, raw_frame BLOB)''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (timestamp TEXT, alert_type TEXT, message TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    
    defaults = {
        'name-s1': 'Sensor 1', 'name-s2': 'Sensor 2', 'name-s3': 'Sensor 3', 'name-s4': 'Sensor 4',
        'dht-temp': '45', 'dht-hum-min': '30', 'dht-hum-max': '60',
        'thermal-temp': '50', 'thermal-mode': 'max', 'alert-email': ''
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        
    conn.commit()
    conn.close()

init_db()

# ---------------------------
# Global Data & DB Helpers
# ---------------------------
data_lock = threading.Lock()

def get_global_config():
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT key, value FROM settings")
        rows = c.fetchall()
        conn.close()
        return {k: v for k, v in rows}
    except:
        return {}

def update_global_config(new_config):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for k, v in new_config.items():
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Config Save Error: {e}")
        return False

config_cache = get_global_config()

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
# DB Logging Helper
# ---------------------------
def log_to_db(timestamp, dht_results, thermal_stats, raw_frame_arr=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        if dht_results:
            for i, (t, h) in enumerate(dht_results):
                if t is not None:
                    c.execute("INSERT INTO dht_readings VALUES (?, ?, ?, ?)", (timestamp, i+1, t, h))
        if thermal_stats and raw_frame_arr is not None:
            frame_json = json.dumps(raw_frame_arr.tolist()).encode('utf-8')
            compressed_frame = zlib.compress(frame_json)
            c.execute("INSERT INTO thermal_data VALUES (?, ?, ?, ?, ?)",
                      (timestamp, thermal_stats['max'], thermal_stats['avg'], thermal_stats['min'], compressed_frame))
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
    global last_dht_read_time, config_cache
    raw_frame = [0.0] * (MLX_WIDTH * MLX_HEIGHT)
    last_db_log_time = 0
    last_config_sync = 0
    latest_dht_results = [(None, None)] * 4
    
    while True:
        current_time = time.monotonic()
        db_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Sync config every 5 seconds
        if (current_time - last_config_sync) > 5.0:
            config_cache = get_global_config()
            last_config_sync = current_time

        if (current_time - last_dht_read_time) > DHT_POLL_INTERVAL:
            def read_dht(sensor):
                try:
                    if sensor: return sensor.temperature, sensor.humidity
                except Exception as e: logger.debug(f"DHT error: {e}")
                return None, None
            
            results = []
            for s in dht_sensors: results.append(read_dht(s))
            latest_dht_results = results
            
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
            last_dht_read_time = current_time

        thermal_stats_for_db = None
        thermal_frame_for_db = None
        
        if mlx:
            try:
                mlx.getFrame(raw_frame)
                frame_arr = np.array(raw_frame).reshape((MLX_HEIGHT, MLX_WIDTH))
                if np.max(frame_arr) > 150:
                    time.sleep(0.1)
                    continue
                thermal_stats_for_db = {'max': float(np.max(frame_arr)), 'avg': float(np.mean(frame_arr)), 'min': float(np.min(frame_arr))}
                thermal_frame_for_db = frame_arr
                with data_lock:
                    latest_data["mlx_frame"] = frame_arr.copy()
                    time_str = datetime.datetime.now().strftime("%H:%M:%S")
                    latest_data["mlx_stats"]["time"].append(time_str)
                    latest_data["mlx_stats"]["min"].append(thermal_stats_for_db['min'])
                    latest_data["mlx_stats"]["max"].append(thermal_stats_for_db['max'])
                    latest_data["mlx_stats"]["avg"].append(thermal_stats_for_db['avg'])
            except Exception as e:
                logger.debug(f"MLX error: {e}")
                time.sleep(0.2)
        
        if (current_time - last_db_log_time) > DB_LOG_INTERVAL:
            log_to_db(db_timestamp, latest_dht_results, thermal_stats_for_db, thermal_frame_for_db)
            last_db_log_time = current_time

        if not mlx: time.sleep(DASH_REFRESH_INTERVAL / 1000.0)

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
    except Exception as e: return None

def generate_dht_history_image(sensor_idx, sensor_name):
    buf = io.BytesIO()
    try:
        with data_lock:
            times = list(latest_data["dht_history"][sensor_idx]["time"])
            temps = list(latest_data["dht_history"][sensor_idx]["temp"])
            hums = list(latest_data["dht_history"][sensor_idx]["hum"])
        if not times: return None
        plt.figure(figsize=(6,3))
        plt.plot(times, temps, color='red', label='Temp')
        plt.plot(times, hums, color='blue', label='Hum')
        plt.title(f'History: {sensor_name}')
        plt.legend()
        plt.grid(True)
        if len(times) > 5: plt.xticks([times[0], times[-1]]) 
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.read()
    except Exception as e: return None

def send_alert_email_thread(target_email, subject, body, frame, failed_sensors=None):
    def runner():
        log_alert_to_db("EMAIL_SENT", subject)
        if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD: return
        try:
            msg = MIMEMultipart()
            msg['From'] = GMAIL_EMAIL
            msg['To'] = target_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            if frame is not None:
                img_data = generate_thermal_image_bytes(frame)
                if img_data: msg.attach(MIMEImage(img_data, name='thermal_snapshot.png'))
            if failed_sensors:
                for idx, name in failed_sensors:
                    dht_img_data = generate_dht_history_image(idx, name)
                    if dht_img_data: msg.attach(MIMEImage(dht_img_data, name=f'{name.replace(" ","_")}_history.png'))
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
            server.starttls()
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.send_message(msg)
            server.quit()
            logger.info(f"Email sent to {target_email}")
        except Exception as e: logger.error(f"Email failed: {e}")
    t = threading.Thread(target=runner, daemon=True)
    t.start()

# ---------------------------
# Dash app & Layout
# ---------------------------
app = dash.Dash(__name__)
app.title = "Server Room Monitor"

live_tab_content = html.Div([
    dcc.Store(id='session-store', storage_type='session'),
    dcc.Interval(id='config-sync-interval', interval=CONFIG_SYNC_INTERVAL),

    html.Div(style={'backgroundColor':'#f0f0f0','padding':'15px','borderRadius':'10px','marginBottom':'20px'}, children=[
        
        html.Div([
            html.H3("⚙️ Alert Configuration", style={'display':'inline-block'}),
            html.Div([
                dcc.Input(id='admin-pass', type='password', placeholder='Admin Pass', style={'marginRight':'5px'}),
                html.Button('Login', id='btn-login', n_clicks=0),
                html.Div(id='login-status', style={'display':'inline-block', 'marginLeft':'10px', 'fontWeight':'bold'})
            ], style={'float':'right'})
        ], style={'marginBottom':'15px', 'overflow':'hidden'}),

        html.Div([html.Label("Sensor Labels:"),
            html.Div([
                dcc.Input(id='name-s1', type='text', disabled=True, style={'marginRight':'10px', 'padding':'5px'}),
                dcc.Input(id='name-s2', type='text', disabled=True, style={'marginRight':'10px', 'padding':'5px'}),
                dcc.Input(id='name-s3', type='text', disabled=True, style={'marginRight':'10px', 'padding':'5px'}),
                dcc.Input(id='name-s4', type='text', disabled=True, style={'marginRight':'10px', 'padding':'5px'}),
            ], style={'display':'flex', 'flexWrap':'wrap', 'marginTop':'5px', 'marginBottom':'15px'})
        ]),
        
        html.Div([html.Label("Active Alert Source:"), 
                  dcc.RadioItems(id='alert-source-selector',
                                options=[{'label':' Monitor DHT Sensors','value':'dht'},{'label':' Monitor Thermal Camera','value':'thermal'}],
                                value='thermal', inline=True, inputStyle={"margin-right": "5px", "margin-left": "20px"})
                 ]),
        
        html.Div(style={'display':'flex','gap':'20px','flexWrap':'wrap', 'marginTop':'15px'}, children=[
            html.Div(id='dht-settings-container', style={'display':'flex','flex':3,'gap':'20px','borderRight':'2px solid #ccc', 'paddingRight':'10px'}, children=[
                html.Div([html.Label("Max Exhaust Temp (°C):"), dcc.Input(id='input-dht-temp', type='number', disabled=True, style={'width':'100%'})], style={'flex':1}),
                html.Div([html.Label("Min Humidity (%):"), dcc.Input(id='input-dht-hum-min', type='number', disabled=True, style={'width':'100%'})], style={'flex':1}),
                html.Div([html.Label("Max Humidity (%):"), dcc.Input(id='input-dht-hum-max', type='number', disabled=True, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div(id='thermal-settings-container', style={'display':'none','flex':2,'gap':'20px'}, children=[
                html.Div([html.Label("Thermal Trigger Mode:"), dcc.Dropdown(id='thermal-mode-select', options=[{'label':'Max Temp (Hotspot)','value':'max'},{'label':'Avg Temp','value':'avg'}], value='max', clearable=False, disabled=True)], style={'flex':1}),
                html.Div([html.Label("Surface Hotspot Limit (°C):"), dcc.Input(id='input-thermal-temp', type='number', disabled=True, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div([html.Label("Alert Email Address:"), dcc.Input(id='input-email-addr', type='text', disabled=True, placeholder='Email...', style={'flex':2})]),
        ]),
        
        html.Div([
            html.Button('SAVE GLOBAL SETTINGS', id='btn-save-config', style={'backgroundColor':'#d9534f', 'color':'white', 'fontWeight':'bold', 'width':'100%', 'marginTop':'15px', 'display':'none'}),
            html.Div(id='save-status', style={'textAlign':'center', 'marginTop':'5px'})
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
        html.Div([
            html.Label("1. Select Date Range:", style={'fontWeight':'bold'}),
            dcc.DatePickerRange(
                id='history-date-picker',
                min_date_allowed=datetime.date(2024, 1, 1),
                max_date_allowed=datetime.date(2025, 12, 31),
                start_date=datetime.date.today(),
                end_date=datetime.date.today()
            ),
        ], style={'display':'inline-block', 'marginRight':'20px', 'verticalAlign':'top'}),
        
        html.Div([
            html.Label("2. Filter Time (HH:MM):", style={'fontWeight':'bold'}),
            html.Div([
                dcc.Input(id='time-start', type='text', value='00:00', style={'width':'60px', 'marginRight':'5px'}),
                html.Span(" to "),
                dcc.Input(id='time-end', type='text', value='23:59', style={'width':'60px', 'marginLeft':'5px'})
            ])
        ], style={'display':'inline-block', 'marginRight':'20px', 'verticalAlign':'top'}),

        html.Div([
            html.Label("3. View Mode:", style={'fontWeight':'bold'}),
            dcc.RadioItems(
                id='history-interval-select',
                options=[
                    {'label': ' Raw', 'value': 'raw'}, {'label': ' 1 Min', 'value': '1T'}, {'label': ' 5 Min', 'value': '5T'}, {'label': ' 10 Min', 'value': '10T'}, {'label': ' Daily', 'value': 'D'}
                ],
                value='raw', inline=True
            )
        ], style={'display':'inline-block', 'marginRight':'20px', 'verticalAlign':'top'}),

        html.Div([
            html.Label("4. Visible Sensors:", style={'fontWeight':'bold'}),
            dcc.Checklist(
                id='history-column-select',
                options=[{'label': ' Thermal', 'value': 'mlx'}, {'label': ' S1', 'value': 's1'}, {'label': ' S2', 'value': 's2'}, {'label': ' S3', 'value': 's3'}, {'label': ' S4', 'value': 's4'}],
                value=['mlx', 's1', 's2', 's3', 's4'], inline=True
            )
        ], style={'display':'inline-block', 'marginRight':'20px', 'verticalAlign':'top'}),

        html.Div([
            html.Label("5. Filters:", style={'fontWeight':'bold'}),
            dcc.Checklist(id='history-filter-select', options=[{'label': ' Exceeded Only', 'value': 'exceeded'}], value=[], inline=True)
        ], style={'display':'inline-block', 'marginRight':'20px', 'verticalAlign':'top'}),

        # Auto-Refresh Toggle
        html.Div([
            html.Label("6. Auto-Refresh:", style={'fontWeight':'bold'}),
            dcc.Checklist(id='history-auto-refresh', options=[{'label': ' Enabled', 'value': 'enabled'}], value=[], inline=True)
        ], style={'display':'inline-block', 'marginRight':'20px', 'verticalAlign':'top'}),

        html.Button("Load Data", id='btn-load-history', style={'height':'40px', 'backgroundColor':'#007BFF', 'color':'white', 'border':'none', 'padding':'0 20px', 'cursor':'pointer', 'verticalAlign':'top'}),
    ], style={'backgroundColor':'#e9ecef', 'padding':'15px', 'borderRadius':'5px', 'marginBottom':'20px'}),
    
    dcc.Interval(id='history-interval', interval=2000), 

    html.Div([
        html.H4("Master Data Log", style={'color':'#555'}),
        dash_table.DataTable(
            id='master-table', columns=[], data=[], page_size=12, row_selectable='single', sort_action='native',
            style_cell={'textAlign': 'center', 'minWidth': '50px', 'fontSize':'12px'},
            style_header={'backgroundColor': 'rgb(230, 230, 230)', 'fontWeight': 'bold'},
            style_data_conditional=[]
        ),
    ]),
    
    html.Div(style={'display':'flex', 'marginTop':'20px', 'borderTop':'2px solid #ccc', 'paddingTop':'20px'}, children=[
        html.Div(style={'flex':1}, children=[dcc.Graph(id='replay-heatmap', style={'height':'400px'})]),
        html.Div(style={'flex':1, 'paddingLeft':'20px', 'backgroundColor':'#f9f9f9'}, children=[html.H3("Snapshot Details"), html.Div(id='replay-info-panel')])
    ])
])

app.layout = html.Div(style={'fontFamily':'Arial','maxWidth':'1200px','margin':'0 auto'}, children=[
    dcc.Tabs([dcc.Tab(label='Live Dashboard', children=live_tab_content), dcc.Tab(label='Data History & Replay', children=history_tab_content)]),
    dcc.Interval(id='interval-component', interval=DASH_REFRESH_INTERVAL, n_intervals=0),
])

# --- CALLBACKS ---

# 1. Login Logic
@app.callback([Output('session-store', 'data'), Output('login-status', 'children'),
               Output('name-s1', 'disabled'), Output('name-s2', 'disabled'), Output('name-s3', 'disabled'), Output('name-s4', 'disabled'),
               Output('input-dht-temp', 'disabled'), Output('input-dht-hum-min', 'disabled'), Output('input-dht-hum-max', 'disabled'),
               Output('input-thermal-temp', 'disabled'), Output('thermal-mode-select', 'disabled'), Output('input-email-addr', 'disabled'),
               Output('btn-save-config', 'style')],
              [Input('btn-login', 'n_clicks')],
              [State('admin-pass', 'value'), State('session-store', 'data')])
def admin_login(n, password, session_data):
    is_admin = session_data.get('is_admin', False) if session_data else False
    if n > 0 and password == ADMIN_PASSWORD: is_admin = True
    disabled = not is_admin
    save_btn_style = {'backgroundColor':'#d9534f', 'color':'white', 'fontWeight':'bold', 'width':'100%', 'marginTop':'15px'}
    if not is_admin: save_btn_style['display'] = 'none'
    msg = "Logged In (Admin)" if is_admin else ""
    return {'is_admin': is_admin}, msg, disabled, disabled, disabled, disabled, disabled, disabled, disabled, disabled, disabled, disabled, save_btn_style

# 2. Config Sync
@app.callback([Output('name-s1', 'value'), Output('name-s2', 'value'), Output('name-s3', 'value'), Output('name-s4', 'value'),
               Output('input-dht-temp', 'value'), Output('input-dht-hum-min', 'value'), Output('input-dht-hum-max', 'value'),
               Output('input-thermal-temp', 'value'), Output('thermal-mode-select', 'value'), Output('input-email-addr', 'value'),
               Output('save-status', 'children')],
              [Input('config-sync-interval', 'n_intervals'), Input('btn-save-config', 'n_clicks')],
              [State('name-s1', 'value'), State('name-s2', 'value'), State('name-s3', 'value'), State('name-s4', 'value'),
               State('input-dht-temp', 'value'), State('input-dht-hum-min', 'value'), State('input-dht-hum-max', 'value'),
               State('input-thermal-temp', 'value'), State('thermal-mode-select', 'value'), State('input-email-addr', 'value')])
def sync_config(n_interval, n_save, ns1, ns2, ns3, ns4, dht_t, dht_hmin, dht_hmax, therm_t, therm_mode, email):
    ctx_id = ctx.triggered_id
    if ctx_id == 'btn-save-config':
        new_conf = {'name-s1': ns1, 'name-s2': ns2, 'name-s3': ns3, 'name-s4': ns4, 'dht-temp': dht_t, 'dht-hum-min': dht_hmin, 'dht-hum-max': dht_hmax, 'thermal-temp': therm_t, 'thermal-mode': therm_mode, 'alert-email': email}
        update_global_config(new_conf)
        global config_cache
        config_cache = new_conf
        return ns1, ns2, ns3, ns4, dht_t, dht_hmin, dht_hmax, therm_t, therm_mode, email, "Settings Saved Globally!"
    conf = get_global_config()
    return (conf.get('name-s1'), conf.get('name-s2'), conf.get('name-s3'), conf.get('name-s4'),
            conf.get('dht-temp'), conf.get('dht-hum-min'), conf.get('dht-hum-max'),
            conf.get('thermal-temp'), conf.get('thermal-mode'), conf.get('alert-email'), "")

# 3. Toggle View Inputs
@app.callback([Output('dht-settings-container','style'), Output('thermal-settings-container','style')], [Input('alert-source-selector','value')])
def toggle_view(selection):
    if selection == 'dht': return {'display':'flex','flex':3,'gap':'20px','borderRight':'2px solid #ccc', 'paddingRight':'10px'}, {'display':'none'}
    else: return {'display':'none'}, {'display':'flex','flex':2,'gap':'20px'}

# 4. Update Live View
@app.callback([Output('dht-status-display','children'),
               Output('thermal-heatmap','figure'), Output('mlx-history-graph','figure'),
               Output('dht-graph-1','figure'), Output('dht-graph-2','figure'),
               Output('dht-graph-3','figure'), Output('dht-graph-4','figure'),
               Output('alert-status-div','children')],
              [Input('interval-component','n_intervals'), Input('view-options', 'value')],
              [State('name-s1', 'value'), State('name-s2', 'value'), State('name-s3', 'value'), State('name-s4', 'value')])
def update_live_view(n, view_opts, ns1, ns2, ns3, ns4):
    global last_alert_time, config_cache
    if view_opts is None: view_opts = []
    
    dht_temp_lim = float(config_cache.get('dht-temp', 100))
    dht_hum_min = float(config_cache.get('dht-hum-min', 0))
    dht_hum_max = float(config_cache.get('dht-hum-max', 100))
    thermal_lim = float(config_cache.get('thermal-temp', 100))
    thermal_mode = config_cache.get('thermal-mode', 'max')
    email_addr = config_cache.get('alert-email', '')

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

    for i in range(1, 5):
        t_val = dht[f't{i}']
        h_val = dht[f'h{i}']
        s_name = sensor_names[i]
        sensor_failed = False
        if t_val is not None and t_val > dht_temp_lim: 
            triggers.append(f"{s_name} Exhaust Temp: {t_val:.1f}C")
            is_dht_temp_alert = True
            sensor_failed = True
        if h_val is not None and (h_val < dht_hum_min or h_val > dht_hum_max):
            is_dht_hum_alert = True
            sensor_failed = True
            if h_val < dht_hum_min: triggers.append(f"{s_name} Low Humidity: {h_val:.1f}%")
            else: triggers.append(f"{s_name} High Humidity: {h_val:.1f}%")
        if sensor_failed:
            failed_sensors.append((i, s_name))
    
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
            subject = "CRITICAL: " + " + ".join(subject_parts) if subject_parts else "SENSOR ALERT"
            body = "Limits breached:\n\n" + "\n".join(triggers)
            send_alert_email_thread(email_addr, subject, body, frame, failed_sensors)
            last_alert_time = current_time
            alert_msg += " (Email Sent)"
        else:
            alert_msg += f" (Cooldown: {int(ALERT_COOLDOWN - (current_time - last_alert_time))}s)"

    # --- PLOTS ---
    try: t_min = float(np.min(frame)); t_max = float(np.max(frame))
    except: frame = np.zeros((MLX_HEIGHT, MLX_WIDTH)); t_min, t_max = 0.0, 1.0
    if t_min == t_max: t_max = t_min + 1.0
    
    # Text Data for Heatmap
    text_data = frame.round(0).astype(int) if 'text' in view_opts else None
    
    heatmap_fig = go.Figure(data=[go.Heatmap(z=frame, zmin=t_min, zmax=t_max, colorscale='Inferno', 
                                             text=text_data, texttemplate="%{text}", textfont={"size":10})])
    layout_args = dict(title=f'Max: {t_max:.1f}°C', yaxis=dict(autorange='reversed'))
    if 'square' in view_opts: layout_args['yaxis']['scaleanchor'] = 'x'
    heatmap_fig.update_layout(**layout_args)

    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('max',[]), name='Max'))
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('avg',[]), name='Avg'))
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('min',[]), name='Min'))
    history_fig.update_layout(title='Thermal Trends', margin=dict(l=20, r=20, t=30, b=20))

    dht_figs = []
    for i in range(1, 5):
        dh = dht_hist[i]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dh['time'], y=dh['temp'], name='Temp', line=dict(color='red')))
        fig.add_trace(go.Scatter(x=dh['time'], y=dh['hum'], name='Hum', line=dict(color='blue')))
        current_t = dht[f't{i}']
        fig.update_layout(title=f"{sensor_names[i]}: {current_t:.1f}°C" if current_t else sensor_names[i], margin=dict(l=20, r=20, t=30, b=20))
        dht_figs.append(fig)

    status_lines = []
    for i in range(1, 5):
        t, h = dht[f't{i}'], dht[f'h{i}']
        status_lines.append(html.Span(f"{sensor_names[i]}: {t:.1f}°C / {h:.1f}% | " if t else f"{sensor_names[i]}: -- | "))
    
    return [html.Div(status_lines)], heatmap_fig, history_fig, dht_figs[0], dht_figs[1], dht_figs[2], dht_figs[3], alert_msg

@app.callback([Output('master-table', 'data'), Output('master-table', 'selected_rows'), 
               Output('master-table', 'style_data_conditional'), Output('master-table', 'columns')],
              [Input('btn-load-history', 'n_clicks'), Input('history-interval', 'n_intervals')],
              [State('history-date-picker', 'start_date'), State('history-date-picker', 'end_date'),
               State('history-interval-select', 'value'), State('history-filter-select', 'value'),
               State('history-column-select', 'value'), State('time-start', 'value'), State('time-end', 'value'),
               State('history-auto-refresh', 'value'),
               State('name-s1','value'), State('name-s2','value'), 
               State('name-s3','value'), State('name-s4','value')])
def load_history_data(n_click, n_interval, start, end, interval, filter_opts, visible_sensors, time_start, time_end, auto_refresh, ns1, ns2, ns3, ns4):
    trigger = ctx.triggered_id
    if trigger == 'history-interval' and 'enabled' not in auto_refresh:
        return no_update 
    
    if n_click is None and n_interval is None: return [], [], [], []
    
    conn = sqlite3.connect(DB_FILE)
    start_ts = f"{start} {time_start}:00"
    end_ts = f"{end} {time_end}:59"
    df_thermal = pd.read_sql_query(f"SELECT timestamp, max_temp, avg_temp, min_temp FROM thermal_data WHERE timestamp BETWEEN '{start_ts}' AND '{end_ts}' ORDER BY timestamp DESC", conn)
    df_dht = pd.read_sql_query(f"SELECT timestamp, sensor_id, temp, humidity FROM dht_readings WHERE timestamp BETWEEN '{start_ts}' AND '{end_ts}' ORDER BY timestamp DESC", conn)
    conn.close()
    
    if df_thermal.empty: return [], [], [], []
    if not df_dht.empty:
        df_dht['sensor_lbl'] = 'S' + df_dht['sensor_id'].astype(str)
        df_dht_pivot = df_dht.pivot_table(index='timestamp', columns='sensor_lbl', values=['temp', 'humidity'], aggfunc='first')
        df_dht_pivot.columns = [f"{col[1]}_{col[0]}" for col in df_dht_pivot.columns]
        df_dht_pivot.reset_index(inplace=True)
        df_final = pd.merge(df_thermal, df_dht_pivot, on='timestamp', how='left')
    else: df_final = df_thermal

    if interval != 'raw':
        df_final['timestamp'] = pd.to_datetime(df_final['timestamp'])
        df_final.set_index('timestamp', inplace=True)
        df_agg = df_final.resample(interval).mean().dropna(how='all')
        df_agg.reset_index(inplace=True)
        if interval == 'D': df_agg['timestamp'] = df_agg['timestamp'].dt.strftime("%Y-%m-%d")
        else: df_agg['timestamp'] = df_agg['timestamp'].dt.strftime("%Y-%m-%d %H:%M:%S")
        df_final = df_agg

    # Filters using cached config
    d_lim, h_min, h_max, t_lim = float(config_cache.get('dht-temp',100)), float(config_cache.get('dht-hum-min',0)), float(config_cache.get('dht-hum-max',100)), float(config_cache.get('thermal-temp',100))
    t_mode = config_cache.get('thermal-mode', 'max')

    styles = [{'if': {'row_index': 'odd'}, 'backgroundColor': 'rgb(248, 248, 248)'}]
    if t_lim:
        c = 'max_temp' if t_mode == 'max' else 'avg_temp'
        styles.append({'if': {'filter_query': f'{{{c}}} > {t_lim}', 'column_id': c}, 'color': 'red', 'fontWeight': 'bold'})
    if d_lim:
        for c in [x for x in df_final.columns if 'temp' in x and x.startswith('S')]:
            styles.append({'if': {'filter_query': f'{{{c}}} > {d_lim}', 'column_id': c}, 'color': 'red', 'fontWeight': 'bold'})
    if h_min is not None and h_max is not None:
        for c in [x for x in df_final.columns if 'humidity' in x and x.startswith('S')]:
            styles.append({'if': {'filter_query': f'{{{c}}} < {h_min} || {{{c}}} > {h_max}', 'column_id': c}, 'color': 'red', 'fontWeight': 'bold'})

    if 'exceeded' in filter_opts:
        mask = pd.Series(False, index=df_final.index)
        if t_lim: mask |= (df_final['max_temp' if t_mode == 'max' else 'avg_temp'] > t_lim)
        if d_lim:
            for c in [x for x in df_final.columns if 'temp' in x and x.startswith('S')]: mask |= (df_final[c].notnull() & (df_final[c] > d_lim))
        if h_min is not None:
            for c in [x for x in df_final.columns if 'humidity' in x and x.startswith('S')]: mask |= (df_final[c].notnull() & ((df_final[c] < h_min) | (df_final[c] > h_max)))
        df_final = df_final[mask]

    columns = [{"name": "Timestamp", "id": "timestamp"}]
    if 'mlx' in visible_sensors: columns.extend([{"name": "MLX Max", "id": "max_temp"}, {"name": "MLX Avg", "id": "avg_temp"}, {"name": "MLX Min", "id": "min_temp"}])
    def add_cols(code, inp, def_n):
        if code in visible_sensors:
            lbl = inp or def_n
            columns.extend([{"name": f"{lbl} Temp", "id": f"{def_n}_temp"}, {"name": f"{lbl} Hum", "id": f"{def_n}_humidity"}])
    add_cols('s1', ns1, 'S1'); add_cols('s2', ns2, 'S2'); add_cols('s3', ns3, 'S3'); add_cols('s4', ns4, 'S4')

    return df_final.round(1).to_dict('records'), [], styles, columns

@app.callback([Output('replay-heatmap', 'figure'), Output('replay-info-panel', 'children')],
              [Input('master-table', 'selected_rows')], [State('master-table', 'data')])
def replay_snapshot(selected_rows, data):
    if not selected_rows or not data: return go.Figure(), "Select a row."
    ts = data[selected_rows[0]]['timestamp']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT raw_frame, timestamp FROM thermal_data WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT 1", (ts,))
    res = c.fetchone()
    conn.close()
    if res and res[0]:
        try:
            arr = np.array(json.loads(zlib.decompress(res[0]).decode('utf-8')))
            fig = go.Figure(data=[go.Heatmap(z=arr, colorscale='Inferno')])
            fig.update_layout(title="Snapshot", margin=dict(l=20, r=20, t=30, b=20), yaxis=dict(autorange='reversed'))
            return fig, [html.B("Selected: "), ts, html.Br(), html.B("Actual: "), res[1], html.Br(), 
                         html.B(f"Max: {np.max(arr):.1f}°C"), html.Br(),
                         html.B(f"Avg: {np.mean(arr):.1f}°C"), html.Br(),
                         html.B(f"Min: {np.min(arr):.1f}°C")]
        except: pass
    return go.Figure(), "No Data"

if __name__ == "__main__":
    logger.info("Starting secure_sensor_dashboard...")
    dht_sensors, mlx = setup_sensors()
    threading.Thread(target=sensor_reading_thread, args=(dht_sensors, mlx), daemon=True).start()
    app.run(host='0.0.0.0', port=8050, debug=False, use_reloader=False)
