# ATC simulation — etapa 1

Simulator de trafic la sol: plecări de la poartă până la decolare, pistă ca resursă exclusivă, afișaj tip radar de sol în browser.

## Rulare

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --port 8000          # http://localhost:8000
SIM_SPEED=4 uvicorn backend.main:app --port 8000   # 4x timp simulat
pytest -q
```

Variabile: `SIM_SPEED` (întreg, pași de fizică per tick), `SIM_HZ` (default 10), `AIRPORT_FILE`, `LOG_LEVEL`.

```bash
curl -s -X POST localhost:8000/api/spawn
curl -s -X POST localhost:8000/api/spawn -H 'content-type: application/json' -d '{"gate":"G2","callsign":"ROT123"}'
curl -s localhost:8000/api/state | jq
```

## Structură

```
backend/
  data/airport.json   graful aeroportului (noduri în metri, muchii, rute fixe)
  airport.py          încărcare graf, Path (polilinie parcursă după distanță)
  aircraft.py         Aircraft + FSM, profil de performanță
  tower.py            pista ca resursă exclusivă: owner + coadă FIFO
  simulation.py       step(dt) determinist, spawn, distanțare la rulare, snapshot
  main.py             FastAPI: /ws, /api/state, /api/spawn, frontend static
frontend/             Canvas + panou turn (fără build, fără dependențe)
tests/                acceptanța etapei 1
```

## Protocol WebSocket `/ws`

Server → client: `{"type":"airport", ...}` o dată la conectare, apoi `{"type":"snapshot", t, aircraft[], runway, events[]}` la fiecare tick.
Client → server: `{"type":"spawn", "gate"?: "G2", "callsign"?: "ROT123"}`, răspuns `{"type":"ack", "ok": bool, ...}`.

## FSM

`PARKED → TAXIING → HOLDING_SHORT → LINE_UP → TAKEOFF_ROLL → AIRBORNE → EXITED`

Pista se eliberează când avionul e în aer și a trecut de capătul pistei; abia atunci turnul dă clearance următorului din coadă.

## Limitări cunoscute (rezolvate în etapa 2)

- Rute fixe pe poartă; fără Dijkstra.
- Distanțarea la sol e doar „urmează avionul din față pe aceeași rută”. Două avioane care ajung simultan la aceeași intersecție din direcții diferite nu sunt gestionate corect; e nevoie de rezervare de segmente.
- Doar plecări, doar pista 09.
