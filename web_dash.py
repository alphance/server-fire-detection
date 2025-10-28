# web_dashboard.py
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
import requests

# ---- App Configuration ----
DASHBOARD_REFRESH_INTERVAL_MS = 1000  # 1 second
SERVER_DATA_URL = "http://127.0.0.1:5000/api/get_data"

# ---- Dash App Setup ----
app = dash.Dash(__name__)
app.title = "Thermal & Environmental Dashboard"

app.layout = html.Div(
    style={"fontFamily": "Arial, sans-serif", "textAlign": "center", "padding": "20px", "backgroundColor": "#f0f0f0"},
    children=[
        html.H1("🔥 Server Room Environmental Monitor", style={"color": "#333"}),
        html.Div(
            style={"display": "flex", "justifyContent": "center", "gap": "40px", "padding": "20px"},
            children=[
                html.Div(id="sensor1-display", style={"fontSize": "24px", "fontWeight": "bold"}),
                html.Div(id="sensor2-display", style={"fontSize": "24px", "fontWeight": "bold"}),
            ]
        ),
        dcc.Graph(id="live-bar-chart"),
        html.Hr(),
        dcc.Graph(id="thermal-heatmap", style={"height": "60vh"}),
        dcc.Interval(
            id="interval-component",
            interval=DASHBOARD_REFRESH_INTERVAL_MS,
            n_intervals=0
        ),
    ]
)

@app.callback(
    [Output("sensor1-display", "children"),
     Output("sensor2-display", "children"),
     Output("live-bar-chart", "figure"),
     Output("thermal-heatmap", "figure")],
    Input("interval-component", "n_intervals")
)
def update_dashboard(n):
    # Fetch all data from the Flask server
    try:
        response = requests.get(SERVER_DATA_URL, timeout=0.5)
        data = response.json()
        t1, h1, t2, h2 = data.get("t1"), data.get("h1"), data.get("t2"), data.get("h2")
        thermal_image = data.get("thermal_image")
    except (requests.exceptions.RequestException, ValueError):
        t1, h1, t2, h2, thermal_image = None, None, None, None, None

    # --- Create Text Displays ---
    s1_text = f"Sensor 1: {t1:.1f}°C | {h1:.1f}%" if t1 is not None else "Sensor 1: Awaiting data..."
    s2_text = f"Sensor 2: {t2:.1f}°C | {h2:.1f}%" if t2 is not None else "Sensor 2: Awaiting data..."

    # --- Create Bar Chart Figure ---
    temps = [t1 or 0, t2 or 0]
    hums = [h1 or 0, h2 or 0]
    bar_fig = go.Figure(data=[
        go.Bar(name='Temperature (°C)', x=['Sensor 1', 'Sensor 2'], y=temps, marker_color='crimson'),
        go.Bar(name='Humidity (%)', x=['Sensor 1', 'Sensor 2'], y=hums, marker_color='royalblue')
    ])
    bar_fig.update_layout(title="Ambient Sensor Readings", plot_bgcolor='white')

    # --- Create Thermal Heatmap Figure ---
    if thermal_image:
        heatmap_fig = go.Figure(data=go.Heatmap(
            z=thermal_image,
            colorscale='inferno',
            zmin=20,  # Set a reasonable min temp for server rooms
            zmax=60   # Set a reasonable max temp to highlight hotspots
        ))
        heatmap_fig.update_layout(
            title='Live Thermal Camera Feed',
            xaxis_title='X Axis',
            yaxis_title='Y Axis',
        )
    else:
        # Create a placeholder if no data is available
        heatmap_fig = go.Figure()
        heatmap_fig.update_layout(
            title='Live Thermal Camera Feed',
            annotations=[dict(text="Awaiting thermal data...", showarrow=False)],
            xaxis={'visible': False},
            yaxis={'visible': False}
        )

    return s1_text, s2_text, bar_fig, heatmap_fig

if __name__ == "__main__":
    app.run_server(host='0.0.0.0', port=8050, debug=True)
