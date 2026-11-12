"use strict";

// World frame from the backend: x = east, y = north, meters.
// Screen frame: x = right, y = down, CSS pixels.

const PHASES = {
  PARKED:        { color: "--st-parked", label: "parked" },
  TAXIING:       { color: "--st-taxi",   label: "taxi" },
  HOLDING_SHORT: { color: "--st-hold",   label: "holding" },
  LINE_UP:       { color: "--st-runway", label: "line-up" },
  TAKEOFF_ROLL:  { color: "--st-runway", label: "takeoff" },
  AIRBORNE:      { color: "--st-air",    label: "climb" },
};
const CLIMB_OUT_MARGIN_M = 300;   // extra world space east of the runway end, to watch the climb-out
const TAXIWAY_WIDTH_M = 23;
const GATE_PAD_M = 60;

const $ = (id) => document.getElementById(id);
const canvas = $("scope");
const ctx = canvas.getContext("2d");
const root = getComputedStyle(document.documentElement);
const color = (name) => root.getPropertyValue(name).trim();
const C = {};
for (const n of ["--field", "--pavement", "--asphalt", "--marking", "--taxi-line", "--stop-bar",
                 "--muted", "--st-parked", "--st-taxi", "--st-hold", "--st-runway", "--st-air"]) {
  C[n] = color(n);
}
const FONT_DATA = root.getPropertyValue("--font-data").trim();

let airport = null;
let snap = null;
let view = { scale: 1, ox: 0, oy: 0, minX: 0, maxY: 0 };
let drawPending = false;

// --- geometry ---------------------------------------------------------------

function toScreen(x, y) {
  return [view.ox + (x - view.minX) * view.scale, view.oy + (view.maxY - y) * view.scale];
}

function computeView() {
  if (!airport) return;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const xs = Object.values(airport.nodes).map((n) => n.x);
  const ys = Object.values(airport.nodes).map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs) + CLIMB_OUT_MARGIN_M;
  const minY = Math.min(...ys) - GATE_PAD_M, maxY = Math.max(...ys) + 120;
  const pad = { l: 60, r: 40, t: 70, b: 60 };
  const scale = Math.min((w - pad.l - pad.r) / (maxX - minX), (h - pad.t - pad.b) / (maxY - minY));
  const usedW = (maxX - minX) * scale, usedH = (maxY - minY) * scale;
  view = {
    scale,
    minX,
    maxY,
    ox: pad.l + (w - pad.l - pad.r - usedW) / 2,
    oy: pad.t + (h - pad.t - pad.b - usedH) / 2,
  };
}

function toWorld(sx, sy) {
  return [view.minX + (sx - view.ox) / view.scale, view.maxY - (sy - view.oy) / view.scale];
}

function zoomAt(sx, sy, factor) {
  const [wx, wy] = toWorld(sx, sy);
  view.scale = Math.min(40, Math.max(0.05, view.scale * factor));
  view.ox = sx - (wx - view.minX) * view.scale;
  view.oy = sy - (view.maxY - wy) * view.scale;
  requestDraw();
}

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015));
}, { passive: false });

let drag = null;
canvas.addEventListener("pointerdown", (e) => {
  drag = { x: e.clientX, y: e.clientY };
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", (e) => {
  if (!drag) return;
  view.ox += e.clientX - drag.x;
  view.oy += e.clientY - drag.y;
  drag = { x: e.clientX, y: e.clientY };
  requestDraw();
});
canvas.addEventListener("pointerup", () => { drag = null; });
canvas.addEventListener("dblclick", () => { computeView(); requestDraw(); });

function resize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * dpr);
  canvas.height = Math.round(canvas.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  computeView();
  requestDraw();
}

// --- drawing ----------------------------------------------------------------

function requestDraw() {
  if (drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => { drawPending = false; draw(); });
}

