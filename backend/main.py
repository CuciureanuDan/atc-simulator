"""FastAPI entrypoint.

  uvicorn backend.main:app --port 8000

WebSocket /ws
  server -> client: {"type": "airport", ...} once, then {"type": "snapshot", ...} every tick
  client -> server: {"type": "spawn", "gate"?: "G2", "callsign"?: "ROT123"}
                    answered with {"type": "ack", "ok": bool, ...}
REST (handy for curl / scripting)
  GET  /api/state
  POST /api/spawn   body optional: {"gate": "G2", "callsign": "ROT123"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .airport import Airport
from .simulation import Simulation

ROOT = Path(__file__).resolve().parent.parent
AIRPORT_FILE = Path(os.getenv("AIRPORT_FILE", ROOT / "backend" / "data" / "airport.json"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")

sim = Simulation(
    Airport.load(AIRPORT_FILE),
    tick_hz=int(os.getenv("SIM_HZ", "10")),
    speed=int(os.getenv("SIM_SPEED", "1")),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(sim.run(), name="sim-loop")
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="ATC simulation", lifespan=lifespan)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json({"type": "airport", **sim.airport.to_dict()})

    async def sender() -> None:
        while True:
            await ws.send_json(await sim.next_snapshot())

    async def receiver() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await ws.send_json({"type": "ack", "ok": False, "error": "message must be a JSON object"})
                continue
            await ws.send_json(sim.handle_command(msg))

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, RuntimeError)):
                logging.getLogger("atc.ws").warning("websocket closed: %r", exc)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@app.get("/api/state")
def api_state() -> dict:
    return sim.snapshot()


@app.post("/api/spawn")
def api_spawn(body: dict | None = Body(default=None)) -> JSONResponse:
    result = sim.handle_command({"type": "spawn", **(body or {})})
    return JSONResponse(result, status_code=200 if result["ok"] else 409)


# Mounted last so /ws and /api/* match first.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
