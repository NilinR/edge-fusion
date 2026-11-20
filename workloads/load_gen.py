import argparse
import json
import random
import time
import uuid

import paho.mqtt.client as mqtt

TOPIC_TELEMETRY = "edgefusion/telemetry"


def run_burst(broker: str, port: int, rate: int, duration: int, sla_ms: int):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(broker, port, keepalive=60)
    client.loop_start()

    interval = 1.0 / max(1, rate)
    total_sent = 0
    end_time = time.time() + duration

    print(f"Starting synthetic burst: {rate} req/s for {duration}s (SLA={sla_ms}ms)...")
    try:
        while time.time() < end_time:
            t0 = time.perf_counter()
            # Generate mostly normal distances (25-90 cm) with occasional anomalies (<20 cm)
            value = round(random.uniform(8.0, 18.0) if random.random() < 0.15 else random.uniform(25.0, 90.0), 2)

            payload = {
                "task_id": f"sim-{uuid.uuid4().hex[:6]}",
                "device_id": "sim-sensor-01",
                "sensor_type": "ultrasonic_sim",
                "value": value,
                "payload_bytes": 512,
                "sla_ms": sla_ms,
                "timestamp_ms": int(time.time() * 1000),
            }
            client.publish(TOPIC_TELEMETRY, json.dumps(payload))
            total_sent += 1

            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\nBurst stopped early by user.")
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"Finished. Total synthetic tasks published: {total_sent}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Edge Fusion Synthetic MQTT Load Generator")
    parser.add_argument("--broker", default="127.0.0.1", help="Mosquitto broker IP")
    parser.add_argument("--port", type=int, default=1883, help="Mosquitto broker port")
    parser.add_argument("--rate", type=int, default=40, help="Requests per second")
    parser.add_argument("--duration", type=int, default=30, help="Burst duration in seconds")
    parser.add_argument("--sla", type=int, default=50, help="Task SLA deadline in ms")
    args = parser.parse_args()

    run_burst(args.broker, args.port, args.rate, args.duration, args.sla)