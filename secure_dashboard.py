"""
secure_sensor_dashboard.py

Secure local-only Dash dashboard with Gmail App Password alerts.

Environment variables (via .env or exported in shell):
  GMAIL_EMAIL            (e.g. your.throwaway@gmail.com)
  GMAIL_APP_PASSWORD     (16-character Google App Password)
  ALERT_COOLDOWN         (optional, seconds, default 300)
  DASH_REFRESH_INTERVAL_MS (optional, default 1500)
"""

import os
import logging
import threading
import time
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

# Optional sensor libs (graceful if missing)
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
# Config
# ---------------------------
GMAIL_EMAIL = os.getenv("EMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "300"))
DASH_REFRESH_INTERVAL = int(os.getenv("DASH_REFRESH_INTERVAL_MS", "1500"))

MLX_WIDTH = 32
MLX_HEIGHT = 24
MAX_HISTORY = 200
DHT_POLL_INTERVAL = float(os.getenv("DHT_POLL_INTERVAL", "2.0"))

# DHT pins if board exists
DHT_PIN_1 = getattr(board, "D23", None) if board else None
DHT_PIN_2 = getattr(board, "D24", None) if board else None

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
last_alert_time = 0

# ---------------------------
# Sensor init
# ---------------------------
def setup_sensors():
    dht1 = dht2 = mlx = None
    if adafruit_dht and DHT_PIN_1:
        try:
            dht1 = adafruit_dht.DHT22(DHT_PIN_1, use_pulseio=False)
            logger.info("DHT1 initialized")
        except Exception as e:
            logger.warning(f"DHT1 init failed: {e}")
    if adafruit_dht and DHT_PIN_2:
        try:
            dht2 = adafruit_dht.DHT22(DHT_PIN_2, use_pulseio=False)
            logger.info("DHT2 initialized")
        except Exception as e:
            logger.warning(f"DHT2 init failed: {e}")
    if adafruit_mlx90640 and board:
        try:
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
            mlx = adafruit_mlx90640.MLX90640(i2c)
            if MLX_REFRESH_RATE:
                mlx.refresh_rate = MLX_REFRESH_RATE
            logger.info("MLX initialized")
        except Exception as e:
            logger.warning(f"MLX init failed: {e}")
    return dht1, dht2, mlx

# ---------------------------
# Background sensor reading
# ---------------------------
def sensor_reading_thread(dht1, dht2, mlx):
    global last_dht_read_time
    raw_frame = [0.0] * (MLX_WIDTH * MLX_HEIGHT)
    while True:
        current_time = time.monotonic()

        if (current_time - last_dht_read_time) > DHT_POLL_INTERVAL:
            def read_dht(sensor):
                try:
                    if sensor:
                        return sensor.temperature, sensor.humidity
                except Exception as e:
                    logger.debug(f"DHT read error: {e}")
                return None, None
            t1, h1 = read_dht(dht1)
            t2, h2 = read_dht(dht2)
            with data_lock:
                latest_data["dht"]["t1"] = t1
                latest_data["dht"]["h1"] = h1
                latest_data["dht"]["t2"] = t2
                latest_data["dht"]["h2"] = h2
            last_dht_read_time = current_time

        if mlx:
            try:
                mlx.getFrame(raw_frame)
                frame_arr = np.array(raw_frame).reshape((MLX_HEIGHT, MLX_WIDTH))
                with data_lock:
                    latest_data["mlx_frame"] = frame_arr.copy()
                    latest_data["mlx_stats"]["time"].append(time.time())
                    latest_data["mlx_stats"]["min"].append(float(np.min(frame_arr)))
                    latest_data["mlx_stats"]["max"].append(float(np.max(frame_arr)))
                    latest_data["mlx_stats"]["avg"].append(float(np.mean(frame_arr)))
            except Exception as e:
                logger.debug(f"MLX read error: {e}")
                time.sleep(0.2)
        else:
            time.sleep(DASH_REFRESH_INTERVAL / 1000.0)

# ---------------------------
# Email helpers (Gmail App Password)
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

def send_alert_email_sync(target_email, subject, body, frame):
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        logger.error("GMAIL_EMAIL or GMAIL_APP_PASSWORD not set.")
        return False
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
        server.set_debuglevel(1)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        logger.info(f"Email sent to {target_email}")
        return True
    except Exception as e:
        logger.exception(f"Email send failed: {e}")
        return False

def send_alert_email_thread(target_email, subject, body, frame):
    def runner():
        for attempt in range(1,4):
            ok = send_alert_email_sync(target_email, subject, body, frame)
            if ok:
                return
            sleep_for = 2 ** attempt
            logger.info(f"Email retry in {sleep_for}s (attempt {attempt})")
            time.sleep(sleep_for)
        logger.error("Email failed after retries.")
    t = threading.Thread(target=runner, daemon=True)
    t.start()

# ---------------------------
# Dash app
# ---------------------------
app = dash.Dash(__name__)
app.title = "Sensor Dashboard (Local Only)"

app.layout = html.Div(style={'fontFamily':'Arial','maxWidth':'1200px','margin':'0 auto'}, children=[
    html.H1("Server Room Monitor (Local Only)", style={'textAlign':'center'}),
    dcc.Interval(id='interval-component', interval=DASH_REFRESH_INTERVAL, n_intervals=0),
    html.Div(style={'backgroundColor':'#f0f0f0','padding':'15px','borderRadius':'10px','marginBottom':'20px'}, children=[
        html.H3("⚙️ Alert Configuration"),
        html.Div([html.Label("Active Alert Source:"), dcc.RadioItems(id='alert-source-selector',
                 options=[{'label':' Monitor DHT Sensors','value':'dht'},
                          {'label':' Monitor Thermal Camera','value':'thermal'}],
                 value='thermal', inline=True)]),
        html.Div(style={'display':'flex','gap':'20px','flexWrap':'wrap'}, children=[
            html.Div(id='dht-settings-container', style={'display':'flex','flex':2,'gap':'20px'}, children=[
                html.Div([html.Label("Max Temp Limit (°C):"), dcc.Input(id='input-dht-temp', type='number', value=30, style={'width':'100%'})], style={'flex':1}),
                html.Div([html.Label("Max Humidity Limit (%):"), dcc.Input(id='input-dht-hum', type='number', value=60, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div(id='thermal-settings-container', style={'display':'none','flex':2,'gap':'20px'}, children=[
                html.Div([html.Label("Thermal Trigger Mode:"), dcc.Dropdown(id='thermal-mode-select',
                     options=[{'label':'Trigger on Max Temp (Hotspot)','value':'max'},{'label':'Trigger on Average Temp','value':'avg'}],
                     value='max', clearable=False)], style={'flex':1}),
                html.Div([html.Label("Temperature Limit (°C):"), dcc.Input(id='input-thermal-temp', type='number', value=40, style={'width':'100%'})], style={'flex':1})
            ]),
            html.Div([html.Label("Alert Email Address:"), dcc.Input(id='input-email-addr', type='text', placeholder='Press Enter to Apply', debounce=True)], style={'flex':2}),
        ]),
        html.Div(id='alert-status-div', style={'marginTop':'10px','color':'red','fontWeight':'bold'})
    ]),
    html.Div(style={'display':'flex','flexWrap':'wrap'}, children=[
        html.Div(style={'flex':'50%','padding':10}, children=[html.H3("Live Thermal Feed"), dcc.Checklist(id='view-options',
                 options=[{'label':' Show Values','value':'text'},{'label':' Force Square Pixels','value':'square'}], value=['square'], inline=True), dcc.Graph(id='thermal-heatmap', style={'height':'600px'})]),
        html.Div(style={'flex':'50%','padding':10}, children=[html.H3("Thermal History"), dcc.Graph(id='mlx-history-graph', style={'height':'400px'})]),
        html.Div(style={'flex':'50%','padding':10}, children=[html.H3("DHT Status"), html.Div(id='dht-status-1'), html.Div(id='dht-status-2', style={'marginTop':'5px'})]),
        html.Div(style={'flex':'50%','padding':10}, children=[html.H3("Environment"), dcc.Graph(id='dht-bar-chart', style={'height':'400px'})]),
    ])
])

@app.callback([Output('dht-settings-container','style'), Output('thermal-settings-container','style')], [Input('alert-source-selector','value')])
def toggle_inputs(selection):
    if selection == 'dht':
        return {'display':'flex','flex':2,'gap':'20px'}, {'display':'none'}
    return {'display':'none'}, {'display':'flex','flex':2,'gap':'20px'}

@app.callback([Output('dht-status-1','children'), Output('dht-status-2','children'),
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
    current_time = time.time()

    valid_email = email_addr and "@" in email_addr and "." in email_addr
    if valid_email:
        if alert_source == 'dht' and dht_temp_lim is not None and dht_hum_lim is not None:
            if dht['t1'] is not None and dht['t1'] > dht_temp_lim: triggers.append(f"S1 Temp: {dht['t1']:.1f}C")
            if dht['h1'] is not None and dht['h1'] > dht_hum_lim: triggers.append(f"S1 Hum: {dht['h1']:.1f}%")
            if dht['t2'] is not None and dht['t2'] > dht_temp_lim: triggers.append(f"S2 Temp: {dht['t2']:.1f}C")
            if dht['h2'] is not None and dht['h2'] > dht_hum_lim: triggers.append(f"S2 Hum: {dht['h2']:.1f}%")
        if alert_source == 'thermal' and thermal_lim is not None:
            val = float(np.max(frame)) if thermal_mode == 'max' else float(np.mean(frame))
            if val > thermal_lim: triggers.append(f"Thermal {thermal_mode.upper()}: {val:.1f}C")
        if triggers:
            alert_msg = f"⚠️ Alert: {', '.join(triggers)}"
            if (current_time - last_alert_time) > ALERT_COOLDOWN:
                subject = f"SENSOR ALERT: {len(triggers)} Warnings"
                body = "Limits breached:\n" + "\n".join(triggers)
                send_alert_email_thread(email_addr, subject, body, frame)
                last_alert_time = current_time
                alert_msg += " (Email Sent)"
            else:
                alert_msg += f" (Cooldown: {int(ALERT_COOLDOWN - (current_time - last_alert_time))}s)"
    elif email_addr:
        alert_msg = "⚠️ Invalid Email Address format"

    # build figures
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

    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('max',[]), name='Max'))
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('avg',[]), name='Avg'))
    history_fig.add_trace(go.Scatter(x=stats.get('time',[]), y=stats.get('min',[]), name='Min'))
    history_fig.update_layout(title='Thermal Trends')

    dht_fig = go.Figure(data=[go.Bar(name='Temp', x=['S1','S2'], y=[dht['t1'] or 0, dht['t2'] or 0]),
                              go.Bar(name='Hum', x=['S1','S2'], y=[dht['h1'] or 0, dht['h2'] or 0])])

    s1 = f"S1: {dht['t1']:.1f}°C" if dht['t1'] is not None else "S1: No Data"
    s2 = f"S2: {dht['t2']:.1f}°C" if dht['t2'] is not None else "S2: No Data"

    return s1, s2, heatmap_fig, history_fig, dht_fig, alert_msg

# ---------------------------
# Entrypoint
# ---------------------------
if __name__ == "__main__":
    logger.info("Starting secure_sensor_dashboard...")
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        logger.warning("GMAIL_EMAIL or GMAIL_APP_PASSWORD not set. Email disabled until set.")
    dht1, dht2, mlx = setup_sensors()
    threading.Thread(target=sensor_reading_thread, args=(dht1, dht2, mlx), daemon=True).start()
    app.run(host='127.0.0.1', port=8050, debug=False, use_reloader=False)
