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

# --- REMOVED THE INTERMEDIATE SURFACES ---
# thermal_surface = pygame.Surface((SENSOR_WIDTH, SENSOR_HEIGHT))
# --------------------

# --- MLX90640 Sensor Setup ---
try:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
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

    # --- REBUILT THE DRAWING LOOP ---
    # This new loop calculates colors, draws the color box,
    # and draws the text all in one pass.
    
    temp_min_frame = np.min(frame)
    temp_max_frame = np.max(frame)

    for y in range(SENSOR_HEIGHT):
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            
            # --- 1. Calculate Color ---
            norm = (temp - temp_min_frame) / (temp_max_frame - temp_min_frame + 0.001)
            norm = max(0.0, min(1.0, norm))
            red = int(255 * norm)
            green = 0
            blue = int(255 * (1 - norm))
            color = (red, green, blue)
            
            # --- 2. Draw the 20x20 Color Box ---
            # We draw this rectangle directly to the screen
            pygame.draw.rect(screen, color, 
                             (x * SCALE_FACTOR, y * SCALE_FACTOR, 
                              SCALE_FACTOR, SCALE_FACTOR))

            # --- 3. Draw the Text Overlay ---
            
            # --- Dynamic Text Color ---
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

