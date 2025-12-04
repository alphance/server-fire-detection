import time
import board
import adafruit_dht

# Initialize the sensor on GPIO 23 (Physical Pin 16)
# If you wired it to a different pin, change board.D23 to match!
dht = adafruit_dht.DHT22(board.D23)

print("Listening for DHT22 on GPIO 23...")

while True:
    try:
        # Attempt to read temperature and humidity
        t = dht.temperature
        h = dht.humidity
        
        if t is not None and h is not None:
            print(f"Success! Temp: {t:.1f} C | Humidity: {h:.1f}%")
        else:
            print("Read None (trying again...)")
            
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
