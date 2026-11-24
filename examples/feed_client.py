"""Push detector readings and plaza counts into a twin running in feed mode.

    ITWIN_MODE=feed ITWIN_API_KEYS=operator:<key> itwin serve
    ITWIN_KEY=<key> python examples/feed_client.py

In production this role belongs to a gateway that reads the detector and toll systems
(for example through DATEX II or vendor APIs) and posts every interval. Here a
synthetic road stands in for them. Only the standard library is used.
"""

from __future__ import annotations

import json
import os
import urllib.request

from itwin.network import load_network
from itwin.twin import SyntheticRoad

URL = os.environ.get("ITWIN_URL", "http://127.0.0.1:8080")
KEY = os.environ["ITWIN_KEY"]


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode(), method="POST", headers={"content-type": "application/json", "x-api-key": KEY}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def main() -> None:
    road = SyntheticRoad(load_network())
    for _ in range(int(os.environ.get("ITWIN_INTERVALS", "120"))):  # default: one hour of 30-second intervals
        readings, counts = road.advance()
        body = {"readings": [r.__dict__ for r in readings], "plaza_counts_vph": counts, "now_s": road.state.time_s}
        out = post("/v1/readings", body)
        print(out["time"], "accepted", out["accepted"], "rejected", out["rejected"], *out["alarms"])


if __name__ == "__main__":
    main()
