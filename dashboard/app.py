import os
import time
import pandas as pd
import streamlit as st

METRICS_CSV = "metrics.csv"

st.set_page_config(page_title="Edge Fusion Live Monitor", layout="wide")
st.title("Edge Fusion: Cloud–Edge Virtualization & Offloading Monitor")

col_ctrl1, col_ctrl2 = st.columns([1, 4])
with col_ctrl1:
    auto_refresh = st.checkbox("Live Auto-Refresh (1s)", value=True)
with col_ctrl2:
    window_size = st.slider("Recent Tasks Window", min_value=30, max_value=300, value=100, step=10)

if not os.path.exists(METRICS_CSV):
    st.info("Waiting for `metrics.csv`... Start `control-plane/proxy.py` and send telemetry.")
    if auto_refresh:
        time.sleep(1.5)
        st.rerun()
    st.stop()

try:
    df = pd.read_csv(METRICS_CSV)
except Exception:
    st.warning("Reading metrics stream...")
    time.sleep(0.5)
    st.rerun()

if df.empty:
    st.info("No tasks recorded yet.")
    if auto_refresh:
        time.sleep(1.5)
        st.rerun()
    st.stop()

recent = df.tail(window_size).copy()
recent["Task Index"] = range(len(df) - len(recent) + 1, len(df) + 1)

# --- Top KPI Metrics ---
total_tasks = len(recent)
avg_latency = recent["actual_latency_ms"].mean()
sla_compliance = (1.0 - recent["sla_violated"].mean()) * 100.0
latest_rtt = recent["ewma_rtt_ms"].iloc[-1]
cloud_pct = (recent["target"] == "CLOUD_DOCKER").mean() * 100.0
edge_pct = 100.0 - cloud_pct

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Window Tasks", f"{total_tasks}")
m2.metric("Avg End-to-End Latency", f"{avg_latency:.1f} ms")
m3.metric("SLA Compliance (<50ms)", f"{sla_compliance:.1f}%")
m4.metric("Cloud EWMA RTT", f"{latest_rtt:.1f} ms")
m5.metric("Edge / Cloud Split", f"{edge_pct:.0f}% / {cloud_pct:.0f}%")

st.divider()

# --- Charts ---
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("End-to-End Task Latency vs. Cloud Network RTT (ms)")
    chart_df = recent.set_index("Task Index")[["actual_latency_ms", "ewma_rtt_ms", "sla_ms"]]
    chart_df.columns = ["Actual Task Latency (ms)", "Cloud EWMA RTT (ms)", "SLA Deadline (ms)"]
    st.line_chart(chart_df)

with c2:
    st.subheader("Execution Tier Distribution")
    tier_counts = recent["target"].value_counts()
    st.bar_chart(tier_counts)

# --- Recent Scheduler Decisions Table ---
st.subheader("Latest Offloading Decisions")
display_cols = [
    "task_id", "device_id", "target", "reason",
    "actual_latency_ms", "ewma_rtt_ms", "edge_queue_len", "action"
]
st.dataframe(recent[display_cols].iloc[::-1].head(12), use_container_width=True, hide_index=True)

if auto_refresh:
    time.sleep(1.0)
    st.rerun()