# Deployment

## Modes
- `ITWIN_MODE=demo` (default): a built-in synthetic road feeds the twin. The clock starts at 07:30 and holds at 10:30. A hidden incident starts at 07:45 at km 7.0. Use this for demos and UI work.
- `ITWIN_MODE=feed`: no synthetic road. A gateway posts readings to `POST /v1/readings` every interval (see `examples/feed_client.py`).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ITWIN_API_KEYS` | (required) | `role:key` pairs, comma-separated. Roles: `viewer`, `planner`, `operator`. Keys ≥ 24 characters |
| `ITWIN_MODE` | `demo` | `demo` or `feed` |
| `ITWIN_SPEED` | `20` | demo mode: simulated seconds per real second |
| `ITWIN_NETWORK` | bundled demo corridor | path to a network TOML (check it with `itwin check-network path`) |
| `ITWIN_HOST` / `ITWIN_PORT` | `127.0.0.1` / `8080` | bind address (the container uses `0.0.0.0`) |

## Container
```bash
cp .env.example .env   # replace every key
docker compose up -d --build
open http://127.0.0.1:8080/dashboard
```
The container runs as UID 10001 with a read-only root filesystem and no capabilities. It keeps no state on disk: the twin rebuilds its state from the next readings after a restart.

## Integrating a real corridor
1. Describe the corridor in TOML: cells, lanes, entries with plazas, exits, detectors. Start from `src/itwin/default_network.toml`.
2. Calibrate the parameters (architecture notes, section 8) and check the model against recorded days before going live.
3. Build a gateway that reads the detector and toll systems and posts 30-second aggregates. Map fault codes from the source systems to missing readings rather than zeros.
4. Run in shadow mode first: the twin computes meter rates, but they are not sent to the field. Compare them with the existing strategy.
5. Connect the meter output through the field controllers' own interface, with a local fallback if the twin is unavailable.
6. Agree alarm procedures with the control room: who checks CCTV, who confirms closures, and how quickly.

## Monitoring
`/metrics` exposes travel time, entry queues, sensor health, active alarms and HTTP request counts in Prometheus format. `/healthz` is liveness; `/readyz` reports the model clock.
