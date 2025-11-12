import adafruit_dht
import board
import time
import threading

# ---- CONFIGURATION (Keep the RPi 5 fix) ----
SENSOR_PIN_1 = board.D23
SENSOR_PIN_2 = board.D24
POLL_INTERVAL = 2  # Seconds

# ---- GPIO SENSOR SETUP ----
try:
    # IMPORTANT: Keep the use_pulseio=False for Raspberry Pi 5 compatibility
    sensor1 = adafruit_dht.DHT22(SENSOR_PIN_1, use_pulseio=False)
    sensor2 = adafruit_dht.DHT22(SENSOR_PIN_2, use_pulseio=False)
    print("Sensors initialized successfully.")
except Exception as e:
    print(f"Error initializing sensors: {e}")
    exit()

# ---- GLOBAL SENSOR DATA ----
data_lock = threading.Lock()
latest_data = {"t1": None, "h1": None, "t2": None, "h2": None}

def read_sensors():
    """Background thread to continuously poll and print DHT22 sensor data."""
    while True:
        try:
            # --- Sensor 1 Read ---
            t1 = sensor1.temperature
            h1 = sensor1.humidity
            print(f"SENSOR 1 - Temp: {t1:.1f} °C, Humidity: {h1:.1f} %")
        except RuntimeError as e:
            # Common error if reading fails, prints the specific error
            t1, h1 = None, None
            print(f"SENSOR 1 READ FAILED: {e}")
        except Exception as e:
            # Catches other non-Runtime errors
            t1, h1 = None, None
            print(f"SENSOR 1 UNEXPECTED ERROR: {e}")

        try:
            # --- Sensor 2 Read ---
            t2 = sensor2.temperature
            h2 = sensor2.humidity
            print(f"SENSOR 2 - Temp: {t2:.1f} °C, Humidity: {h2:.1f} %")
        except RuntimeError as e:
            t2, h2 = None, None
            print(f"SENSOR 2 READ FAILED: {e}")
        except Exception as e:
            t2, h2 = None, None
            print(f"SENSOR 2 UNEXPECTED ERROR: {e}")

        # Update global data (mostly for completeness, not used here)
        with data_lock:
            latest_data.update({"t1": t1, "h1": h1, "t2": t2, "h2": h2})

        print("-" * 30)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    print("Starting sensor reading thread...")
    threading.Thread(target=read_sensors, daemon=True).start()
    
    # Keep the main thread alive indefinitely to allow the daemon thread to run
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Script terminated by user.")
        pass
