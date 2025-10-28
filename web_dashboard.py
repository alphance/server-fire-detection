import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
import serial
import re
import threading
import time

# ---- SERIAL SETUP ----
COM_PORT = '/dev/ttyUSB0'  # adjust this to your actual port (check with `ls /dev/tty*`)
BAUD_RATE = 9600
pattern = re.compile(r'S1,([0-9.]+),([0-9.]+);S2,([0-9.]+),([0-9.]+)')

try:
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    print(f"Opened serial port {COM_PORT} at {BAUD_RATE} baud.")
except serial.SerialException as e:
    print(f"Error opening serial port: {e}")
    ser = None

# ---- GLOBAL SENSOR DATA ----
data_lock = threading.Lock()
latest_data = {"t1": None, "h1": None, "t2": None, "h2": None}

def read_serial():
    """Background thread to constantly read serial data."""
    while ser:
        try:
            line = ser.readline().decode('utf-8').strip()
            m = pattern.match(line)
            if m:
                t1, h1, t2, h2 = map(float, m.groups())
                with data_lock:
                    latest_data.update({"t1": t1, "h1": h1, "t2": t2, "h2": h2})
        except Exception:
            continue

threading.Thread(target=read_serial, daemon=True).start()

# ---- DASH APP SETUP ----
app = dash.Dash(__name__)
app.title = "Sensor Dashboard"

app.layout = html.Div(
    style={"fontFamily": "Arial", "textAlign": "center", "padding": "20px"},
    children=[
        html.H1("Dual DHT22 Sensor Dashboard"),
        html.Div(id="sensor1"),
        html.Div(id="sensor2"),
        dcc.Graph(id="live-graph"),
        dcc.Interval(id="interval-component", interval=1000, n_intervals=0),
    ]
)

@app.callback(
    [Output("sensor1", "children"),
     Output("sensor2", "children"),
     Output("live-graph", "figure")],
    Input("interval-component", "n_intervals")
)
def update_dashboard(n):
    with data_lock:
        t1 = latest_data["t1"]
        h1 = latest_data["h1"]
        t2 = latest_data["t2"]
        h2 = latest_data["h2"]

    # Show current readings
    s1_text = f"Sensor 1 — Temp: {t1:.1f} °C, Humidity: {h1:.1f} %" if t1 else "Sensor 1: waiting..."
    s2_text = f"Sensor 2 — Temp: {t2:.1f} °C, Humidity: {h2:.1f} %" if t2 else "Sensor 2: waiting..."

    # Plot basic comparison bars
    temps = [t1 or 0, t2 or 0]
    hums = [h1 or 0, h2 or 0]

    fig = go.Figure(data=[
        go.Bar(name='Temperature (°C)', x=['Sensor 1', 'Sensor 2'], y=temps),
        go.Bar(name='Humidity (%)', x=['Sensor 1', 'Sensor 2'], y=hums)
    ])
    fig.update_layout(barmode='group', title="Current Sensor Readings")

    return s1_text, s2_text, fig

if __name__ == "__main__":
    # host='0.0.0.0' lets it be accessible to other devices on your LAN
    app.run_server(host='0.0.0.0', port=8050, debug=True)
