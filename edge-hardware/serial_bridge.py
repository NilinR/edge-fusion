import json
import os
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import serial
import serial.tools.list_ports

MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_TELEMETRY = "edgefusion/telemetry"
TOPIC_ACTUATE = "edgefusion/actuate"

DEVICE_ID = "arduino-uno-01"
BAUD_RATE = 115200
SLA_MS = 50

serial_lock = threading.Lock()
ser: serial.Serial | None = None


def find_arduino_port() -> str:
    env_port = os.getenv("ARDUINO_PORT")
    if env_port:
        return env_port
    for port in serial.tools.list_ports.comports():
        desc = (port.description or "").lower()
        if any(k in desc for k in ("arduino", "ch340", "usb serial", "usb-serial")):
            return port.device
    return "COM3"


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"MQTT connected -> Listening for servo commands on {TOPIC_ACTUATE}")
    client.subscribe(TOPIC_ACTUATE)


def on_message(client, userdata, msg):
    global ser
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        if data.get("device_id") != DEVICE_ID:
            return
        action = data.get("action", "NORMAL")
        with serial_lock:
            if ser and ser.is_open:
                ser.write(f"{action}\n".encode("utf-8"))
    except Exception as exc:
        print(f"Actuation forward error: {exc}")


def main():
    global ser
    port = find_arduino_port()
    print(f"Opening Arduino serial port {port} at {BAUD_RATE} baud...")
    ser = serial.Serial(port, BAUD_RATE, timeout=1.0)
    time.sleep(2.0)  # Wait for Arduino Uno auto-reset after serial open

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    print(f"Streaming ultrasonic telemetry from {DEVICE_ID} -> {TOPIC_TELEMETRY}")
    try:
        while True:
            with serial_lock:
                raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not raw_line:
                continue
            try:
                sensor_data = json.loads(raw_line)
                distance_cm = float(sensor_data["value"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

            payload = {
                "task_id": f"ard-{uuid.uuid4().hex[:6]}",
                "device_id": DEVICE_ID,
                "sensor_type": "ultrasonic",
                "value": distance_cm,
                "payload_bytes": 128,
                "sla_ms": SLA_MS,
                "timestamp_ms": int(time.time() * 1000),
            }
            client.publish(TOPIC_TELEMETRY, json.dumps(payload))
    except KeyboardInterrupt:
        print("\nStopping serial bridge...")
    finally:
        client.loop_stop()
        if ser and ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()