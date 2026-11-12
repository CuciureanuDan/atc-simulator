"""Simulation core.

step(dt) is pure and deterministic (no wall clock), so it can be driven by the
asyncio loop in production or by a plain for-loop in tests.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from itertools import count

from .aircraft import ON_GROUND, Aircraft, State
from .airport import Airport
from .tower import Tower

log = logging.getLogger("atc.sim")

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,8}$")
AIRLINES = ["ROT", "WZZ", "TVS", "DLH", "LOT", "AUA"]
GATE_CLEAR_DISTANCE = 80.0   # m an aircraft must have moved off a gate before it is reusable
LATERAL_TOLERANCE = 12.0     # m, how close to my path another aircraft must be to count as "ahead"
EVENTS_IN_SNAPSHOT = 30


class Simulation:
    def __init__(self, airport: Airport, tick_hz: int = 10, speed: int = 1):
        self.airport = airport
        self.dt = 1.0 / tick_hz          # fixed physics step, seconds of sim time
        self.speed = max(1, int(speed))  # physics steps per wall-clock tick
        self.t = 0.0
        self.aircraft: dict[str, Aircraft] = {}
        self.events: deque[dict] = deque(maxlen=200)
        self._event_ids = count(1)
        self._seq = count(1)
        self.tower = Tower(airport, self.log)
        self._snapshot: dict | None = None
        self._cond = asyncio.Condition()

    # --- logging ----------------------------------------------------------------

    def log(self, callsign: str, msg: str, kind: str = "atc") -> None:
        ev = {"id": next(self._event_ids), "t": round(self.t, 1), "callsign": callsign, "kind": kind, "msg": msg}
        self.events.append(ev)
        log.info("[%7.1f] %-7s %s", self.t, callsign or "-", msg)

    # --- commands -----------------------------------------------------------------

    def gate_is_free(self, gate: str) -> bool:
        for ac in self.aircraft.values():
            if ac.gate != gate:
                continue
            if ac.state is State.PARKED:
                return False
            if ac.state is State.TAXIING and ac.s < GATE_CLEAR_DISTANCE:
                return False
        return True

    def spawn(self, gate: str | None = None, callsign: str | None = None) -> Aircraft:
        if gate is None:
            gate = next((g for g in self.airport.gates if self.gate_is_free(g)), None)
            if gate is None:
                raise ValueError("all gates are occupied")
        elif gate not in self.airport.gates:
            raise ValueError(f"unknown gate {gate}")
        elif not self.gate_is_free(gate):
            raise ValueError(f"gate {gate} is occupied")

        seq = next(self._seq)
        if callsign is None:
            callsign = f"{AIRLINES[(seq - 1) % len(AIRLINES)]}{300 + seq}"
            while callsign in self.aircraft:
                callsign += "A"
        callsign = callsign.upper()
        if not CALLSIGN_RE.match(callsign):
            raise ValueError("callsign must be 3-8 letters or digits")
        if callsign in self.aircraft:
            raise ValueError(f"callsign {callsign} already in use")

        node = self.airport.nodes[gate]
        ac = Aircraft(callsign, gate, node.x, node.y, seq)
        # Point the parked aircraft toward its first taxi node.
        route = self.airport.departure_routes[gate]
        ac.heading = self.airport.path(route).pose_at(0)[2]
        self.aircraft[callsign] = ac
        self.log(callsign, f"Spawned at {gate}", kind="info")
        return ac

    def handle_command(self, msg: dict) -> dict:
        kind = msg.get("type")
        try:
            if kind == "spawn":
                ac = self.spawn(gate=msg.get("gate"), callsign=msg.get("callsign"))
                return {"type": "ack", "ok": True, "callsign": ac.callsign}
            raise ValueError(f"unknown command {kind!r}")
        except ValueError as e:
            self.log("", f"Command rejected: {e}", kind="error")
            return {"type": "ack", "ok": False, "error": str(e)}

    # --- physics -------------------------------------------------------------------

    def _leader_gaps(self) -> dict[str, float]:
        """For each taxiing aircraft: free distance to the aircraft ahead on its path."""
        ground = [a for a in self.aircraft.values() if a.state in ON_GROUND and a.state is not State.PARKED]
        leaders: dict[str, tuple[float, str]] = {}
        for ac in ground:
            if ac.state is not State.TAXIING or ac.path is None:
                continue
            best: tuple[float, str] | None = None
            for other in ground:
                if other is ac:
                    continue
                d = ac.path.distance_ahead(ac.s, other.x, other.y, LATERAL_TOLERANCE)
                if d is not None and (best is None or d < best[0]):
                    best = (d, other.callsign)
            if best:
                leaders[ac.callsign] = best

        # Two aircraft that each see the other ahead (meeting at a junction) would
        # wait forever. The earlier spawn keeps priority. Proper intersection
        # reservation comes in stage 2.
        for cs, (_, other) in list(leaders.items()):
            if cs in leaders and other in leaders and leaders[other][1] == cs:
                first = min(cs, other, key=lambda c: self.aircraft[c].seq)
                leaders.pop(first, None)

        return {cs: d - self.aircraft[cs].perf.follow_distance for cs, (d, _) in leaders.items()}

    def step(self, dt: float) -> None:
        self.t += dt
        gaps = self._leader_gaps()
        for ac in list(self.aircraft.values()):
            ac.step(dt, self, gaps.get(ac.callsign))
        self.tower.update(self.aircraft)
        for cs in [cs for cs, a in self.aircraft.items() if a.state is State.EXITED]:
            del self.aircraft[cs]

    # --- output --------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "t": round(self.t, 1),
            "aircraft": [a.to_dict() for a in self.aircraft.values()],
            "runway": self.tower.to_dict(),
            "events": list(self.events)[-EVENTS_IN_SNAPSHOT:],
        }

    # --- realtime loop ---------------------------------------------------------------

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_wall = loop.time()
        while True:
            for _ in range(self.speed):
                self.step(self.dt)
            self._snapshot = self.snapshot()
            async with self._cond:
                self._cond.notify_all()
            next_wall += self.dt
            delay = next_wall - loop.time()
            if delay < 0:            # fell behind (e.g. debugger pause): resync, don't burst
                next_wall, delay = loop.time(), 0.0
            await asyncio.sleep(delay)

    async def next_snapshot(self) -> dict:
        """Wait for the next tick. Slow clients just skip ticks; the loop never waits on them."""
        async with self._cond:
            await self._cond.wait()
        assert self._snapshot is not None
        return self._snapshot
