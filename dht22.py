import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
import adafruit_dht
import board
import threading
import time

# ---- GPIO SENSOR SETUP ----
# Corrected code using D# BCM naming
#sensor1 = adafruit_dht.DHT22(board.D23)
#sensor2 = adafruit_dht.DHT22(board.D24)

# ---- GPIO SENSOR SETUP ----
# For RPi 5, we must disable pulseio due to hardware/driver incompatibility
sensor1 = adafruit_dht.DHT22(board.D23, use_pulseio=False)
sensor2 = adafruit_dht.DHT22(board.D24, use_pulseio=False)

# ---- GLOBAL SENSOR DATA ----
data_lock = threading.Lock()
latest_data = {"t1": None, "h1": None, "t2": None, "h2": None}

def read_sensors():
    """Background thread to continuously poll DHT22 sensors."""
    while True:
        try:
            t1 = sensor1.temperature
            h1 = sensor1.humidity
        except Exception:
            t1, h1 = None, None

        try:
            t2 = sensor2.temperature
            h2 = sensor2.humidity
        except Exception:
            t2, h2 = None, None

        with data_lock:
            latest_data.update({"t1": t1, "h1": h1, "t2": t2, "h2": h2})

        time.sleep(2)  # DHT22 recommended poll interval
        

threading.Thread(target=read_sensors, daemon=True).start()

# ---- DASH APP SETUP ----
app = dash.Dash(__name__)
app.title = "Dual DHT22 Sensor Dashboard"

app.layout = html.Div(
    style={"fontFamily": "Arial", "textAlign": "center", "padding": "20px"},
    children=[
        html.H1("Dual DHT22 Sensor Dashboard"),
        html.Div(id="sensor1"),
        html.Div(id="sensor2"),
        dcc.Graph(id="live-graph"),
        dcc.Interval(id="interval-component", interval=2000, n_intervals=0),
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

    s1_text = f"Sensor 1 — Temp: {t1:.1f} °C, Humidity: {h1:.1f} %" if t1 else "Sensor 1: waiting..."
    s2_text = f"Sensor 2 — Temp: {t2:.1f} °C, Humidity: {h2:.1f} %" if t2 else "Sensor 2: waiting..."

    temps = [t1 or 0, t2 or 0]
    hums = [h1 or 0, h2 or 0]

    fig = go.Figure(data=[
        go.Bar(name='Temperature (°C)', x=['Sensor 1', 'Sensor 2'], y=temps),
        go.Bar(name='Humidity (%)', x=['Sensor 1', 'Sensor 2'], y=hums)
    ])
    fig.update_layout(barmode='group', title="Current Sensor Readings")

    return s1_text, s2_text, fig

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8050, debug=True)
