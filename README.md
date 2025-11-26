# Hive Monitoring System

This package contains two cleaned Python scripts for Raspberry Pi-based hive monitoring:

- **clean_fix2.py**: Sensor data collection and ThingSpeak upload.
- **clean_newbot2.py**: Telegram bot for remote control and monitoring.

## ✅ SSL Fix
Both scripts include a certifi-based SSL fix to prevent SSL certificate errors:
```python
import os
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
```

## 📦 Dependencies
Install the following Python packages:
```bash
pip install certifi requests Adafruit_DHT hx711 numpy scipy python-telegram-bot psutil
```

## 🔧 Hardware Requirements
- Raspberry Pi with GPIO enabled
- HX711 Load Cell Amplifier
- DHT22 Sensor
- MPU6050 Accelerometer/Gyro
- MAX4466 Microphone + MCP3008 ADC

## ▶️ How to Run

### 1. Start Sensor Script
```bash
python3 clean_fix2.py [initial_weight] [starter_name] [starter_id]
```
Example:
```bash
python3 clean_fix2.py 212 Vinod 12345
```

### 2. Start Telegram Bot
```bash
python3 clean_newbot2.py
```

## ⚠️ Notes
- Ensure your bot token and API keys are correctly set in the scripts.
- Place known weight on the scale during calibration.
- Data is logged to CSV and uploaded to ThingSpeak.

## ✅ Features
- SSL-proof communication
- Minimal logging (critical errors only)
- Telegram bot with member management and remote control

