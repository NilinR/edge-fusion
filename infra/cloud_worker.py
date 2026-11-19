import time
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Edge Fusion Cloud Docker Worker")

DISTANCE_THRESHOLD_CM = 20.0


class TaskRequest(BaseModel):
    task_id: str
    device_id: str
    sensor_type: str = "ultrasonic"
    value: float
    payload_bytes: int = 128
    sla_ms: int = 50
    sent_timestamp_ms: int = 0


class TaskResponse(BaseModel):
    task_id: str
    is_anomaly: bool
    confidence: float
    action: str
    exec_time_ms: float
    worker_tier: str


@app.get("/health")
def health():
    return {"status": "healthy", "tier": "CLOUD_DOCKER"}


@app.post("/execute", response_model=TaskResponse)
def execute_task(req: TaskRequest):
    t0 = time.perf_counter()

    # Simulate lightweight inference/scoring workload (~4 ms)
    time.sleep(0.004)
    is_anomaly = req.value < DISTANCE_THRESHOLD_CM
    action = "TRIGGER_SERVO" if is_anomaly else "NORMAL"
    confidence = 0.95 if is_anomaly else 0.99

    exec_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return TaskResponse(
        task_id=req.task_id,
        is_anomaly=is_anomaly,
        confidence=confidence,
        action=action,
        exec_time_ms=exec_ms,
        worker_tier="CLOUD_DOCKER",
    )
