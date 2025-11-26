import os
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
import RPi.GPIO as GPIO
from hx711 import HX711
import smbus
import time
import requests
import Adafruit_DHT
from datetime import datetime
import numpy as np
from scipy.fft import rfft, rfftfreq
import spidev
import os 
import sys 
import csv 

# ---------------------------
# GLOBAL CONFIGURATION
# ---------------------------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# --- ThingSpeak Setup ---
# CHANNEL 1: Environment & Motion (ID: 3173934)
TS_ENV_MOTION_URL = "https://api.thingspeak.com/update"
TS_ENV_MOTION_API_KEY = "5YCLLHKETJNYW9SL"

# CHANNEL 2: Load Cell & Audio (ID: 3175777)
TS_WEIGHT_AUDIO_URL = "https://api.thingspeak.com/update"
TS_WEIGHT_AUDIO_API_KEY = "4LFS7NGH9THTXTL6"

# --- Telegram Bot Setup ---
TELEGRAM_TOKEN = "8280400805:AAFY5aIJ9jrPUi6G4YhwhUbXbDPi1EMMQDc"
TELEGRAM_LOG_CHANNEL = "@MyHiveAlerts" 

# --- Alert Thresholds ---
ALERT_FREQ_CHANGE_THRESHOLD = 20 # Delta change
ALERT_TEMP_HIGH = 36
ALERT_TEMP_LOW = 30
ALERT_HUMID_LOW = 40
ALERT_HUMID_HIGH = 70

# --- File Paths ---
BASE_PATH = "/home/vinod"
HIVE_DATA_FILE = os.path.join(BASE_PATH, "hive_update.csv")

# --- DHT22 + MPU6050 Setup ---
DHT_SENSOR = Adafruit_DHT.DHT22
DHT_PIN = 4
MPU6050_ADDR = 0x68
PWR_MGMT_1   = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_XOUT_H  = 0x43
ACCEL_CONFIG = 0x1C 
GYRO_CONFIG  = 0x1B 

# Initialize I2C (MPU6050)
try:
    bus = smbus.SMBus(1)
except Exception as e:
    print(f"Warning: I2C Bus (MPU6050) not detected: {e}")
    bus = None

# --- MAX4466 + MCP3008 Setup ---
spi = spidev.SpiDev()
try:
    spi.open(0, 0)
    spi.max_speed_hz = 1350000
except Exception as e:
    print(f"Warning: SPI (MCP3008) not detected: {e}")

SAMPLE_RATE = 2000
DURATION = 1
NUM_SAMPLES = SAMPLE_RATE * DURATION

# --- Global Variables ---
global_scale_ratio = 1.0 
CURRENT_STARTER_NAME = "System"
CURRENT_STARTER_ID = "N/A"

# ---------------------------
# TELEGRAM & LOGGING FUNCTIONS
# ---------------------------

