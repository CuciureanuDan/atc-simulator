"""Airport graph and path geometry.

All coordinates are in a local metric frame: x = east, y = north, meters.
Headings follow aviation convention: 0 = north, 90 = east, clockwise.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path as FsPath


def heading_deg(dx: float, dy: float) -> float:
    """Aviation heading of a vector in the (east, north) frame."""
    return (90.0 - math.degrees(math.atan2(dy, dx))) % 360.0


@dataclass(frozen=True)
class Node:
    id: str
    x: float
    y: float
    type: str


class Path:
    """A polyline through graph nodes, addressed by distance s from its start."""

    def __init__(self, node_ids: list[str], points: list[tuple[float, float]]):
        if len(points) < 2:
            raise ValueError("a path needs at least two points")
        self.node_ids = node_ids
        self.points = points
        self.seg_lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        self.length = sum(self.seg_lengths)

    def _locate(self, s: float) -> tuple[int, float]:
        """Segment index and distance into that segment for path distance s."""
        s = max(0.0, min(s, self.length))
        for i, seg in enumerate(self.seg_lengths):
            if s <= seg or i == len(self.seg_lengths) - 1:
                return i, min(s, seg)
            s -= seg
        raise AssertionError("unreachable")

    def pose_at(self, s: float) -> tuple[float, float, float]:
        """(x, y, heading) at path distance s."""
        i, d = self._locate(s)
        (x0, y0), (x1, y1) = self.points[i], self.points[i + 1]
        seg = self.seg_lengths[i]
        t = d / seg if seg else 0.0
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, heading_deg(x1 - x0, y1 - y0)

    def distance_ahead(self, s_from: float, x: float, y: float, max_lateral: float) -> float | None:
        """Path distance from s_from to the point (x, y), if (x, y) lies on the
        remaining path (within max_lateral meters of it). None otherwise."""
        start = 0.0
        best: float | None = None
        for (x0, y0), (x1, y1), seg in zip(self.points, self.points[1:], self.seg_lengths):
            end = start + seg
            if end > s_from and seg > 0:
                ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
                along = (x - x0) * ux + (y - y0) * uy
                if 0.0 <= along <= seg:
                    lateral = abs((x - x0) * uy - (y - y0) * ux)
                    s_point = start + along
                    if lateral <= max_lateral and s_point > s_from + 1.0:
                        d = s_point - s_from
                        best = d if best is None else min(best, d)
            start = end
        return best


class Airport:
    def __init__(self, raw: dict):
        self.raw = raw
        self.name: str = raw["name"]
        self.nodes: dict[str, Node] = {
            nid: Node(nid, float(n["x"]), float(n["y"]), n["type"]) for nid, n in raw["nodes"].items()
        }
        self.edges: list[dict] = raw["edges"]
        self.runway: dict = raw["runway"]
        self.departure_routes: dict[str, list[str]] = raw["departure_routes"]
        self.exit_radius_m: float = float(raw.get("exit_radius_m", 6000))
        self._validate()

        thr, end = self.nodes[self.runway["threshold"]], self.nodes[self.runway["end"]]
        self.runway_length = math.dist((thr.x, thr.y), (end.x, end.y))
        self.runway_heading = heading_deg(end.x - thr.x, end.y - thr.y)
        self.runway_dir = ((end.x - thr.x) / self.runway_length, (end.y - thr.y) / self.runway_length)
        self.runway_threshold = (thr.x, thr.y)

    @classmethod
    def load(cls, path: str | FsPath) -> "Airport":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def _validate(self) -> None:
        edge_set = {frozenset((e["from"], e["to"])) for e in self.edges}
        for e in self.edges:
            for nid in (e["from"], e["to"]):
                if nid not in self.nodes:
                    raise ValueError(f"edge references unknown node {nid}")
        routes = dict(self.departure_routes)
        routes["lineup"] = self.runway["lineup_route"]
        routes["takeoff"] = self.runway["takeoff_route"]
        for name, route in routes.items():
            for a, b in zip(route, route[1:]):
                if frozenset((a, b)) not in edge_set:
                    raise ValueError(f"route {name}: no edge between {a} and {b}")

    @property
    def gates(self) -> list[str]:
        return [nid for nid, n in self.nodes.items() if n.type == "gate"]

    def path(self, node_ids: list[str]) -> Path:
        return Path(node_ids, [(self.nodes[n].x, self.nodes[n].y) for n in node_ids])

    def along_runway(self, x: float, y: float) -> float:
        """Distance past the threshold, measured along the runway axis."""
        tx, ty = self.runway_threshold
        return (x - tx) * self.runway_dir[0] + (y - ty) * self.runway_dir[1]

    def to_dict(self) -> dict:
        """Static layout sent to clients once, on connect."""
        return {
            "name": self.name,
            "nodes": {nid: {"x": n.x, "y": n.y, "type": n.type} for nid, n in self.nodes.items()},
            "edges": self.edges,
            "runway": {
                "id": self.runway["id"],
                "width_m": self.runway["width_m"],
                "threshold": self.runway["threshold"],
                "end": self.runway["end"],
                "holding_point": self.runway["holding_point"],
            },
        }
