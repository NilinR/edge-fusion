from typing import Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel

app = FastAPI(title="Edge Fusion Intelligent Scheduler")

# Exponentially Weighted Moving Average (EWMA) state for Cloud RTT
ewma_rtt_ms: Optional[float] = None
ALPHA = 0.3

# Empirical baseline execution estimates (ms)
EST_WASM_EXEC_MS = 3.5
EST_CLOUD_EXEC_MS = 7.0
ASSUMED_BANDWIDTH_KBPS = 5000.0  # 5 Mbps baseline


class DecisionRequest(BaseModel):
    task_id: str
    rtt_cloud_ms: float
    edge_cpu_pct: float
    edge_queue_len: int
    payload_bytes: int
    sla_ms: int


class DecisionResponse(BaseModel):
    task_id: str
    target: str                 # "EDGE_WASM" or "CLOUD_DOCKER"
    predicted_latency_ms: float
    ewma_rtt_ms: float
    reason: str


@app.get("/health")
def health_check():
    return {"status": "ok", "ewma_rtt_ms": ewma_rtt_ms}


@app.post("/decide", response_model=DecisionResponse)
def decide_placement(
    req: DecisionRequest,
    mode: str = Query("fusion", pattern="^(fusion|all-edge|all-cloud)$"),
):
    global ewma_rtt_ms

    if ewma_rtt_ms is None:
        ewma_rtt_ms = req.rtt_cloud_ms
    else:
        ewma_rtt_ms = ALPHA * req.rtt_cloud_ms + (1.0 - ALPHA) * ewma_rtt_ms

    # Predict local Edge Wasm delay (queue waiting time + CPU load penalty + Wasm exec)
    queue_delay_ms = req.edge_queue_len * EST_WASM_EXEC_MS
    cpu_penalty_ms = (req.edge_cpu_pct / 100.0) * 10.0
    pred_edge_ms = queue_delay_ms + cpu_penalty_ms + EST_WASM_EXEC_MS

    # Predict Cloud Docker delay (smoothed RTT + transmission delay + remote exec)
    tx_delay_ms = ((req.payload_bytes / 1024.0) / ASSUMED_BANDWIDTH_KBPS) * 1000.0
    pred_cloud_ms = ewma_rtt_ms + tx_delay_ms + EST_CLOUD_EXEC_MS

    # Fixed baseline overrides for demo & paper comparisons
    if mode == "all-edge":
        return DecisionResponse(
            task_id=req.task_id,
            target="EDGE_WASM",
            predicted_latency_ms=round(pred_edge_ms, 2),
            ewma_rtt_ms=round(ewma_rtt_ms, 2),
            reason="FORCED_ALL_EDGE_BASELINE",
        )

    if mode == "all-cloud":
        return DecisionResponse(
            task_id=req.task_id,
            target="CLOUD_DOCKER",
            predicted_latency_ms=round(pred_cloud_ms, 2),
            ewma_rtt_ms=round(ewma_rtt_ms, 2),
            reason="FORCED_ALL_CLOUD_BASELINE",
        )

    # Adaptive Edge Fusion decision logic
    if pred_edge_ms <= req.sla_ms and pred_edge_ms <= pred_cloud_ms:
        target = "EDGE_WASM"
        chosen_latency = pred_edge_ms
        reason = "EDGE_MEETS_SLA_FASTER"
    elif pred_cloud_ms <= req.sla_ms:
        target = "CLOUD_DOCKER"
        chosen_latency = pred_cloud_ms
        reason = "OFFLOAD_TO_CLOUD_FOR_SLA"
    elif pred_edge_ms <= pred_cloud_ms:
        target = "EDGE_WASM"
        chosen_latency = pred_edge_ms
        reason = "FALLBACK_LOWEST_DELAY_EDGE"
    else:
        target = "CLOUD_DOCKER"
        chosen_latency = pred_cloud_ms
        reason = "FALLBACK_LOWEST_DELAY_CLOUD"

    return DecisionResponse(
        task_id=req.task_id,
        target=target,
        predicted_latency_ms=round(chosen_latency, 2),
        ewma_rtt_ms=round(ewma_rtt_ms, 2),
        reason=reason,
    )