# ADR 0001: Macroscopic cell transmission model, not microsimulation

- Status: accepted
- Date: 2026-11-24

## Context
The twin must run faster than real time many times over: every 30 seconds for the live state, and dozens of times for each what-if request (baseline plus scenarios, 60 to 240 minutes ahead). Planners want answers in seconds. Microsimulators (car-following and lane-changing models) are more detailed but need far more parameters, are stochastic, and are slow at corridor scale.

## Decision
Use the Cell Transmission Model (Daganzo 1994/1995) with a triangular fundamental diagram, plus four common extensions: capacity drop, FIFO diverge, priority merge, and point queues at entries and plazas.

## Consequences
- A 5-hour peak runs in well under a second. What-if requests are synchronous and deterministic.
- Parameters are few and can be calibrated from detector data (section 8 of the architecture notes).
- Lane-level behaviour (weaving, lane choice at plazas, individual vehicle delays) is not represented. If those questions matter, use a microsimulator offline, seeded with the twin's state.
- Conservation of vehicles is exact and tested.
