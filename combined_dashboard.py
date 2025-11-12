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

# --- Try to import sensor libraries ---
# This allows the script to run even if some libraries aren't installed
# or sensors aren't connected (for testing)

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
DHT_POLL_INTERVAL = 2.0  # Time in seconds between DHT reads
MLX_REFRESH_RATE = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ if adafruit_mlx90640 else None
DASH_REFRESH_INTERVAL = 500 # Milliseconds (ms) for dashboard update (2Hz)

# MLX sensor dimensions
MLX_WIDTH = 32
MLX_HEIGHT = 24

# History graph configuration
MAX_HISTORY = 100  # Number of data points to show on the history graph

# ---- GLOBAL DATA STORE ----
# We use a single lock to protect all shared data
data_lock = threading.Lock()

# Deques are efficient "lists" that automatically pop old items
# when the max length is reached.
latest_data = {
    "dht": {"t1": None, "h1": None, "t2": None, "h2": None},
    "mlx_frame": np.zeros((MLX_HEIGHT, MLX_WIDTH)), # 24x32 array
    "mlx_stats": {
        "time": collections.deque(maxlen=MAX_HISTORY),
        "min": collections.deque(maxlen=MAX_HISTORY),
        "max": collections.deque(maxlen=MAX_HISTORY),
        "avg": collections.deque(maxlen=MAX_HISTORY),
    }
}
# Variable to track DHT polling
last_dht_read_time = 0

# ---- SENSOR INITIALIZATION ----
def setup_sensors():
    """Attempts to initialize all sensors."""
    dht1, dht2, mlx = None, None, None

    # --- Initialize DHT1 ---
    if adafruit_dht:
        try:
            dht1 = adafruit_dht.DHT22(DHT_PIN_1, use_pulseio=False)
            print("DHT Sensor 1 initialized.")
        except Exception as e:
            print(f"WARNING: Failed to initialize DHT Sensor 1: {e}")
    
    # --- Initialize DHT2 ---
    if adafruit_dht:
        try:
            dht2 = adafruit_dht.DHT22(DHT_PIN_2, use_pulseio=False)
            print("DHT Sensor 2 initialized.")
        except Exception as e:
            print(f"WARNING: Failed to initialize DHT Sensor 2: {e}")

    # --- Initialize MLX90640 ---
    if adafruit_mlx90640:
        try:
            # *** FIX 2: Lowered frequency for stability ***
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000) 
            mlx = adafruit_mlx90640.MLX90640(i2c)
            mlx.refresh_rate = MLX_REFRESH_RATE
            print("MLX90640 Thermal Camera initialized.")
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to initialize MLX90640: {e}")
            
    return dht1, dht2, mlx

# ---- BACKGROUND SENSOR THREAD ----
def sensor_reading_thread(dht1, dht2, mlx):
    """
    A single background thread to read all sensors.
    The MLX sensor drives the loop speed, and DHTs are read on an interval.
    """
    global last_dht_read_time
    raw_frame = [0] * (MLX_WIDTH * MLX_HEIGHT) # Buffer for MLX data

    while True:
        current_time = time.monotonic()
        
        # --- 1. Read DHT Sensors (if they exist and interval has passed) ---
        if (current_time - last_dht_read_time) > DHT_POLL_INTERVAL:
            dht_readings = {}
            if dht1:
                try:
                    dht_readings["t1"] = dht1.temperature
                    dht_readings["h1"] = dht1.humidity
                except Exception:
                    dht_readings["t1"], dht_readings["h1"] = None, None
            
            if dht2:
                try:
                    dht_readings["t2"] = dht2.temperature
                    dht_readings["h2"] = dht2.humidity
                except Exception:
                    dht_readings["t2"], dht_readings["h2"] = None, None
            
            # Update global data with lock
            with data_lock:
                latest_data["dht"].update(dht_readings)
            
            last_dht_read_time = current_time

        # --- 2. Read MLX Sensor (if it exists) ---
        if mlx:
            try:
                mlx.getFrame(raw_frame)
                
                # Process the frame
                frame_arr = np.array(raw_frame)
                t_min = np.min(frame_arr)
                t_max = np.max(frame_arr)
                t_avg = np.mean(frame_arr)
                
                # Reshape for heatmap (24 rows, 32 cols)
                frame_2d = frame_arr.reshape((MLX_HEIGHT, MLX_WIDTH))

                # Update global data with lock
                with data_lock:
                    latest_data["mlx_frame"] = frame_2d
                    latest_data["mlx_stats"]["time"].append(current_time)
                    latest_data["mlx_stats"]["min"].append(t_min)
                    latest_data["mlx_stats"]["max"].append(t_max)
                    latest_data["mlx_stats"]["avg"].append(t_avg)

            except ValueError:
                # This can happen if a frame is bad
                print("MLX read error (ValueError), skipping frame.")
                continue
            except Exception as e:
                print(f"Unhandled MLX error: {e}")
                time.sleep(0.5) # Avoid spamming errors

        # If no MLX, we need to sleep to avoid a 100% CPU busy-loop
        if not mlx:
            time.sleep(DASH_REFRESH_INTERVAL / 1000.0)


