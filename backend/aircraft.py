"""Aircraft model and departure state machine.

Internals are SI (m, m/s, s). Snapshots convert to knots / feet for display.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .airport import Path

if TYPE_CHECKING:
    from .simulation import Simulation

KT = 0.514444  # m/s per knot
FT = 0.3048    # m per foot


class State(str, Enum):
    PARKED = "PARKED"
    TAXIING = "TAXIING"
    HOLDING_SHORT = "HOLDING_SHORT"
    LINE_UP = "LINE_UP"
    TAKEOFF_ROLL = "TAKEOFF_ROLL"
    AIRBORNE = "AIRBORNE"
    EXITED = "EXITED"


ON_GROUND = {State.PARKED, State.TAXIING, State.HOLDING_SHORT, State.LINE_UP, State.TAKEOFF_ROLL}


class Clearance(str, Enum):
    CLEARED_TAKEOFF = "CLEARED_TAKEOFF"


@dataclass(frozen=True)
class Performance:
    """One generic medium jet. Stage 2 can add per-type profiles (wake category etc.)."""
    type_code: str = "A320"
    taxi_speed: float = 18 * KT
    lineup_speed: float = 8 * KT
    ground_accel: float = 0.6        # m/s^2
    ground_decel: float = 1.2        # m/s^2
    takeoff_accel: float = 2.0       # m/s^2
    rotate_speed: float = 140 * KT
    climb_speed: float = 180 * KT
    climb_accel: float = 1.0         # m/s^2
    climb_rate: float = 2000 * FT / 60  # m/s (2000 ft/min)
    ready_delay: float = 5.0         # s parked before requesting taxi
    follow_distance: float = 70.0    # m, minimum nose-to-nose spacing while taxiing


class Aircraft:
    def __init__(self, callsign: str, gate: str, x: float, y: float, seq: int,
                 perf: Performance | None = None):
        self.callsign = callsign
        self.gate = gate
        self.seq = seq                      # spawn order; used as priority tie-breaker
        self.perf = perf or Performance()
        self.state = State.PARKED
        self.x, self.y = x, y
        self.heading = 0.0                  # deg, 0 = north
        self.speed = 0.0                    # ground speed, m/s
        self.alt = 0.0                      # m above field
        self.path: Path | None = None
        self.s = 0.0                        # distance travelled along current path, m
        self.timer = self.perf.ready_delay
        self.clearance: Clearance | None = None
        self.runway_vacated = False

    # --- tower interface ---------------------------------------------------

    def receive(self, clearance: Clearance) -> None:
        self.clearance = clearance

    @property
    def occupies_runway(self) -> bool:
        if self.state in (State.LINE_UP, State.TAKEOFF_ROLL):
            return True
        return self.state is State.AIRBORNE and not self.runway_vacated

    # --- state machine -----------------------------------------------------

    def step(self, dt: float, sim: "Simulation", leader_gap: float | None) -> None:
        if self.state is State.PARKED:
            self.timer -= dt
            if self.timer <= 0:
                route = sim.tower.request_taxi(self)
                self._set_path(sim.airport.path(route))
                self._transition(sim, State.TAXIING)

        elif self.state is State.TAXIING:
            arrived = self._advance(dt, self.perf.taxi_speed, self.perf.ground_accel,
                                    self.perf.ground_decel, stop_at_end=True, leader_gap=leader_gap)
            if arrived:
                self._transition(sim, State.HOLDING_SHORT)
                sim.tower.request_takeoff(self)

        elif self.state is State.HOLDING_SHORT:
            if self.clearance is Clearance.CLEARED_TAKEOFF:
                self._set_path(sim.airport.path(sim.airport.runway["lineup_route"]))
                self._transition(sim, State.LINE_UP)

        elif self.state is State.LINE_UP:
            arrived = self._advance(dt, self.perf.lineup_speed, self.perf.ground_accel,
                                    self.perf.ground_decel, stop_at_end=True)
            if arrived:
                self._set_path(sim.airport.path(sim.airport.runway["takeoff_route"]))
                self.heading = sim.airport.runway_heading
                self._transition(sim, State.TAKEOFF_ROLL)

        elif self.state is State.TAKEOFF_ROLL:
            self._advance(dt, self.perf.rotate_speed * 1.2, self.perf.takeoff_accel,
                          self.perf.ground_decel, stop_at_end=False)
            if self.speed >= self.perf.rotate_speed or self.s >= self.path.length:
                self._transition(sim, State.AIRBORNE)

        elif self.state is State.AIRBORNE:
            self._fly_straight(dt, sim)
            if not self.runway_vacated and sim.airport.along_runway(self.x, self.y) >= sim.airport.runway_length:
                self.runway_vacated = True
                sim.tower.runway_vacated(self)
            if math.hypot(self.x, self.y) > sim.airport.exit_radius_m:
                self._transition(sim, State.EXITED)

    # --- motion ------------------------------------------------------------

    def _set_path(self, path: Path) -> None:
        self.path = path
        self.s = 0.0

    def _advance(self, dt: float, v_target: float, accel: float, decel: float,
                 stop_at_end: bool, leader_gap: float | None = None) -> bool:
        """Move along the current path. Returns True once stopped at the path end."""
        assert self.path is not None
        remaining = self.path.length - self.s
        v_limit = v_target
        # v = sqrt(2 a d): the fastest speed from which we can still stop within d
        if stop_at_end:
            v_limit = min(v_limit, math.sqrt(2 * decel * max(0.0, remaining)))
        if leader_gap is not None:
            v_limit = min(v_limit, math.sqrt(2 * decel * max(0.0, leader_gap)))

        if self.speed < v_limit:
            self.speed = min(v_limit, self.speed + accel * dt)
        else:
            self.speed = max(v_limit, self.speed - decel * dt)

        step = self.speed * dt
        if leader_gap is not None:
            step = min(step, max(0.0, leader_gap))
        self.s = min(self.path.length, self.s + step)

        self.x, self.y, self.heading = self.path.pose_at(self.s)

        if stop_at_end and self.path.length - self.s < 0.5:
            self.s, self.speed = self.path.length, 0.0
            self.x, self.y, _ = self.path.pose_at(self.s)
            return True
        return False

    def _fly_straight(self, dt: float, sim: "Simulation") -> None:
        self.heading = sim.airport.runway_heading
        self.speed = min(self.perf.climb_speed, self.speed + self.perf.climb_accel * dt)
        self.alt += self.perf.climb_rate * dt
        ux, uy = sim.airport.runway_dir
        self.x += ux * self.speed * dt
        self.y += uy * self.speed * dt

    def _transition(self, sim: "Simulation", new: State) -> None:
        old, self.state = self.state, new
        sim.log(self.callsign, f"{old.value} → {new.value}", kind="state")

    # --- output ------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "callsign": self.callsign,
            "type": self.perf.type_code,
            "state": self.state.value,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "heading": round(self.heading, 1),
            "gs_kt": round(self.speed / KT),
            "alt_ft": round(self.alt / FT),
            "gate": self.gate,
            "route": self.path.node_ids if self.path else [],
            "clearance": self.clearance.value if self.clearance else None,
        }