function strokeEdges(kinds, width, style, cap = "round") {
  ctx.lineWidth = width;
  ctx.strokeStyle = style;
  ctx.lineCap = cap;
  ctx.lineJoin = "round";
  for (const e of airport.edges) {
    if (!kinds.includes(e.kind)) continue;
    const a = airport.nodes[e.from], b = airport.nodes[e.to];
    ctx.beginPath();
    ctx.moveTo(...toScreen(a.x, a.y));
    ctx.lineTo(...toScreen(b.x, b.y));
    ctx.stroke();
  }
}

function label(text, x, y, { font = `600 14px ${FONT_DATA}`, fill = C["--muted"], align = "center", rot = 0 } = {}) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.font = font;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillStyle = fill;
  ctx.fillText(text, 0, 0);
  ctx.restore();
}

function drawRunway() {
  const rw = airport.runway;
  const a = airport.nodes[rw.threshold], b = airport.nodes[rw.end];
  const [ax, ay] = toScreen(a.x, a.y), [bx, by] = toScreen(b.x, b.y);
  const width = Math.max(14, rw.width_m * view.scale);
  const ang = Math.atan2(by - ay, bx - ax);
  const len = Math.hypot(bx - ax, by - ay);

  ctx.save();
  ctx.translate(ax, ay);
  ctx.rotate(ang);
  ctx.fillStyle = C["--asphalt"];
  ctx.fillRect(-6, -width / 2, len + 12, width);

  ctx.strokeStyle = C["--marking"];
  ctx.lineWidth = 1;
  ctx.strokeRect(-6, -width / 2 + 1.5, len + 12, width - 3);

  // threshold piano keys at both ends
  const keys = 6, keyW = (width - 6) / (keys * 2 - 1);
  for (const x0 of [2, len - 14]) {
    for (let i = 0; i < keys; i++) {
      ctx.fillStyle = C["--marking"];
      ctx.fillRect(x0, -width / 2 + 3 + i * keyW * 2, 12, keyW);
    }
  }

  // dashed centreline
  ctx.setLineDash([14, 10]);
  ctx.beginPath();
  ctx.moveTo(40, 0);
  ctx.lineTo(len - 40, 0);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();

  // designators, oriented so that the top of the digits points along the landing direction
  const [d1, d2] = rw.id.split("/");
  const font = `700 ${Math.max(14, Math.min(22, width * 0.6))}px ${FONT_DATA}`;
  label(d1, ax - width * 0.9 - 6, ay, { font, fill: C["--marking"] });
  label(d2, bx + width * 0.9 + 6, by, { font, fill: C["--marking"] });
}

function drawStopBar() {
  const hpId = airport.runway.holding_point;
  const hp = airport.nodes[hpId];
  const into = airport.edges.find((e) => e.kind === "taxiway" && (e.to === hpId || e.from === hpId));
  if (!into) return;
  const other = airport.nodes[into.from === hpId ? into.to : into.from];
  const [hx, hy] = toScreen(hp.x, hp.y), [ox, oy] = toScreen(other.x, other.y);
  const len = Math.hypot(hx - ox, hy - oy) || 1;
  const px = -(hy - oy) / len, py = (hx - ox) / len;          // perpendicular, unit
  const half = Math.max(6, (TAXIWAY_WIDTH_M * view.scale) / 2 + 3);

  const crossing = snap && snap.aircraft.some((a) => a.state === "LINE_UP");
  ctx.fillStyle = crossing ? "#6fcf8e" : C["--stop-bar"];
  const dots = 5;
  for (let i = 0; i < dots; i++) {
    const t = -half + (2 * half * i) / (dots - 1);
    ctx.beginPath();
    ctx.arc(hx + px * t, hy + py * t, 2.2, 0, Math.PI * 2);
    ctx.fill();
  }
  label(hpId, hx - half - 16, hy, { font: `600 12px ${FONT_DATA}` });
}

