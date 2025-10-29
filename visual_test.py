import time
import board
import busio
import adafruit_mlx90640
import numpy as np
import pygame

# --- Pygame Setup ---
pygame.init()

# Screen dimensions
SENSOR_WIDTH = 32
SENSOR_HEIGHT = 24

# Scale up the 32x24 sensor output to a larger window
# We can't use 640x480 as it's not a direct multiple.
# 32 * 20 = 640
# 24 * 20 = 480
# So a 20x scale factor works perfectly.
SCALE_FACTOR = 20
SCREEN_WIDTH = SENSOR_WIDTH * SCALE_FACTOR
SCREEN_HEIGHT = SENSOR_HEIGHT * SCALE_FACTOR

# Create the display
try:
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("MLX90640 Thermal Camera Feed")
    print(f"Pygame window created: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
except pygame.error as e:
    print(f"Error initializing pygame display: {e}")
    print("Are you running this from a graphical desktop (not just SSH)?")
    exit()

# This surface will hold the 32x24 thermal image
thermal_surface = pygame.Surface((SENSOR_WIDTH, SENSOR_HEIGHT))
# --------------------


# --- Color Mapping Function ---
# These are the min/max temps we expect. You can change them.
# A narrower range will give more color contrast.
TEMP_MIN_C = 20  # Coolest temp (blue)
TEMP_MAX_C = 35  # Hottest temp (red)

def temp_to_color(temp):
    """Maps a temperature (in C) to an RGB color."""
    # Normalize temp to 0.0 - 1.0
    norm = (temp - TEMP_MIN_C) / (TEMP_MAX_C - TEMP_MIN_C)
    norm = max(0.0, min(1.0, norm))  # Clamp value between 0.0 and 1.0

    # Simple Blue -> Red colormap
    red = int(255 * norm)
    green = 0
    blue = int(255 * (1 - norm))

    return (red, green, blue)
# ----------------------------


# --- MLX90640 Sensor Setup ---
try:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
    mlx = adafruit_mlx90640.MLX90640(i2c)
    mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
    frame = [0] * (SENSOR_WIDTH * SENSOR_HEIGHT)
    print("MLX90640 sensor initialized")
except Exception as e:
    print(f"Error initializing MLX90640: {e}")
    print("Is the camera wired correctly? Did 'i2cdetect -y 1' show 0x33?")
    pygame.quit()
    exit()
# ---------------------------


# --- Main Loop ---
running = True
while running:
    # Check for Pygame events (like closing the window)
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            running = False

    try:
        # Get a new frame of temperature data
        mlx.getFrame(frame)
    except ValueError:
        print("Frame read error, skipping")
        continue

    # --- Draw the thermal image ---
    # Find the min and max temp in *this* frame for auto-scaling
    # This gives much better contrast than a fixed range.
    temp_min_frame = np.min(frame)
    temp_max_frame = np.max(frame)
    print(f"Frame Temps: Min={temp_min_frame:0.2f}C Max={temp_max_frame:0.2f}C")

    # Lock the surface so we can draw to it
    thermal_surface.lock()
    for y in range(SENSOR_HEIGHT):
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            
            # Normalize temp based on this frame's min/max
            norm = (temp - temp_min_frame) / (temp_max_frame - temp_min_frame + 0.001) # +0.001 to avoid div by zero
            norm = max(0.0, min(1.0, norm))

            # Simple Blue -> Red colormap
            red = int(255 * norm)
            green = 0
            blue = int(255 * (1 - norm))
            
            # Set the pixel on our small 32x24 surface
            thermal_surface.set_at((x, y), (red, green, blue))
    thermal_surface.unlock()

    # --- Scale and display ---
    # Scale the 32x24 surface up to our big screen size
    # This uses "nearest neighbor" scaling for a cool, blocky, pixelated look
    scaled_surface = pygame.transform.scale(thermal_surface, (SCREEN_WIDTH, SCREEN_HEIGHT))
    
    # (Optional: Use this line instead for a blurry, "smooth" look)
    # scaled_surface = pygame.transform.smoothscale(thermal_surface, (SCREEN_WIDTH, SCREEN_HEIGHT))

    # Draw the scaled image to the screen
    screen.blit(scaled_surface, (0, 0))
    
    # Update the display
    pygame.display.flip()

# --- End of Loop ---
pygame.quit()
print("Pygame window closed. Exiting.")
