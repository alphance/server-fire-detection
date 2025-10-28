# server.py
from flask import Flask, request, jsonify
import threading

app = Flask(__name__)

# --- In-memory data store with a lock for thread safety ---
data_lock = threading.Lock()
latest_data = {
    "t1": None, "h1": None,
    "t2": None, "h2": None,
    "thermal_image": None  # Added field for the camera data
}

print("Starting Flask server...")

# --- API Endpoints ---
@app.route('/api/data', methods=['POST'])
def receive_data():
    """Endpoint to receive all sensor data from the detector script."""
    global latest_data
    if not request.json:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    data = request.json
    with data_lock:
        # Update with new data, using .get() for safety
        latest_data["t1"] = data.get("t1")
        latest_data["h1"] = data.get("h1")
        latest_data["t2"] = data.get("t2")
        latest_data["h2"] = data.get("h2")
        latest_data["thermal_image"] = data.get("thermal_image")

    return jsonify({"status": "success", "message": "Data received"}), 200

@app.route('/api/get_data', methods=['GET'])
def get_data():
    """Endpoint for the web dashboard to fetch the latest data."""
    with data_lock:
        return jsonify(latest_data.copy())

# --- Run the App ---
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
