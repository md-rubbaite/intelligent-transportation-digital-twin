# Threat model (STRIDE)

Scope: the twin service, its API and console, the data gateway that posts readings, and the meter-rate output. Road-side controllers, the detector network and the toll system are out of scope, but they are the reason the risks below matter: this service sits next to operational technology (OT).

## Assets
- Meter rates sent to the road (the one output that changes traffic)
- Integrity of the live state and alarms that operators act on
- What-if results used for incident and planning decisions
- API keys

## Trust boundaries
1. Gateway → API (readings): machine-to-machine, operator role.
2. Browsers → API (console): people, any role.
3. Twin → field meter controllers: integration outside this repo.

## Threats and controls

| # | STRIDE | Threat | Control in this repo | Residual risk / next step |
|---|---|---|---|---|
| 1 | Spoofing | Someone posts fake readings to trigger alarms or skew meters | Operator key required; keys stored as SHA-256 and compared in constant time | Use mTLS between gateway and twin; one key per gateway |
| 2 | Tampering | Valid-looking but false readings (compromised detector or gateway) | Range, consistency, staleness and stuck checks; assimilation gain < 1; meter rates bounded with queue override; closures need a person (ADR 0004) | Plausibility across sources (probe data, toll counts) |
| 3 | Tampering | Replay of old readings | Batches whose `now_s` is earlier than the twin clock are refused (`409`); readings more than 2 min old are flagged `stale` and not used | Signed payloads with nonces from the gateway |
| 4 | Repudiation | "Who switched metering off / confirmed that closure?" | Alerts log records the action and role; JSON access log with request id | Ship logs to an append-only store; per-person identities through SSO |
| 5 | Information disclosure | Leaking keys from the console | Key kept in page memory only (not stored); `no-store`, `no-referrer`; strict CSP, `frame-ancestors 'none'` | SSO for people, keys only for machines |
| 6 | Information disclosure | Detector data reveals movements | Only aggregate flow, speed and occupancy; no plates or IDs | Keep it that way: do not feed ANPR data into this service |
| 7 | Denial of service | Expensive what-if calls exhaust CPU | Horizon ≤ 240 min, ≤ 10 closures, planner role; runs are bounded and deterministic | Rate limit per key at the gateway or ingress |
| 8 | Denial of service | Large reading batches | ≤ 500 readings per call, validated with Pydantic | Request size limit at ingress |
| 9 | Elevation of privilege | Viewer runs operator actions | Role check on every endpoint; tests cover 401/403 | Periodic key rotation |
| 10 | Tampering | XSS through detector IDs or alert text in the console | All dynamic text escaped; CSP blocks third-party scripts | Move inline script to a file and drop `'unsafe-inline'` |
| 11 | Tampering | Supply-chain change in dependencies or actions | Two runtime dependencies; actions pinned to commit SHAs; Dependabot; CodeQL | Signed images and SBOM in release pipeline |
| 12 | Elevation of privilege | Container escape or write | Non-root UID 10001, read-only root filesystem, all capabilities dropped, no-new-privileges | Run in a segmented OT DMZ, not on the control network itself |

## Relevant frameworks
For deployment on a real network, map these controls to the operator's OT security programme, for example IEC 62443 zones and conduits. Where it applies, also cover the national rules for critical infrastructure and the EU NIS2 directive. This document is an engineering threat model, not a compliance assessment.