function drawLayout() {
  const pave = Math.max(5, TAXIWAY_WIDTH_M * view.scale);
  strokeEdges(["taxiway", "apron"], pave, C["--pavement"]);

  // gate stands
  ctx.fillStyle = C["--pavement"];
  const pad = Math.max(16, GATE_PAD_M * view.scale);
  for (const [id, n] of Object.entries(airport.nodes)) {
    if (n.type !== "gate") continue;
    const [x, y] = toScreen(n.x, n.y);
    ctx.fillRect(x - pad / 2, y - pad / 2, pad, pad);
    label(id, x, y + pad / 2 + 12, { font: `600 14px ${FONT_DATA}` });
  }

  drawRunway();
  strokeEdges(["taxiway", "apron"], 1.3, C["--taxi-line"]);
  drawStopBar();

  for (const [id, n] of Object.entries(airport.nodes)) {
    if (n.type !== "taxiway") continue;
    const [x, y] = toScreen(n.x, n.y);
    label(id, x, y - pave / 2 - 9, { font: `500 11px ${FONT_DATA}`, fill: "rgba(127,144,155,.7)" });
  }
}

function drawAircraft(a) {
  const phase = PHASES[a.state] || PHASES.PARKED;
  const fill = C[phase.color];
  const [x, y] = toScreen(a.x, a.y);
  const s = 1.15; // icon scale in px per unit

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate((a.heading * Math.PI) / 180);   // heading 0 = up on screen, clockwise
  ctx.scale(s, s);
  ctx.beginPath();                            // simple jet silhouette, nose pointing up
  ctx.moveTo(0, -10);
  ctx.lineTo(1.6, -6);
  ctx.lineTo(1.6, -2);
  ctx.lineTo(9, 3);
  ctx.lineTo(9, 5);
  ctx.lineTo(1.6, 2.5);
  ctx.lineTo(1.2, 7);
  ctx.lineTo(4, 9);
  ctx.lineTo(4, 10.5);
  ctx.lineTo(0, 9.5);
  ctx.lineTo(-4, 10.5);
  ctx.lineTo(-4, 9);
  ctx.lineTo(-1.2, 7);
  ctx.lineTo(-1.6, 2.5);
  ctx.lineTo(-9, 5);
  ctx.lineTo(-9, 3);
  ctx.lineTo(-1.6, -2);
  ctx.lineTo(-1.6, -6);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = 1.2 / s;
  ctx.strokeStyle = C["--field"];
  ctx.stroke();
  ctx.restore();

  // data block with a short leader line
  const lx = x + 14, ly = y - 18;
  ctx.strokeStyle = fill;
  ctx.globalAlpha = 0.6;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x + 6, y - 6);
  ctx.lineTo(lx - 2, ly + 8);
  ctx.stroke();
  ctx.globalAlpha = 1;

  const line2 = a.alt_ft > 0
    ? `${phase.label} ${a.gs_kt} kt ${a.alt_ft} ft`
    : `${phase.label} ${a.gs_kt} kt`;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.lineJoin = "round";
  ctx.lineWidth = 3;
  ctx.strokeStyle = C["--field"];
  for (const [text, font, dy] of [[a.callsign, `700 15px ${FONT_DATA}`, 0], [line2, `500 13px ${FONT_DATA}`, 14]]) {
    ctx.font = font;
    ctx.strokeText(text, lx, ly + dy);
    ctx.fillStyle = fill;
    ctx.fillText(text, lx, ly + dy);
  }
}

function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  ctx.fillStyle = C["--field"];
  ctx.fillRect(0, 0, w, h);
  if (!airport) return;
  drawLayout();
  if (!snap) return;
  // airborne traffic on top
  const order = [...snap.aircraft].sort((p, q) => (p.state === "AIRBORNE") - (q.state === "AIRBORNE"));
  for (const a of order) drawAircraft(a);
}

// --- panel ------------------------------------------------------------------

const fmtClock = (t) => {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node[k] = v;
  node.append(...children);
  return node;
}

let lastQueueKey = null, lastEventId = 0;

