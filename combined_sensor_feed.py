import time
import board
import busio
import adafruit_mlx90640
import adafruit_dht # Import DHT library
import numpy as np
import pygame
import os

# --- DHT Sensor Setup (Copy from dht_test.py) ---
# IMPORTANT: Keep the use_pulseio=False for Raspberry Pi 5 compatibility
SENSOR_PIN_1 = board.D23
SENSOR_PIN_2 = board.D24
DHT_POLL_INTERVAL = 2  # Read DHT sensors every 2 seconds

try:
    sensor1 = adafruit_dht.DHT22(SENSOR_PIN_1, use_pulseio=False)
    sensor2 = adafruit_dht.DHT22(SENSOR_PIN_2, use_pulseio=False)
    print("DHT Sensors initialized successfully.")
except Exception as e:
    print(f"Error initializing DHT sensors: {e}")
    # Don't exit, just continue without DHT data
    sensor1, sensor2 = None, None 

# Variables to store the latest DHT readings
dht_data = {"t1": None, "h1": None, "t2": None, "h2": None}
last_dht_read_time = 0 
# ---------------------------------------------

# --- Pygame Setup ---
pygame.init()
pygame.font.init()

# --- MLX90640 Configuration ---
SENSOR_WIDTH = 32
SENSOR_HEIGHT = 24
SCALE_FACTOR = 20
SCREEN_WIDTH = SENSOR_WIDTH * SCALE_FACTOR
SCREEN_HEIGHT = SENSOR_HEIGHT * SCALE_FACTOR + 40 # Add space for text overlay

# --- Font and Text Setup ---
FONT_SIZE = 18
font = pygame.font.Font(None, FONT_SIZE)
TEXT_WHITE = (255, 255, 255)
TEXT_BLACK = (0, 0, 0)
TEXT_YELLOW = (255, 255, 0) # For status text

# Create the display
try:
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Combined Sensor Feed")
    print("Pygame window created.")
except pygame.error as e:
    print(f"Error initializing pygame display: {e}")
    exit()

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

# --- Heatmap Color Function (No Change) ---
def get_heatmap_color(norm_temp):
    """Maps a normalized value (0.0 to 1.0) to a BGYR heatmap color."""
    norm_temp = max(0.0, min(1.0, norm_temp))
    r, g, b = 0, 0, 0
    
    if norm_temp < 0.25:
        g = int(255 * (norm_temp / 0.25))
        b = int(255 * (1.0 - (norm_temp / 0.25)))
    elif norm_temp < 0.5:
        r = int(255 * ((norm_temp - 0.25) / 0.25))
        g = 255
    elif norm_temp < 0.75:
        r = 255
        g = int(255 * (1.0 - ((norm_temp - 0.5) / 0.25)))
    else:
        r = 255
    return (r, g, b)
# ----------------------------------------

# --- NEW FUNCTION: Read DHT Data ---
def read_dht_sensors(current_time):
    global last_dht_read_time
    
    if (current_time - last_dht_read_time) < DHT_POLL_INTERVAL:
        return
    
    # Attempt to read sensors
    try:
        dht_data["t1"] = sensor1.temperature
        dht_data["h1"] = sensor1.humidity
    except RuntimeError as e:
        print(f"DHT1 read error: {e}")
        dht_data["t1"] = None
        dht_data["h1"] = None
    
    try:
        dht_data["t2"] = sensor2.temperature
        dht_data["h2"] = sensor2.humidity
    except RuntimeError as e:
        print(f"DHT2 read error: {e}")
        dht_data["t2"] = None
        dht_data["h2"] = None
        
    last_dht_read_time = current_time
    
    # Print to console for confirmation
    print(f"DHT1: T={dht_data['t1']} H={dht_data['h1']} | DHT2: T={dht_data['t2']} H={dht_data['h2']}")

# --- Main Loop ---
running = True
while running:
    current_time = time.monotonic()
    
    # Check for Pygame events (like closing the window)
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            running = False

    # 1. Read DHT Sensors (Only if the interval has passed)
    if sensor1 and sensor2: # Only try if sensors were initialized
        read_dht_sensors(current_time)

    # 2. Read MLX90640 Frame
    try:
        mlx.getFrame(frame)
    except ValueError:
        continue # skip this frame

    # 3. Draw Thermal Feed
    
    # --- Clear the area for the thermal feed
    screen.fill((0, 0, 0)) # Clear the whole screen

    temp_min_frame = np.min(frame)
    temp_max_frame = np.max(frame)

    for y in range(SENSOR_HEIGHT):
        for x in range(SENSOR_WIDTH):
            temp = frame[y * SENSOR_WIDTH + x]
            
            norm = (temp - temp_min_frame) / (temp_max_frame - temp_min_frame + 0.001)
            color = get_heatmap_color(norm)
            
            # Draw box for thermal feed (offset by 40 pixels for DHT data)
            pygame.draw.rect(screen, color, 
                             (x * SCALE_FACTOR, y * SCALE_FACTOR, 
                              SCALE_FACTOR, SCALE_FACTOR))

            # Draw the temperature text overlay on the thermal feed
            text_color = TEXT_BLACK if norm > 0.5 else TEXT_WHITE
            text_str = f"{temp:0.0f}"
            text_surf = font.render(text_str, True, text_color)
            text_rect = text_surf.get_rect()
            text_rect.center = (x * SCALE_FACTOR + (SCALE_FACTOR // 2), 
                                y * SCALE_FACTOR + (SCALE_FACTOR // 2))
            screen.blit(text_surf, text_rect)

    # 4. Draw DHT Data Text Overlay at the bottom
    dht1_str = f"DHT1: T={dht_data['t1'] if dht_data['t1'] is not None else 'N/A'}C, H={dht_data['h1'] if dht_data['h1'] is not None else 'N/A'}%"
    dht2_str = f"DHT2: T={dht_data['t2'] if dht_data['t2'] is not None else 'N/A'}C, H={dht_data['h2'] if dht_data['h2'] is not None else 'N/A'}%"
    
    # Render and blit DHT data
    dht1_surf = font.render(dht1_str, True, TEXT_YELLOW)
    dht2_surf = font.render(dht2_str, True, TEXT_YELLOW)
    
    # Position them at the bottom
    screen.blit(dht1_surf, (10, SCREEN_HEIGHT - 35))
    screen.blit(dht2_surf, (SCREEN_WIDTH // 2, SCREEN_HEIGHT - 35))

    # 5. Update the full display
    pygame.display.flip()

# --- End of Loop ---
pygame.font.quit()
pygame.quit()
print("Pygame window closed. Exiting.")
