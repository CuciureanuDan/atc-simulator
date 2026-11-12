"""Tower controller, stage 1: the runway is an exclusive resource.

The simulation runs in a single asyncio loop, so there is no real concurrency
here: the "lock" is an explicit owner plus a FIFO queue, mutated only between
ticks. That keeps the logic deterministic and testable.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Callable

from .aircraft import Aircraft, Clearance

if TYPE_CHECKING:
    from .airport import Airport


class Tower:
    def __init__(self, airport: "Airport", log: Callable[..., None]):
        self.airport = airport
        self.log = log
        self.rwy = airport.runway["id"].split("/")[0]   # departures use RWY 09
        self.hp = airport.runway["holding_point"]
        self.runway_owner: str | None = None
        self.queue: deque[str] = deque()

    # --- requests from aircraft ---------------------------------------------

    def request_taxi(self, ac: Aircraft) -> list[str]:
        """Stage 1: always granted, route is the fixed one for the gate."""
        route = self.airport.departure_routes[ac.gate]
        via = " ".join(route[1:-1])
        self.log(ac.callsign, f"Taxi to {self.hp} via {via}, hold short runway {self.rwy}")
        return route

    def request_takeoff(self, ac: Aircraft) -> None:
        self.queue.append(ac.callsign)
        if self.runway_owner is not None or len(self.queue) > 1:
            self.log(ac.callsign, f"Hold short runway {self.rwy}, number {len(self.queue)} for departure")

    def runway_vacated(self, ac: Aircraft) -> None:
        if self.runway_owner == ac.callsign:
            self.runway_owner = None
            self.log(ac.callsign, f"Runway {self.rwy} vacated", kind="info")

    # --- per-tick decision ----------------------------------------------------

    def update(self, aircraft: dict[str, Aircraft]) -> None:
        # Drop queue entries for aircraft that no longer exist.
        while self.queue and self.queue[0] not in aircraft:
            self.queue.popleft()
        if self.runway_owner is None and self.queue:
            cs = self.queue.popleft()
            self.runway_owner = cs
            aircraft[cs].receive(Clearance.CLEARED_TAKEOFF)
            self.log(cs, f"Runway {self.rwy}, cleared for takeoff")

    def to_dict(self) -> dict:
        return {"id": self.airport.runway["id"], "occupied_by": self.runway_owner, "queue": list(self.queue)}
