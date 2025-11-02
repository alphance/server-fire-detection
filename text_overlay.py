import time
import board
import busio
import adafruit_mlx90640
import numpy as np
import pygame
import os

# --- Pygame Setup ---
pygame.init()
pygame.font.init()

SENSOR_WIDTH = 32
SENSOR_HEIGHT = 24
SCALE_FACTOR = 20
SCREEN_WIDTH = SENSOR_WIDTH * SCALE_FACTOR
SCREEN_HEIGHT = SENSOR_HEIGHT * SCALE_FACTOR

# --- Font and Text Setup ---
try:
    FONT_SIZE = 18
    font = pygame.font.SysFont('monospace', FONT_SIZE, bold=True)
except Exception as e:
    print(f"Warning: Could not load monospace font. Using default. Error: {e}")
    font = pygame.font.Font(None, FONT_SIZE)

TEXT_WHITE = (255, 255, 255)
TEXT_BLACK = (0, 0, 0)
# --------------------

# Create the display
try:
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("MLX90640 Thermal Camera Feed (with Text)")
    print("Pygame window created. Displaying visual feed with text overlay.")
except pygame.error as e:
    print(f"Error initializing pygame display: {e}")
    exit()

thermal_surface = pygame.Surface((SENSOR_WIDTH, SENSOR_HEIGHT))
# --------------------

# --- MLX90640 Sensor Setup ---
try:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
    # --- THIS IS THE FIX ---
    # Corrected the typo from MLX9G90640 to MLX90640
    mlx = adafruit_mlx90640.MLX90640(i2c)
    mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
    frame = [0] * (SENSOR_WIDTH * SENSOR_HEIGHT)
    print("MLX90640 sensor initialized. Starting feed...")
except Exception as e:
    print(f"Error initializing MLX90640: {e}")
    pygame.font.quit()
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

    # --- Clear the entire screen with black before drawing anything
    screen.fill((0, 0, 0))
    # ----------------------

    # --- Draw the thermal image (as background) ---
    temp_min_frame = np.min(frame)
    temp_max_frame = np.max(frame)

    # Lock the surface ONE time
    thermal_surface.lock()
    for y in range(SENSOR_HEIGHT):
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            # Normalize temp for color mapping
            norm = (temp - temp_min_frame) / (temp_max_frame - temp_min_frame + 0.001)
            norm = max(0.0, min(1.0, norm))
            red = int(255 * norm)
            green = 0
            blue = int(255 * (1 - norm))
            thermal_surface.set_at((x, y), (red, green, blue))
    
    # Unlock the surface ONE time, after ALL loops are done
    thermal_surface.unlock() # <-- MOVED THIS LINE OUTSIDE THE 'for y' LOOP

    # Scale the 32x24 surface up to our big 640x480 screen
    scaled_surface = pygame.transform.scale(thermal_surface, (SCREEN_WIDTH, SCREEN_HEIGHT))
    # Draw the scaled image to the screen
    screen.blit(scaled_surface, (0, 0))

    # --- Draw the text overlay ---
    for y in range(SENSOR_HEIGHT):
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            
            # --- Dynamic Text Color ---
            norm = (temp - temp_min_frame) / (temp_max_frame - temp_min_frame + 0.001)
            text_color = TEXT_BLACK if norm > 0.6 else TEXT_WHITE
            
            # Create the text string (rounded to integer)
            text_str = f"{temp:0.0f}"
            
            # Render the text
            text_surf = font.render(text_str, True, text_color)
            
            # Get the text rectangle and center it in the 20x20 box
            text_rect = text_surf.get_rect()
            text_rect.center = (x * SCALE_FACTOR + (SCALE_FACTOR // 2), 
                                y * SCALE_FACTOR + (SCALE_FACTOR // 2))
            
            # Draw the text onto the screen
            screen.blit(text_surf, text_rect)

    # Update the full display
    pygame.display.flip()

# --- End of Loop ---
pygame.font.quit()
pygame.quit()
print("Pygame window closed. Exiting.")

