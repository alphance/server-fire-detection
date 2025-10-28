# detector_main.py
import time
import requests
import Adafruit_DHT
import board
import busio
import adafruit_mlx90640
import numpy as np

# --- Configuration ---
# DHT Sensor Config
DHT_SENSOR_TYPE = Adafruit_DHT.DHT22
SENSOR_1_PIN = 4   # GPIO pin for the first DHT22 sensor
SENSOR_2_PIN = 17  # GPIO pin for the second DHT22 sensor

# Flask Server Config
SERVER_URL = "http://127.0.0.1:5000/api/data"

# Timing Config
READ_INTERVAL = 2 # seconds

# --- I2C and Thermal Camera Setup ---
try:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
    mlx = adafruit_mlx90640.MLX90640(i2c)
    mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
    print("MLX90640 thermal camera initialized successfully.")
except Exception as e:
    print(f"Error initializing thermal camera: {e}")
    mlx = None

# --- Main Loop ---
print("Starting sensor reading script...")

while True:
    try:
        # --- Read DHT Sensors ---
        h1, t1 = Adafruit_DHT.read_retry(DHT_SENSOR_TYPE, SENSOR_1_PIN)
        h2, t2 = Adafruit_DHT.read_retry(DHT_SENSOR_TYPE, SENSOR_2_PIN)

        # --- Read Thermal Camera ---
        thermal_image_data = None
        if mlx:
            try:
                # Create a buffer for the frame data
                frame = [0] * 768
                mlx.getFrame(frame)
                
                # Reshape into a 24x32 numpy array and flip it
                thermal_image_np = np.array(frame).reshape((24, 32))
                thermal_image_np = np.flipud(thermal_image_np) # Flip vertically
                
                # Convert numpy array to a standard Python list for JSON serialization
                thermal_image_data = np.round(thermal_image_np, 2).tolist()
            except Exception as e:
                print(f"Could not read from thermal camera: {e}")
                
        # --- Prepare Data Payload ---
        data = {
            "t1": round(t1, 2) if t1 is not None else None,
            "h1": round(h1, 2) if h1 is not None else None,
            "t2": round(t2, 2) if t2 is not None else None,
            "h2": round(h2, 2) if h2 is not None else None,
            "thermal_image": thermal_image_data
        }

        # --- Send Data to Server ---
        try:
            requests.post(SERVER_URL, json=data, timeout=5)
            print(f"Successfully sent data: t1={data['t1']}, t2={data['t2']}, thermal_image_shape={np.array(thermal_image_data).shape if thermal_image_data else 'None'}")
        except requests.exceptions.RequestException as e:
            print(f"Error sending data to server: {e}")

    except Exception as e:
        print(f"An unexpected error occurred in the main loop: {e}")

    time.sleep(READ_INTERVAL)
