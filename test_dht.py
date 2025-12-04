import time
    import board
    import adafruit_dht

    # Initialize the sensor on GPIO 23
    # If you are using a different pin, change board.D23 to match!
    dht = adafruit_dht.DHT22(board.D23)

    print("Listening for DHT22 on GPIO 23...")

    while True:
        try:
            t = dht.temperature
            h = dht.humidity
            if t is not None and h is not None:
                print(f"Success! Temp: {t:.1f} C | Humidity: {h:.1f}%")
            else:
                print("Read None (trying again...)")
        except RuntimeError as error:
            # DHT sensors fail to read often (timing issues), just keep retrying
            print(f"Retrying: {error.args[0]}")
        except Exception as error:
            dht.exit()
            raise error
        time.sleep(2.0)
    