def send_telegram_message(chat_id, message):
    """Sends a raw message to a specified chat_id."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram Error]: {e}")

def send_simple_log(message):
    send_telegram_message(TELEGRAM_LOG_CHANNEL, message)

def send_telegram_data_and_alerts(data_message, alerts):
    if alerts:
        alert_section = "🚨 *CRITICAL ALERTS* 🚨\n" + "\n".join(alerts) + "\n\n"
    else:
        alert_section = ""
    full_message = alert_section + data_message
    send_telegram_message(TELEGRAM_LOG_CHANNEL, full_message)

def log_to_hive_data_csv(data, is_initial_run=False):
    file_exists = os.path.exists(HIVE_DATA_FILE)
    
    data_row = [
        data.get('datestamp'),
        data.get('temperature'),
        data.get('humidity'),
        data.get('weight'),
        data.get('dominant_freq'),
        data.get('accel_x'),
        data.get('accel_y'),
        data.get('accel_z'),
        data.get('gyro_x'),
        data.get('gyro_y'),
        data.get('gyro_z')
    ]
    
    try:
        with open(HIVE_DATA_FILE, mode='a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists or is_initial_run:
                writer.writerow([
                    "Timestamp", "Temperature (C)", "Humidity (%)", "Weight (g)", 
                    "Frequency (Hz)", "Accel X", "Accel Y", "Accel Z", 
                    "Gyro X", "Gyro Y", "Gyro Z"
                ])
            if not is_initial_run:
                writer.writerow(data_row)
    except Exception as e:
        print(f"[CSV Error]: {e}")

# ---------------------------
# SENSOR READ FUNCTIONS
# ---------------------------

def read_raw_data(addr):
    if bus is None: return 0
    try:
        high = bus.read_byte_data(MPU6050_ADDR, addr)
        low = bus.read_byte_data(MPU6050_ADDR, addr + 1)
        value = (high << 8) | low
        if value > 32768:
            value -= 65536
        return value
    except Exception:
        return 0

def read_channel(channel):
    if not spi: return 0
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    data = ((adc[1] & 3) << 8) + adc[2]
    return data

def analyze_audio():
    """Performs FFT analysis to find dominant frequency."""
    samples = []
    try:
        for _ in range(NUM_SAMPLES):
            samples.append(read_channel(0))
            time.sleep(1 / SAMPLE_RATE)
        
        signal = np.array(samples)
        fft_result = rfft(signal)
        frequencies = rfftfreq(NUM_SAMPLES, 1 / SAMPLE_RATE)
        magnitude = np.abs(fft_result)

        # Filter for bee frequencies (broad range 50Hz - 600Hz to catch roaring/piping)
        valid_indices = np.where((frequencies >= 50) & (frequencies <= 600))
        filtered_freqs = frequencies[valid_indices]
        filtered_magnitude = magnitude[valid_indices]

        if filtered_freqs.size > 0:
            dominant_freq = filtered_freqs[np.argmax(filtered_magnitude)]
            return round(dominant_freq, 2)
    except Exception as e:
        print(f"Audio Analysis Error: {e}")
    return 0.0 

def get_bee_behavior_status(freq):
    """Returns a status string and a detailed alert with explanation based on frequency."""
    
    if freq > 450:
        status = "⚔️ Aggressive / Swarming"
        alert = (
            f"⚔️ *DANGER: Aggression/Swarm Detected!* ({freq}Hz)\n"
            "_High pitch indicates bees are defensive or taking flight to swarm._"
        )
        return status, alert
        
    elif 330 <= freq <= 450:
        status = "👑 Queen Piping"
        alert = (
            f"👑 *ALERT: Queen Piping Detected!* ({freq}Hz)\n"
            "_Virgin queen is signaling; a swarm may leave the hive soon._"
        )
        return status, alert
        
    elif 190 <= freq < 330:
        status = "🟢 Normal / Active"
        # No alert for normal behavior
        return status, None
        
    elif 100 <= freq < 190:
        status = "🆘 Queenless Roar"
        alert = (
            f"🆘 *WARNING: Queenless Roar!* ({freq}Hz)\n"
            "_Low, chaotic moaning sound indicates distress or a missing queen._"
        )
        return status, alert
        
    elif 0 < freq < 100:
        status = "💤 Dormant / Low"
        alert = (
            f"💤 *NOTICE: Low Activity* ({freq}Hz)\n"
            "_Hive is dormant/sleeping, or the sensor path is obstructed._"
        )
        return status, alert
        
    else:
        return "⚪ Unknown / Silence", None

def get_all_sensor_data(hx):
    datestamp = datetime.now()
    
    # HX711 Weight
    try:
        weight = round(hx.get_weight_mean(20), 2)
    except:
        weight = 0.0

    # DHT22
    humidity, temperature = Adafruit_DHT.read_retry(DHT_SENSOR, DHT_PIN)
    if humidity is None: humidity = 0
    if temperature is None: temperature = 0
    
    # MPU6050
    accel_x = read_raw_data(ACCEL_XOUT_H) / 16384.0
    accel_y = read_raw_data(ACCEL_XOUT_H + 2) / 16384.0
    accel_z = read_raw_data(ACCEL_XOUT_H + 4) / 16384.0
    gyro_x = read_raw_data(GYRO_XOUT_H) / 131.0
    gyro_y = read_raw_data(GYRO_XOUT_H + 2) / 131.0
    gyro_z = read_raw_data(GYRO_XOUT_H + 4) / 131.0

    return {
        'datestamp': datestamp.strftime("%Y-%m-%d %H:%M:%S"),
        'weight': weight,
        'temperature': temperature,
        'humidity': humidity,
        'accel_x': round(accel_x, 2),
        'accel_y': round(accel_y, 2),
        'accel_z': round(accel_z, 2),
        'gyro_x': round(gyro_x, 2),
        'gyro_y': round(gyro_y, 2),
        'gyro_z': round(gyro_z, 2),
    }

# ---------------------------
# CALIBRATION & MAIN
# ---------------------------

def calibration_mode(hx, initial_weight):
    global global_scale_ratio
    hx.reset()
    hx.zero() 
    
    time.sleep(10) 

    raw_weight_samples = []
    for i in range(5):
        raw_weight_samples.append(hx.get_weight_mean(50))
        time.sleep(0.5)

    avg_raw_value = np.mean(raw_weight_samples)
    if avg_raw_value == 0: avg_raw_value = 1 
    
    scale_ratio = avg_raw_value / initial_weight
    hx.set_scale_ratio(scale_ratio)
    global_scale_ratio = scale_ratio
    
    final_weight = round(hx.get_weight_mean(20), 2)
    log_message = f"✅ *Calibration Done!*\nRatio: `{scale_ratio:.2f}`\nTest Weight: {final_weight}g"
    send_simple_log(log_message)

def main():
    global CURRENT_STARTER_NAME, CURRENT_STARTER_ID
    
    if len(sys.argv) >= 4:
        try:
            initial_weight = float(sys.argv[1])
            CURRENT_STARTER_NAME = sys.argv[2]
            CURRENT_STARTER_ID = sys.argv[3]
        except ValueError:
            initial_weight = 212.0
    else:
        initial_weight = 212.0

    # Init MPU
    if bus:
        try:
            bus.write_byte_data(MPU6050_ADDR, PWR_MGMT_1, 0)
            bus.write_byte_data(MPU6050_ADDR, ACCEL_CONFIG, 0x00)
            bus.write_byte_data(MPU6050_ADDR, GYRO_CONFIG, 0x00)
        except: pass

    hx = HX711(dout_pin=5, pd_sck_pin=6)
    calibration_mode(hx, initial_weight)

    send_simple_log(f"🚀 *Started* by {CURRENT_STARTER_NAME}")
    log_to_hive_data_csv({}, is_initial_run=True) 

    
    previous_freq = 0.0
    last_audio_time = 0.0 
    SYNC_INTERVAL = 25 

    try:
        while True:
            current_time = time.time()
            
            if current_time - last_audio_time >= SYNC_INTERVAL:
                
                # 1. Get Data
                sensor_data = get_all_sensor_data(hx)
                
                # 2. Audio Analysis
                dominant_freq = analyze_audio()
                sensor_data['dominant_freq'] = dominant_freq 
                
                # 3. Determine Acoustic Status & Alerts
                behavior_status, behavior_alert = get_bee_behavior_status(dominant_freq)
                
                # 4. Check Alerts
                alerts = []
                
                # --- Frequency / Acoustic Alerts ---
                if behavior_alert:
                    alerts.append(behavior_alert)
                
                # Check for rapid frequency shifts (sudden disturbance)
                if abs(dominant_freq - previous_freq) >= ALERT_FREQ_CHANGE_THRESHOLD and previous_freq > 0:
                     alerts.append(f"⚠️ *Sudden Shift:* {previous_freq}Hz ⮕ {dominant_freq}Hz")

                # --- Environmental Alerts ---
                if sensor_data['temperature'] > ALERT_TEMP_HIGH:
                    alerts.append(f"🔥 *Too Hot!* {sensor_data['temperature']:.1f}°C")
                elif sensor_data['temperature'] < ALERT_TEMP_LOW:
                    alerts.append(f"🥶 *Too Cold!* {sensor_data['temperature']:.1f}°C")
                
                if sensor_data['humidity'] < ALERT_HUMID_LOW:
                    alerts.append(f"🌵 *Too Dry!* {sensor_data['humidity']:.1f}%")
                elif sensor_data['humidity'] > ALERT_HUMID_HIGH:
                    alerts.append(f"💧 *Too Humid!* {sensor_data['humidity']:.1f}%")
                
                previous_freq = dominant_freq
                last_audio_time = current_time

                # 5. Telegram Log (FANCY FORMAT)
                msg = f"""