function renderPanel() {
  $("sim-clock").textContent = `T+ ${fmtClock(snap.t)}`;

  const rw = snap.runway;
  const strip = $("runway-strip");
  strip.classList.toggle("busy", !!rw.occupied_by);
  $("rwy-status").textContent = rw.occupied_by ? `${rw.occupied_by} on runway` : "Runway free";

  const qKey = rw.queue.join(",");
  if (qKey !== lastQueueKey) {
    lastQueueKey = qKey;
    const q = $("queue");
    q.replaceChildren(
      ...(rw.queue.length
        ? rw.queue.map((cs) => el("li", { textContent: cs }))
        : [el("li", { className: "empty", textContent: "No one waiting." })]),
    );
  }

  const tbody = $("traffic");
  tbody.replaceChildren(
    ...(snap.aircraft.length
      ? snap.aircraft.map((a) => {
          const tr = el("tr", {},
            el("td", { className: "cs", textContent: a.callsign }),
            el("td", { textContent: (PHASES[a.state] || {}).label || a.state }),
            el("td", { className: "num", textContent: a.gs_kt }),
            el("td", { className: "num", textContent: a.alt_ft }),
          );
          tr.style.color = C[(PHASES[a.state] || PHASES.PARKED).color];
          return tr;
        })
      : [el("tr", { className: "empty" }, el("td", { colSpan: 4, textContent: "No traffic. Spawn a departure to start." }))]),
  );

  const newest = snap.events.length ? snap.events[snap.events.length - 1].id : 0;
  if (newest !== lastEventId) {
    lastEventId = newest;
    const items = [...snap.events].reverse().map((e) =>
      el("li", { className: e.kind },
        el("time", { textContent: fmtClock(e.t) }),
        el("span", { className: "cs", textContent: e.callsign || "SIM" }),
        el("span", { textContent: e.msg }),
      ));
    $("log").replaceChildren(...items);
  }
}

// --- connection -------------------------------------------------------------

let ws = null, retryMs = 1000;

function setLink(up, text) {
  $("link").classList.toggle("up", up);
  $("link-text").textContent = text;
  $("spawn-btn").disabled = !up;
}

function connect() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  ws = new WebSocket(url);
  ws.onopen = () => { retryMs = 1000; setLink(true, "Connected"); };
  ws.onclose = () => {
    setLink(false, "Reconnecting");
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 8000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "airport") {
      airport = msg;
      $("airport-name").textContent = msg.name;
      const sel = $("spawn-gate");
      sel.replaceChildren(el("option", { value: "", textContent: "First free" }),
        ...Object.entries(msg.nodes).filter(([, n]) => n.type === "gate")
          .map(([id]) => el("option", { value: id, textContent: id })));
      const [d1, d2] = msg.runway.id.split("/");
      $("rwy-a").textContent = d1;
      $("rwy-b").textContent = d2;
      computeView();
      requestDraw();
    } else if (msg.type === "snapshot") {
      snap = msg;
      renderPanel();
      requestDraw();
    } else if (msg.type === "ack") {
      const fb = $("spawn-feedback");
      fb.classList.toggle("error", !msg.ok);
      fb.textContent = msg.ok ? `${msg.callsign} spawned. Taxi request in a few seconds.` : msg.error;
      if (msg.ok) $("spawn-callsign").value = "";
    }
  };
}

$("spawn-btn").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const cmd = { type: "spawn" };
  const gate = $("spawn-gate").value;
  const cs = $("spawn-callsign").value.trim();
  if (gate) cmd.gate = gate;
  if (cs) cmd.callsign = cs.toUpperCase();
  ws.send(JSON.stringify(cmd));
});
$("spawn-callsign").addEventListener("keydown", (e) => { if (e.key === "Enter") $("spawn-btn").click(); });

new ResizeObserver(resize).observe(canvas);
if (document.fonts) document.fonts.ready.then(requestDraw);
setLink(false, "Connecting");
connect();
