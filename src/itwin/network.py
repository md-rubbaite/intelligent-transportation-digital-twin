"""Corridor network: mainline cells, entries with toll plazas, exits, detectors.

The network is a single directional expressway corridor split into cells. Boundaries
are numbered 0..N: boundary 0 is the upstream end, boundary N the downstream end,
boundary b sits between cell b-1 and cell b. Entries (on-ramps, including the
upstream mainline entry at boundary 0) and exits (off-ramps) attach to boundaries.

All quantities use hours and kilometres: densities in veh/km, flows in veh/h.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


class NetworkError(ValueError):
    """Raised when a network file is invalid."""


@dataclass(frozen=True)
class CellSpec:
    id: str
    length_km: float
    lanes: int
    free_flow_kmh: float
    capacity_vphpl: float
    jam_density_vpkpl: float

    @property
    def capacity_vph(self) -> float:
        return self.lanes * self.capacity_vphpl

    @property
    def jam_density(self) -> float:
        return self.lanes * self.jam_density_vpkpl

    @property
    def critical_density(self) -> float:
        return self.capacity_vph / self.free_flow_kmh

    @property
    def wave_speed_kmh(self) -> float:
        """Backward wave speed of the triangular fundamental diagram."""
        return self.capacity_vphpl / (self.jam_density_vpkpl - self.capacity_vphpl / self.free_flow_kmh)


@dataclass(frozen=True)
class PlazaSpec:
    """Toll plaza at an entry. ETC and manual lanes have separate queues."""

    etc_lanes: int
    manual_lanes: int
    etc_service_vphpl: float
    manual_service_vphpl: float
    etc_share: float

    @property
    def etc_capacity_vph(self) -> float:
        return self.etc_lanes * self.etc_service_vphpl

    @property
    def manual_capacity_vph(self) -> float:
        return self.manual_lanes * self.manual_service_vphpl


@dataclass(frozen=True)
class EntrySpec:
    id: str
    name: str
    boundary: int
    ramp_capacity_vph: float
    plaza: PlazaSpec | None
    meterable: bool
    max_queue_veh: float
    merge_priority: float  # share of the merge capacity reserved for the ramp when both sides are congested


@dataclass(frozen=True)
class ExitSpec:
    id: str
    name: str
    boundary: int
    split_ratio: float
    capacity_vph: float


@dataclass(frozen=True)
class DetectorSpec:
    id: str
    cell: str


@dataclass(frozen=True)
class Network:
    name: str
    cells: tuple[CellSpec, ...]
    entries: tuple[EntrySpec, ...]
    exits: tuple[ExitSpec, ...]
    detectors: tuple[DetectorSpec, ...]
    downstream_capacity_vph: float
    capacity_drop: float
    step_seconds: float
    _cell_index: dict[str, int] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_cell_index", {c.id: i for i, c in enumerate(self.cells)})

    @property
    def n_cells(self) -> int:
        return len(self.cells)

    @property
    def length_km(self) -> float:
        return sum(c.length_km for c in self.cells)

    def cell_index(self, cell_id: str) -> int:
        try:
            return self._cell_index[cell_id]
        except KeyError as exc:
            raise NetworkError(f"unknown cell '{cell_id}'") from exc

    def entry(self, entry_id: str) -> EntrySpec:
        for e in self.entries:
            if e.id == entry_id:
                return e
        raise NetworkError(f"unknown entry '{entry_id}'")

    def entry_at(self, boundary: int) -> EntrySpec | None:
        for e in self.entries:
            if e.boundary == boundary:
                return e
        return None

    def exit_at(self, boundary: int) -> ExitSpec | None:
        for x in self.exits:
            if x.boundary == boundary:
                return x
        return None

    def position_km(self, boundary: int) -> float:
        return sum(c.length_km for c in self.cells[:boundary])

    def free_flow_travel_time_min(self) -> float:
        return sum(c.length_km / c.free_flow_kmh for c in self.cells) * 60.0


_CELL_KEYS = {"count", "length_km", "lanes", "free_flow_kmh", "capacity_vphpl", "jam_density_vpkpl", "lanes_override"}
_ENTRY_KEYS = {"id", "name", "boundary", "ramp_capacity_vph", "plaza", "meterable", "max_queue_veh", "merge_priority"}
_PLAZA_KEYS = {"etc_lanes", "manual_lanes", "etc_service_vphpl", "manual_service_vphpl", "etc_share"}
_EXIT_KEYS = {"id", "name", "boundary", "split_ratio", "capacity_vph"}
_TOP_KEYS = {"name", "step_seconds", "downstream_capacity_vph", "capacity_drop", "cells", "entries", "exits", "detectors"}


def _num(d: dict, key: str, where: str, lo: float, hi: float, default: float | None = None) -> float:
    if key not in d:
        if default is None:
            raise NetworkError(f"{where}: missing '{key}'")
        return default
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise NetworkError(f"{where}: '{key}' must be a number")
    if not lo <= v <= hi:
        raise NetworkError(f"{where}: '{key}'={v} outside [{lo}, {hi}]")
    return float(v)


def _unknown(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise NetworkError(f"{where}: unknown keys {sorted(extra)}")


def parse_network(text: str) -> Network:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise NetworkError(f"invalid TOML: {exc}") from exc
    _unknown(raw, _TOP_KEYS, "network")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise NetworkError("network: 'name' is required")
    step = _num(raw, "step_seconds", "network", 1, 60, 10)

    c = raw.get("cells")
    if not isinstance(c, dict):
        raise NetworkError("network: [cells] table is required")
    _unknown(c, _CELL_KEYS, "cells")
    count = int(_num(c, "count", "cells", 2, 500))
    length = _num(c, "length_km", "cells", 0.05, 5)
    lanes = int(_num(c, "lanes", "cells", 1, 8))
    vf = _num(c, "free_flow_kmh", "cells", 20, 140)
    q = _num(c, "capacity_vphpl", "cells", 500, 2600)
    kj = _num(c, "jam_density_vpkpl", "cells", 80, 250)
    if kj <= q / vf * 1.5:
        raise NetworkError("cells: jam density must be well above the critical density")
    overrides = c.get("lanes_override", {})
    if not isinstance(overrides, dict):
        raise NetworkError("cells: 'lanes_override' must be a table of index = lanes")
    cells = []
    for i in range(count):
        n = lanes
        if str(i) in overrides:
            n = int(_num(overrides, str(i), "cells.lanes_override", 1, 8))
        cells.append(CellSpec(f"C{i:02d}", length, n, vf, q, kj))
    bad = [k for k in overrides if not k.isdigit() or int(k) >= count]
    if bad:
        raise NetworkError(f"cells.lanes_override: invalid cell indexes {bad}")
    if step / 3600 * vf > length + 1e-9:
        raise NetworkError(f"step_seconds={step} violates the CFL condition (free-flow travel per step exceeds the cell length)")

    entries = []
    for i, e in enumerate(raw.get("entries", [])):
        where = f"entries[{i}]"
        _unknown(e, _ENTRY_KEYS, where)
        eid, ename = e.get("id"), e.get("name", e.get("id"))
        if not isinstance(eid, str) or not eid:
            raise NetworkError(f"{where}: 'id' is required")
        b = int(_num(e, "boundary", where, 0, count - 1))
        plaza = None
        if "plaza" in e:
            p = e["plaza"]
            _unknown(p, _PLAZA_KEYS, where + ".plaza")
            plaza = PlazaSpec(
                etc_lanes=int(_num(p, "etc_lanes", where + ".plaza", 0, 20)),
                manual_lanes=int(_num(p, "manual_lanes", where + ".plaza", 0, 20)),
                etc_service_vphpl=_num(p, "etc_service_vphpl", where + ".plaza", 100, 2500, 1000),
                manual_service_vphpl=_num(p, "manual_service_vphpl", where + ".plaza", 50, 1200, 350),
                etc_share=_num(p, "etc_share", where + ".plaza", 0, 1),
            )
            if plaza.etc_lanes + plaza.manual_lanes == 0:
                raise NetworkError(f"{where}.plaza: at least one lane is required")
            if plaza.etc_share > 0 and plaza.etc_lanes == 0:
                raise NetworkError(f"{where}.plaza: etc_share > 0 but no ETC lanes")
            if plaza.etc_share < 1 and plaza.manual_lanes == 0:
                raise NetworkError(f"{where}.plaza: manual traffic but no manual lanes")
        entries.append(
            EntrySpec(
                id=eid,
                name=str(ename),
                boundary=b,
                ramp_capacity_vph=_num(e, "ramp_capacity_vph", where, 200, 10000),
                plaza=plaza,
                meterable=bool(e.get("meterable", False)),
                max_queue_veh=_num(e, "max_queue_veh", where, 5, 5000, 120),
                merge_priority=_num(e, "merge_priority", where, 0.05, 0.95, 0.3),
            )
        )
    if not any(e.boundary == 0 for e in entries):
        raise NetworkError("entries: an entry at boundary 0 (upstream mainline) is required")

    exits = []
    for i, x in enumerate(raw.get("exits", [])):
        where = f"exits[{i}]"
        _unknown(x, _EXIT_KEYS, where)
        xid = x.get("id")
        if not isinstance(xid, str) or not xid:
            raise NetworkError(f"{where}: 'id' is required")
        exits.append(
            ExitSpec(
                id=xid,
                name=str(x.get("name", xid)),
                boundary=int(_num(x, "boundary", where, 1, count - 1)),
                split_ratio=_num(x, "split_ratio", where, 0.0, 0.6),
                capacity_vph=_num(x, "capacity_vph", where, 200, 10000, 1800),
            )
        )

    ids = [e.id for e in entries] + [x.id for x in exits]
    if len(ids) != len(set(ids)):
        raise NetworkError("entry and exit ids must be unique")
    for group, label in ((entries, "entries"), (exits, "exits")):
        bs = [g.boundary for g in group]
        if len(bs) != len(set(bs)):
            raise NetworkError(f"{label}: at most one per boundary")

    cell_ids = {cell.id for cell in cells}
    detectors = []
    for i, d in enumerate(raw.get("detectors", [])):
        if not isinstance(d, dict) or set(d) != {"id", "cell"}:
            raise NetworkError(f"detectors[{i}]: needs exactly 'id' and 'cell'")
        if d["cell"] not in cell_ids:
            raise NetworkError(f"detectors[{i}]: unknown cell '{d['cell']}'")
        detectors.append(DetectorSpec(str(d["id"]), d["cell"]))
    if len({d.id for d in detectors}) != len(detectors):
        raise NetworkError("detector ids must be unique")

    return Network(
        name=name.strip(),
        cells=tuple(cells),
        entries=tuple(sorted(entries, key=lambda e: e.boundary)),
        exits=tuple(sorted(exits, key=lambda x: x.boundary)),
        detectors=tuple(detectors),
        downstream_capacity_vph=_num(raw, "downstream_capacity_vph", "network", 500, 20000, cells[-1].capacity_vph),
        capacity_drop=_num(raw, "capacity_drop", "network", 0, 0.3, 0.08),
        step_seconds=step,
    )


def load_network(path: str | Path | None = None) -> Network:
    if path is None:
        text = resources.files("itwin").joinpath("default_network.toml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return parse_network(text)