🐝 *HIVE MONITOR REPORT* 🐝
──────────────────────
🕒 *Time:* `{sensor_data['datestamp']}`

🔊 *Acoustics & Behavior*
├ Freq: `{dominant_freq} Hz`
└ Status: *{behavior_status}*

🌡️ *Environment*
├ Temp: `{sensor_data['temperature']:.1f} °C`
└ Hum:  `{sensor_data['humidity']:.1f} %`

⚖️ *Production*
└ Weight: `{sensor_data['weight']} g`
──────────────────────

To access our bot:"@Dummyinvbot"

______________________

Have a good day!❤
"""
                send_telegram_data_and_alerts(msg, alerts)
                log_to_hive_data_csv(sensor_data)

                # 6. Upload to ThingSpeak
                payload_env_motion = {
                    "api_key": TS_ENV_MOTION_API_KEY,
                    "field1": sensor_data['temperature'],
                    "field2": sensor_data['humidity'],
                    "field3": sensor_data['accel_x'],
                    "field4": sensor_data['accel_y'],
                    "field5": sensor_data['accel_z'],
                    "field6": sensor_data['gyro_x'],
                    "field7": sensor_data['gyro_y'],
                    "field8": sensor_data['gyro_z']
                }
                
                payload_weight_audio = {
                    "api_key": TS_WEIGHT_AUDIO_API_KEY,
                    "field1": sensor_data['weight'],
                    "field2": sensor_data['datestamp'],
                    "field3": dominant_freq,
                    "field4": sensor_data['datestamp']
                }

                try:
                    r1 = requests.post(TS_ENV_MOTION_URL, data=payload_env_motion, timeout=10)
                    time.sleep(2) 
                    r2 = requests.post(TS_WEIGHT_AUDIO_URL, data=payload_weight_audio, timeout=10)
                except Exception as e:
                    print(f"ThingSpeak Upload Error: {e}")

            
            time.sleep(0.1)

    except KeyboardInterrupt:
        send_simple_log("🛑 *Stopped*")
        GPIO.cleanup()

if __name__ == "__main__":
    main()
