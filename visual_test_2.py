import time
import board
import busio
import adafruit_mlx90640
import numpy as np
import pygame
import os # We'll use this to clear the terminal

# --- Pygame Setup ---
pygame.init()

SENSOR_WIDTH = 32
SENSOR_HEIGHT = 24
SCALE_FACTOR = 20
SCREEN_WIDTH = SENSOR_WIDTH * SCALE_FACTOR
SCREEN_HEIGHT = SENSOR_HEIGHT * SCALE_FACTOR

try:
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("MLX90640 Thermal Camera Feed")
    print("Pygame window created. Check your terminal for raw data.")
except pygame.error as e:
    print(f"Error initializing pygame display: {e}")
    exit()

thermal_surface = pygame.Surface((SENSOR_WIDTH, SENSOR_HEIGHT))
# --------------------

# --- MLX90640 Sensor Setup ---
try:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
    mlx = adafruit_mlx90640.MLX90640(i2c)
    mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
    frame = [0] * (SENSOR_WIDTH * SENSOR_HEIGHT)
    print("MLX90640 sensor initialized")
except Exception as e:
    print(f"Error initializing MLX90640: {e}")
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
        mlx.getFrame(frame)
    except ValueError:
        continue # skip this frame

    # --- Draw the thermal image (in Pygame window) ---
    temp_min_frame = np.min(frame)
    temp_max_frame = np.max(frame)

    thermal_surface.lock()
    for y in range(SENSOR_HEIGHT):
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            norm = (temp - temp_min_frame) / (temp_max_frame - temp_min_frame + 0.001)
            norm = max(0.0, min(1.0, norm))
            red = int(255 * norm)
            green = 0
            blue = int(255 * (1 - norm))
            thermal_surface.set_at((x, y), (red, green, blue))
    thermal_surface.unlock()

    scaled_surface = pygame.transform.scale(thermal_surface, (SCREEN_WIDTH, SCREEN_HEIGHT))
    screen.blit(scaled_surface, (0, 0))
    pygame.display.flip()
    
    # --- NEW: Print the raw data (in Terminal window) ---
    
    # This ANSI escape code clears the terminal screen
    # \033[H moves cursor to top-left, \033[J clears from cursor down
    print("\033[H\033[J", end="")
    
    print("--- 32x24 Raw Thermal Array (Degrees C) ---")
    output_str = ""
    for y in range(SENSOR_HEIGHT):
        row = []
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            # Format to 4 chars total, 1 decimal place (e.g., " 23.4" or "-10.2")
            row.append(f"{temp: 4.1f}") 
        output_str += " | ".join(row) + "\n"
        
    print(output_str)
    print("-" * (32 * 7)) # Separator line
    print(f"Frame Min: {temp_min_frame:0.2f}C   Max: {temp_max_frame:0.2f}C   Refresh: {mlx.refresh_rate}Hz")
    print("Press ESC or close the Pygame window to stop.")
    
    # A short delay so the terminal is readable and we don't overwhelm it
    time.sleep(0.01)

# --- End of Loop ---
pygame.quit()
print("Pygame window closed. Exiting.")