# ---- DASH APP SETUP ----
app = dash.Dash(__name__)
app.title = "Combined Sensor Dashboard"

app.layout = html.Div(style={'fontFamily': 'Arial'}, children=[
    html.H1("Server Room Sensor Dashboard"),
    
    dcc.Interval(
        id='interval-component',
        interval=DASH_REFRESH_INTERVAL,  # in milliseconds
        n_intervals=0
    ),
    
    # Status text for DHTs
    html.Div([
        html.H3("DHT Sensor Status"),
        html.Div(id='dht-status-1', style={'fontSize': 18}),
        html.Div(id='dht-status-2', style={'fontSize': 18}),
    ], style={'textAlign': 'center', 'marginBottom': 20}),
    
    # Main content area (Graphs)
    html.Div(style={'display': 'flex', 'flexWrap': 'wrap'}, children=[
        
        # Left Column
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Live Thermal Image"),
            dcc.Graph(id='thermal-heatmap'),
        ]),
        
        # Right Column
        html.Div(style={'flex': '50%', 'padding': 10}, children=[
            html.H3("Thermal Sensor History (Min/Max/Avg)"),
            dcc.Graph(id='mlx-history-graph'),
            
            html.H3("Current DHT Readings"),
            dcc.Graph(id='dht-bar-chart'),
        ]),
    ])
])

# ---- DASH CALLBACK ----
@app.callback(
    [Output('dht-status-1', 'children'),
     Output('dht-status-2', 'children'),
     Output('thermal-heatmap', 'figure'),
     Output('mlx-history-graph', 'figure'),
     Output('dht-bar-chart', 'figure')],
    Input('interval-component', 'n_intervals')
)
def update_dashboard(n):
    # --- Get a consistent snapshot of data ---
    with data_lock:
        dht = latest_data["dht"].copy()
        frame = latest_data["mlx_frame"]
        
        # Convert deques to lists for Plotly
        stats = {
            "time": list(latest_data["mlx_stats"]["time"]),
            "min": list(latest_data["mlx_stats"]["min"]),
            "max": list(latest_data["mlx_stats"]["max"]),
            "avg": list(latest_data["mlx_stats"]["avg"]),
        }

    # --- 1. Update DHT Status Text ---
    s1_text = f"Sensor 1 — Temp: {dht['t1']:.1f} °C, Humidity: {dht['h1']:.1f} %" if dht['t1'] is not None else "Sensor 1: Waiting for data..."
    s2_text = f"Sensor 2 — Temp: {dht['t2']:.1f} °C, Humidity: {dht['h2']:.1f} %" if dht['t2'] is not None else "Sensor 2: Waiting for data..."

    # --- 2. Create Thermal Heatmap ---
    # We flip the 'y' axis so it displays correctly
    heatmap_fig = go.Figure(data=go.Heatmap(
        z=frame,
        colorscale='Inferno',
        zmin=np.min(frame), # Auto-scale color range to this frame
        zmax=np.max(frame)
    ))
    heatmap_fig.update_layout(
        title='MLX90640 (Click and drag to zoom)',
        yaxis=dict(autorange='reversed') # Flip Y-axis
    )

    # --- 3. Create MLX History Graph ---
    history_fig = go.Figure()
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['max'], name='Max Temp', mode='lines'))
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['avg'], name='Avg Temp', mode='lines'))
    history_fig.add_trace(go.Scatter(x=stats['time'], y=stats['min'], name='Min Temp', mode='lines'))
    history_fig.update_layout(title='Temperature Trends')

    # --- 4. Create DHT Bar Chart ---
    temps = [dht['t1'] or 0, dht['t2'] or 0]
    hums = [dht['h1'] or 0, dht['h2'] or 0]
    
    dht_fig = go.Figure(data=[
        go.Bar(name='Temperature (°C)', x=['Sensor 1', 'Sensor 2'], y=temps),
        go.Bar(name='Humidity (%)', x=['Sensor 1', 'Sensor 2'], y=hums)
    ])
    dht_fig.update_layout(barmode='group', title="Current Ambient Readings")

    return s1_text, s2_text, heatmap_fig, history_fig, dht_fig


# ---- MAIN EXECUTION ----
if __name__ == "__main__":
    print("--- Setting up sensors ---")
    dht1, dht2, mlx = setup_sensors()
    
    print("--- Starting background sensor thread ---")
    threading.Thread(
        target=sensor_reading_thread, 
        args=(dht1, dht2, mlx), 
        daemon=True
    ).start()
    
    print("--- Starting Dash server on http://0.0.0.0:8050 ---")
    # *** FIX 1: Added use_reloader=False to fix threading issue ***
    app.run(host='0.0.0.0', port=8050, debug=True, use_reloader=False)
