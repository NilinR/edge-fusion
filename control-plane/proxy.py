import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import paho.mqtt.client as mqtt
import psutil
from wasmtime import Engine, Instance, Module, Store, wat2wasm

# --- Configuration (Override via environment variables when Cloud VM is ready) ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_TELEMETRY = "edgefusion/telemetry"
TOPIC_ACTUATE = "edgefusion/actuate"

SCHEDULER_URL = os.getenv("SCHEDULER_URL", "http://127.0.0.1:8001/decide")
SCHEDULER_MODE = os.getenv("SCHEDULER_MODE", "fusion")  # fusion | all-edge | all-cloud
CLOUD_WORKER_URL = os.getenv("CLOUD_WORKER_URL", "http://127.0.0.1:8000")

EDGE_MAX_WORKERS = 2        # Throttled pool to emulate a resource-constrained edge gateway
DISTANCE_THRESHOLD_CM = 20.0
METRICS_CSV = "metrics.csv"

# --- WebAssembly Sandbox Setup (Compiled from WAT to Wasm binary) ---
WAT_SOURCE = """
(module
  (func (export "evaluate") (param $val f64) (param $thresh f64) (result i32)
    local.get $val
    local.get $thresh
    f64.lt
  )
)
"""
WASM_ENGINE = Engine()
WASM_MODULE = Module(WASM_ENGINE, wat2wasm(WAT_SOURCE))

# --- Shared Runtime State ---
rtt_lock = threading.Lock()
latest_cloud_rtt_ms = 25.0  # Initial estimate before first ping completes

queue_lock = threading.Lock()
pending_tasks = 0

csv_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=EDGE_MAX_WORKERS)
http_client = httpx.Client(timeout=2.5)


def init_csv():
    if not os.path.exists(METRICS_CSV):
        with open(METRICS_CSV, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_ms", "task_id", "device_id", "target", "reason",
                "predicted_latency_ms", "actual_latency_ms", "ewma_rtt_ms",
                "edge_queue_len", "sla_ms", "sla_violated", "action"
            ])


def ping_cloud_loop():
    """Background probe measuring live RTT to the Cloud VM every 500 ms."""
    global latest_cloud_rtt_ms
    while True:
        start = time.perf_counter()
        try:
            resp = http_client.get(f"{CLOUD_WORKER_URL}/health", timeout=1.0)
            if resp.status_code == 200:
                rtt = (time.perf_counter() - start) * 1000.0
                with rtt_lock:
                    latest_cloud_rtt_ms = rtt
        except Exception:
            # If Cloud VM is unreachable or timing out, report high RTT penalty
            with rtt_lock:
                latest_cloud_rtt_ms = 999.0
        time.sleep(0.5)


def run_edge_wasm(value: float) -> tuple[bool, str, float]:
    """Executes sensor threshold logic inside an isolated Wasmtime sandbox."""
    t0 = time.perf_counter()
    store = Store(WASM_ENGINE)
    instance = Instance(store, WASM_MODULE, [])
    evaluate_fn = instance.exports(store)["evaluate"]
    is_anomaly = bool(evaluate_fn(store, float(value), DISTANCE_THRESHOLD_CM))
    exec_ms = (time.perf_counter() - t0) * 1000.0
    action = "TRIGGER_SERVO" if is_anomaly else "NORMAL"
    return is_anomaly, action, exec_ms


def run_cloud_docker(payload: dict) -> tuple[bool, str, float]:
    """Forwards task over HTTP to the remote Cloud Docker worker."""
    t0 = time.perf_counter()
    req_body = {
        "task_id": payload["task_id"],
        "device_id": payload["device_id"],
        "sensor_type": payload.get("sensor_type", "ultrasonic"),
        "value": float(payload["value"]),
        "payload_bytes": int(payload.get("payload_bytes", 128)),
        "sla_ms": int(payload.get("sla_ms", 50)),
        "sent_timestamp_ms": int(payload.get("timestamp_ms", time.time() * 1000)),
    }
    resp = http_client.post(f"{CLOUD_WORKER_URL}/execute", json=req_body)
    resp.raise_for_status()
    data = resp.json()
    roundtrip_ms = (time.perf_counter() - t0) * 1000.0
    return bool(data["is_anomaly"]), data["action"], roundtrip_ms


