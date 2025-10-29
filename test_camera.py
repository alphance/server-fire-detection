    import time
    import board
    import busio
    import adafruit_mlx90640
    import numpy as np

    # Set up the I2C bus
    # Blinka (via 'board' and 'busio') handles all the low-level Pi 5 details!
    try:
        i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
    except Exception as e:
        print(f"Error initializing I2C: {e}")
        print("Please check your I2C setup and permissions.")
        exit()

    # Initialize the MLX90640 camera
    try:
        mlx = adafruit_mlx90640.MLX90640(i2c)
        print("MLX90640 sensor initialized")
    except Exception as e:
        print(f"Error initializing MLX90640: {e}")
        print("Is the camera wired correctly? Did 'i2cdetect -y 1' show 0x33?")
        exit()

    # Set the refresh rate
    mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
    print(f"Set refresh rate to {mlx.refresh_rate} Hz")

    # Create a frame buffer to store the 768 (32x24) pixel temperatures
    frame = [0] * 768

    print("Starting thermal data stream... Press Ctrl+C to stop.")

    while True:
        try:
            # Get a new frame of temperature data
            mlx.getFrame(frame)

            # Calculate some simple stats
            temp_min = np.min(frame)
            temp_max = np.max(frame)
            temp_avg = np.mean(frame)

            # Print the stats to the console
            print(f"Min: {temp_min:0.2f}C  Max: {temp_max:0.2f}C  Avg: {temp_avg:0.2f}C")
            
        except ValueError:
            # This can happen if the sensor isn't ready. Just skip the frame.
            print("Frame read error, skipping")
            continue
        except KeyboardInterrupt:
            print("\nInterrupted by user. Exiting...")
            break
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            break
    

