"""HTTP API and operator console (FastAPI).

Roles: ``viewer`` (read state), ``planner`` (also run what-if scenarios and sizing),
``operator`` (also send readings and switch ramp metering). Keys come from the
environment and are kept only as SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__
from .network import NetworkError
from .plaza import lanes_needed
from .scenario import Closure, PlazaChange, Scenario
from .sensors import Reading
from .twin import DigitalTwin, SyntheticRoad, run_closed_loop

log = logging.getLogger("itwin")
ROLES = ("viewer", "planner", "operator")


class KeyStore:
    def __init__(self, spec: str) -> None:
        """``spec`` is 'role:key,role:key'. Keys shorter than 24 characters are refused."""
        self._keys: list[tuple[bytes, str]] = []
        for part in filter(None, (p.strip() for p in spec.split(","))):
            role, sep, key = part.partition(":")
            if not sep or role not in ROLES:
                raise ValueError(f"invalid key entry for role '{role}': use role:key with role in {ROLES}")
            if len(key) < 24:
                raise ValueError(f"key for role '{role}' is too short (minimum 24 characters)")
            self._keys.append((hashlib.sha256(key.encode()).digest(), role))
        if not self._keys:
            raise ValueError("no API keys configured")

    def role(self, key: str | None) -> str | None:
        if not key:
            return None
        digest = hashlib.sha256(key.encode()).digest()
        found = None
        for d, role in self._keys:
            if hmac.compare_digest(d, digest):
                found = role
        return found


class ReadingIn(BaseModel):
    detector_id: str = Field(min_length=1, max_length=32)
    timestamp_s: float = Field(ge=0, le=7 * 86400)
    flow_vph: float | None = None
    speed_kmh: float | None = None
    occupancy_pct: float | None = None


class ReadingsIn(BaseModel):
    readings: list[ReadingIn] = Field(min_length=1, max_length=500)
    plaza_counts_vph: dict[str, float] = Field(default_factory=dict)
    now_s: float | None = Field(default=None, ge=0, le=7 * 86400)


class ClosureIn(BaseModel):
    cell: str = Field(pattern=r"^C\d{2,3}$")
    lanes_closed: int = Field(ge=1, le=8)
    start_min: float = Field(default=0, ge=0, le=240)
    duration_min: float = Field(default=30, gt=0, le=240)


class PlazaChangeIn(BaseModel):
    entry: str = Field(min_length=1, max_length=16)
    etc_lanes: int | None = Field(default=None, ge=0, le=20)
    manual_lanes: int | None = Field(default=None, ge=0, le=20)


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    horizon_min: float = Field(default=60, ge=5, le=240)
    closures: list[ClosureIn] = Field(default_factory=list, max_length=10)
    demand_factor: float = Field(default=1.0, ge=0.3, le=2.0)
    metering: bool | None = None
    reopen_after_min: float | None = Field(default=None, ge=0, le=240)
    plaza_changes: list[PlazaChangeIn] = Field(default_factory=list, max_length=10)


class ConfirmClosureIn(BaseModel):
    cell: str = Field(pattern=r"^C\d{2,3}$")
    lanes_closed: int = Field(ge=0, le=8)


class MeteringIn(BaseModel):
    enabled: bool


class SizingIn(BaseModel):
    arrival_vph: float = Field(gt=0, le=20000)
    service_vphpl: float = Field(gt=0, le=3000)
    target_wait_s: float = Field(gt=0, le=3600)


class Runner:
    """Demo mode: drives a synthetic road and the twin in a background thread."""

    def __init__(self, twin: DigitalTwin, road: SyntheticRoad, speed: float, start_s: float, end_s: float) -> None:
        self.twin, self.road, self.speed = twin, road, speed
        self.start_s, self.end_s = start_s, end_s
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="itwin-demo", daemon=True)

    def start(self) -> None:
        with self.twin.lock:
            run_closed_loop(self.twin, self.road, self.start_s)  # warm up to the start time
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        wait = self.road.interval_s / max(self.speed, 0.1)
        while not self._stop.wait(wait):
            with self.twin.lock:
                if self.road.state.time_s >= self.end_s:
                    continue  # hold at the end of the demo window
                run_closed_loop(self.twin, self.road, self.road.state.time_s + self.road.interval_s)


def create_app(twin: DigitalTwin, keys: KeyStore, runner: Runner | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if runner is not None:
            runner.start()
        yield
        if runner is not None:
            runner.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="Intelligent Transportation Digital Twin",
        version=__version__,
        description="Expressway corridor twin: live state from detectors, incident alarms, ramp metering and what-if scenarios.",
    )
    counters: dict[str, int] = {}

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        response = await call_next(request)
        ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["x-request-id"] = rid[:64]
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["cache-control"] = "no-store"
        response.headers["referrer-policy"] = "no-referrer"
        key = f"{request.method} {response.status_code}"
        counters[key] = counters.get(key, 0) + 1
        log.info(
            json.dumps({"rid": rid[:64], "method": request.method, "path": request.url.path, "status": response.status_code, "ms": ms})
        )
        return response

    @app.exception_handler(NetworkError)
    async def network_error(_: Request, exc: NetworkError):
        return JSONResponse({"error": "NetworkError", "detail": str(exc)}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse({"error": "ValueError", "detail": str(exc)}, status_code=422)

    def require(minimum: str):
        level = ROLES.index(minimum)

        def dep(x_api_key: str | None = Header(default=None)) -> str:
            role = keys.role(x_api_key)
            if role is None:
                raise HTTPException(401, "missing or invalid x-api-key")
            if ROLES.index(role) < level:
                raise HTTPException(403, f"role '{role}' cannot do this; needs '{minimum}'")
            return role

        return dep

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        ok = twin.state is not None and twin.net.n_cells > 0
        return JSONResponse({"ready": ok, "clock": twin.status()["clock"]}, status_code=200 if ok else 503)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        s = twin.status()
        lines = [
            "# TYPE itwin_travel_time_minutes gauge",
            f"itwin_travel_time_minutes {s['travel_time_min']}",
            "# TYPE itwin_model_time_seconds gauge",
            f"itwin_model_time_seconds {s['time_s']}",
            "# TYPE itwin_entry_queue_vehicles gauge",
            *[f'itwin_entry_queue_vehicles{{entry="{e["id"]}"}} {e["queue_veh"]}' for e in s["entries"]],
            "# TYPE itwin_sensor_ok gauge",
            *[f'itwin_sensor_ok{{detector="{d["id"]}"}} {1 if d["status"] == "ok" else 0}' for d in s["sensors"]],
            "# TYPE itwin_incident_alarms_active gauge",
            f"itwin_incident_alarms_active {s['active_incident_alarms']}",
            "# TYPE itwin_http_requests_total counter",
            *[f'itwin_http_requests_total{{method="{k.split()[0]}",status="{k.split()[1]}"}} {v}' for k, v in sorted(counters.items())],
        ]
        return "\n".join(lines) + "\n"

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        html = resources.files("itwin").joinpath("dashboard/index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            headers={
                "content-security-policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            },
        )

    @app.get("/v1/whoami")
    def whoami(role: str = Depends(require("viewer"))) -> dict:
        return {"role": role}

    @app.get("/v1/network")
    def network(_: str = Depends(require("viewer"))) -> dict:
        n = twin.net
        return {
            "name": n.name,
            "length_km": round(n.length_km, 2),
            "step_seconds": n.step_seconds,
            "cells": [
                {
                    "id": c.id,
                    "km": round(n.position_km(i), 2),
                    "lanes": c.lanes,
                    "length_km": c.length_km,
                    "free_flow_kmh": c.free_flow_kmh,
                    "capacity_vph": c.capacity_vph,
                }
                for i, c in enumerate(n.cells)
            ],
            "entries": [
                {
                    "id": e.id,
                    "name": e.name,
                    "km": round(n.position_km(e.boundary), 2),
                    "meterable": e.meterable,
                    "ramp_capacity_vph": e.ramp_capacity_vph,
                    "plaza": None
                    if e.plaza is None
                    else {"etc_lanes": e.plaza.etc_lanes, "manual_lanes": e.plaza.manual_lanes, "etc_share": e.plaza.etc_share},
                }
                for e in n.entries
            ],
            "exits": [
                {"id": x.id, "name": x.name, "km": round(n.position_km(x.boundary), 2), "split_ratio": x.split_ratio} for x in n.exits
            ],
            "detectors": [{"id": d.id, "cell": d.cell} for d in n.detectors],
        }

    @app.get("/v1/state")
    def state(_: str = Depends(require("viewer"))) -> dict:
        return twin.status()

    @app.get("/v1/history")
    def history(_: str = Depends(require("viewer"))) -> dict:
        with twin.lock:
            return {"points": list(twin.history)}

    @app.get("/v1/alerts")
    def alerts(_: str = Depends(require("viewer"))) -> dict:
        with twin.lock:
            return {"alerts": list(twin.alerts)}

    @app.post("/v1/readings")
    def readings(body: ReadingsIn, _: str = Depends(require("operator"))) -> dict:
        if runner is not None:
            raise HTTPException(409, "the server runs in demo mode with its own synthetic feed")
        rs = [Reading(r.detector_id, r.timestamp_s, r.flow_vph, r.speed_kmh, r.occupancy_pct) for r in body.readings]
        known = {e.id for e in twin.net.entries}
        unknown = set(body.plaza_counts_vph) - known
        if unknown:
            raise HTTPException(422, f"unknown entries in plaza_counts_vph: {sorted(unknown)}")
        if body.now_s is not None and body.now_s < twin.state.time_s:
            raise HTTPException(409, "now_s is earlier than the twin clock; readings must move forward in time")
        return twin.ingest(rs, body.plaza_counts_vph, body.now_s)

    @app.post("/v1/metering")
    def metering(body: MeteringIn, role: str = Depends(require("operator"))) -> dict:
        twin.set_metering(body.enabled)
        with twin.lock:
            twin.alerts.appendleft(
                {
                    "time": twin.status()["clock"],
                    "kind": "operator",
                    "message": f"Ramp metering switched {'on' if body.enabled else 'off'} by {role}.",
                }
            )
        return {"metering_enabled": body.enabled}

    @app.post("/v1/closures")
    def confirm_closure(body: ConfirmClosureIn, role: str = Depends(require("operator"))) -> dict:
        twin.confirm_closure(body.cell, body.lanes_closed, by=role)
        return {"confirmed_closures": twin.closures()}

    @app.post("/v1/what-if")
    def what_if(body: ScenarioIn, _: str = Depends(require("planner"))) -> dict:
        sc = Scenario(
            name=body.name,
            horizon_min=body.horizon_min,
            closures=tuple(Closure(c.cell, c.lanes_closed, c.start_min, c.duration_min) for c in body.closures),
            demand_factor=body.demand_factor,
            metering=body.metering,
            reopen_after_min=body.reopen_after_min,
            plaza_changes=tuple(PlazaChange(p.entry, p.etc_lanes, p.manual_lanes) for p in body.plaza_changes),
        )
        return twin.what_if(sc)

    @app.post("/v1/plaza/sizing")
    def sizing(body: SizingIn, _: str = Depends(require("planner"))) -> dict:
        r = lanes_needed(body.arrival_vph, body.service_vphpl, body.target_wait_s)
        return {
            "lanes": r.lanes,
            "utilisation": round(r.utilisation, 3),
            "p_wait": round(r.p_wait, 3),
            "mean_wait_s": round(r.mean_wait_s, 1),
            "model": "M/M/c (Erlang C), steady state",
        }

    return app