def process_task(mqtt_client: mqtt.Client, payload: dict):
    global pending_tasks
    arrival_t0 = time.perf_counter()

    task_id = payload.get("task_id", "unknown")
    device_id = payload.get("device_id", "unknown")
    value = float(payload.get("value", 100.0))
    payload_bytes = int(payload.get("payload_bytes", 128))
    sla_ms = int(payload.get("sla_ms", 50))

    with queue_lock:
        q_len = pending_tasks
    with rtt_lock:
        current_rtt = latest_cloud_rtt_ms
    cpu_pct = psutil.cpu_percent(interval=None)

    # 1. Ask Intelligent Scheduler for placement decision
    decision_req = {
        "task_id": task_id,
        "rtt_cloud_ms": round(current_rtt, 2),
        "edge_cpu_pct": round(cpu_pct, 2),
        "edge_queue_len": q_len,
        "payload_bytes": payload_bytes,
        "sla_ms": sla_ms,
    }

    try:
        dec_resp = http_client.post(
            f"{SCHEDULER_URL}?mode={SCHEDULER_MODE}", json=decision_req
        )
        dec_resp.raise_for_status()
        decision = dec_resp.json()
        target = decision["target"]
        pred_ms = decision["predicted_latency_ms"]
        ewma_rtt = decision["ewma_rtt_ms"]
        reason = decision["reason"]
    except Exception as exc:
        target = "EDGE_WASM"
        pred_ms = 5.0
        ewma_rtt = current_rtt
        reason = f"SCHEDULER_FALLBACK ({type(exc).__name__})"

    # 2. Execute on chosen virtualization tier
    try:
        if target == "CLOUD_DOCKER":
            _, action, _ = run_cloud_docker(payload)
        else:
            target = "EDGE_WASM"
            _, action, _ = run_edge_wasm(value)
    except Exception:
        # Automatic resilience failover to local Wasm if Cloud call errors out
        target = "EDGE_WASM"
        reason = "CLOUD_ERROR_FAILOVER_EDGE"
        _, action, _ = run_edge_wasm(value)

    total_latency_ms = round((time.perf_counter() - arrival_t0) * 1000.0, 2)
    sla_violated = total_latency_ms > sla_ms

    with queue_lock:
        pending_tasks = max(0, pending_tasks - 1)

    # 3. Publish actuator command back to MQTT (Arduino servo listens here)
    act_msg = {
        "task_id": task_id,
        "device_id": device_id,
        "action": action,
        "servo_angle": 90 if action == "TRIGGER_SERVO" else 0,
        "executed_on": target,
        "total_latency_ms": total_latency_ms,
    }
    mqtt_client.publish(TOPIC_ACTUATE, json.dumps(act_msg))

    # 4. Append telemetry row for live dashboard & paper plots
    with csv_lock:
        with open(METRICS_CSV, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                int(time.time() * 1000), task_id, device_id, target, reason,
                pred_ms, total_latency_ms, ewma_rtt, q_len, sla_ms,
                int(sla_violated), action
            ])

    print(
        f"[{task_id}] {target:12s} | {total_latency_ms:6.2f} ms (SLA {sla_ms} ms) "
        f"| {action:13s} | {reason}"
    )


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected to Mosquitto ({MQTT_BROKER}:{MQTT_PORT}) -> Subscribing to {TOPIC_TELEMETRY}")
    client.subscribe(TOPIC_TELEMETRY)


def on_message(client, userdata, msg):
    global pending_tasks
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        with queue_lock:
            pending_tasks += 1
        executor.submit(process_task, client, payload)
    except Exception as exc:
        print(f"Malformed MQTT payload: {exc}")


def main():
    init_csv()
    psutil.cpu_percent(interval=None)  # Prime non-blocking CPU counter

    ping_thread = threading.Thread(target=ping_cloud_loop, daemon=True)
    ping_thread.start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()