import time
import board
import adafruit_dht

# --- CONFIGURATION ---
# CHANGE THIS if you move the wire! (e.g., board.D17, board.D27, board.D23)
# GPIO 23 is Physical Pin 16.
PIN_TO_TEST = board.D23
# ---------------------

# Initialize the sensor with use_pulseio=False (REQUIRED for Pi 5)
try:
    dht = adafruit_dht.DHT22(PIN_TO_TEST, use_pulseio=False)
    print(f"Listening for DHT22 on {PIN_TO_TEST}...")
except Exception as e:
    print(f"Error initializing sensor: {e}")
    print("Check if the pin is already in use or if permissions are denied.")
    exit()

while True:
    try:
        # Attempt to read temperature and humidity
        t = dht.temperature
        h = dht.humidity
        
        if t is not None and h is not None:
            print(f"Success! Temp: {t:.1f} C | Humidity: {h:.1f}%")
        else:
            print("Read None (sensor active, but no data yet...)")
            
    except RuntimeError as error:
        # DHT sensors are tricky and fail to read often (checksum/timing errors)
        # This is normal behavior for these sensors; we just print the error and try again.
        print(f"Retrying: {error.args[0]}")
        
    except Exception as error:
        # This catches fatal errors (like permissions or completely disconnected pins)
        dht.exit()
        raise error

    # Wait 2 seconds before next read (DHT22 requires at least 2s refresh rate)
    time.sleep(2.0)
