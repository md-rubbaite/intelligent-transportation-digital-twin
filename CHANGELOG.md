# Changelog

## 1.0.0 — 2026-11-24
- Cell Transmission Model with capacity drop, FIFO diverge, priority merge, toll-plaza queues (ETC and manual) and ramp-meter limits; tested for vehicle conservation.
- Detector quality checks (missing, range, stale, future, inconsistent, stuck) and sensor health.
- Nudging assimilation with interpolation between detectors; demand correction from toll-plaza counts.
- Residual-based incident detection (3 of 4 rule), clearing, and prompts to reopen confirmed closures.
- PI-ALINEA ramp metering with queue override.
- What-if scenarios forked from the live state: closures, reopening times, demand, metering, plaza lanes.
- Erlang C toll-lane sizing.
- FastAPI service with viewer / planner / operator keys, operator console, Prometheus metrics, JSON access logs.
- Scripted demo and reproducible validation runs on synthetic data; container and CI.
