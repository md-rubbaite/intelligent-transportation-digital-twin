"""Command line: ``itwin serve | demo | check-network | sizing``."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__
from .network import NetworkError, load_network
from .plaza import lanes_needed


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import KeyStore, Runner, create_app
    from .demo import INCIDENT
    from .twin import DigitalTwin, SyntheticRoad

    spec = os.environ.get("ITWIN_API_KEYS", "")
    try:
        keys = KeyStore(spec)
    except ValueError as exc:
        print(f"ITWIN_API_KEYS: {exc}. Example: viewer:<24+ chars>,planner:<...>,operator:<...>", file=sys.stderr)
        return 2
    net = load_network(os.environ.get("ITWIN_NETWORK") or None)
    mode = os.environ.get("ITWIN_MODE", "demo")
    twin = DigitalTwin(net)
    runner = None
    if mode == "demo":
        road = SyntheticRoad(net, incidents=[INCIDENT], detector_faults={"D10": "stuck"})
        runner = Runner(twin, road, speed=float(os.environ.get("ITWIN_SPEED", "20")), start_s=7.5 * 3600, end_s=10.5 * 3600)
    elif mode != "feed":
        print("ITWIN_MODE must be 'demo' or 'feed'", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app = create_app(twin, keys, runner)
    uvicorn.run(app, host=os.environ.get("ITWIN_HOST", "127.0.0.1"), port=int(os.environ.get("ITWIN_PORT", "8080")), log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itwin", description="Expressway corridor digital twin")
    p.add_argument("--version", action="version", version=f"itwin {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the API and operator console (configuration from ITWIN_* environment variables)")
    sub.add_parser("demo", help="scripted end-to-end scenario on synthetic data")
    c = sub.add_parser("check-network", help="validate a network TOML file")
    c.add_argument("path", nargs="?", help="network file (default: the bundled demo corridor)")
    s = sub.add_parser("sizing", help="toll lanes needed for a target mean wait (Erlang C)")
    s.add_argument("--arrivals", type=float, required=True, help="arrivals, veh/h")
    s.add_argument("--service", type=float, required=True, help="service rate per lane, veh/h")
    s.add_argument("--target-wait", type=float, default=60, help="target mean wait, seconds")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        return _serve(args)
    if args.cmd == "demo":
        from .demo import run

        r = run()
        return 0 if r["detection_delay_s"] is not None and abs(r["conservation"]) < 1e-6 else 1
    if args.cmd == "check-network":
        try:
            net = load_network(args.path)
        except (NetworkError, OSError) as exc:
            print(f"invalid: {exc}", file=sys.stderr)
            return 1
        print(
            f"ok: {net.name}: {net.length_km:.1f} km, {net.n_cells} cells, {len(net.entries)} entries, "
            f"{len(net.exits)} exits, {len(net.detectors)} detectors, step {net.step_seconds:g} s"
        )
        return 0
    if args.cmd == "sizing":
        try:
            r = lanes_needed(args.arrivals, args.service, args.target_wait)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"{r.lanes} lanes: utilisation {r.utilisation:.2f}, P(wait) {r.p_wait:.2f}, mean wait {r.mean_wait_s:.1f} s (M/M/c)")
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
