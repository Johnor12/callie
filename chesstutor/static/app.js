"use strict";
// The page. It knows no chess: the server sends, with every position where it is your move, each legal move and
// the position it leads to. The page draws boards from FENs and asks the server for everything else.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const TOUCH = matchMedia("(pointer: coarse)");
const PHONE = matchMedia("(max-width: 760px)");  // the one-column layout of style.css
const FILES = "abcdefgh";
const FORM = "chess-tutor.form";

const S = {
  meta: null,
  gid: null,       // the game on screen
  game: null,      // its state, as the server last sent it
  view: 0,         // the ply shown on the board (0: the start)
  follow: true,    // the board shows the latest position and moves on with the game
  flip: false,
  sel: null,       // the square of the piece picked up
  drag: null,
  promo: null,     // a pawn move waiting for its piece to be chosen
  pending: null,   // your move, shown before the server has answered
  poll: null,      // AbortController of the wait on the game
  clockAt: 0,      // when the clock in S.game was read
  term: null,
  reopen: false,   // the game's session is to be shown again if its terminal has ended (once each time you come back)
  form: { engine: "stockfish", color: "w", elo: 1200, full: false, opening: "", time: "untimed" },
};
try { Object.assign(S.form, JSON.parse(localStorage.getItem(FORM) || "{}")); } catch (e) { /* no storage */ }

async function api(method, path, body, signal) {
  const opt = { method, signal };
  if (method === "POST") { opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body || {}); }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `the server answered ${r.status}`);
  return data;
}
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

// ---------- positions ----------
function fenBoard(fen) {  // the 64 squares, a1 first
  const out = new Array(64).fill(null);
  fen.split(" ")[0].split("/").forEach((row, i) => {
    let f = 0;
    for (const ch of row) {
      if (ch >= "1" && ch <= "8") f += +ch;
      else out[f++ + 8 * (7 - i)] = ch;
    }
  });
  return out;
}
const sqName = (i) => FILES[i & 7] + ((i >> 3) + 1);
const sqIndex = (name) => FILES.indexOf(name[0]) + 8 * (+name[1] - 1);
// White's winning chances, 0 to 100, for an evaluation (the same curve as the server's).
function winPct(e) {
  if (!e) return 50;
  if (e.mate !== null && e.mate !== undefined) return e.value > 0 ? 100 : 0;
  const cp = Math.max(-1000, Math.min(1000, e.value));
  return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1);
}
const showEval = (e) => !e ? "…" : e.mate !== null && e.mate !== undefined ? (e.mate === 0 ? "#" : `#${e.mate}`) : `${e.cp >= 0 ? "+" : "−"}${(Math.abs(e.cp) / 100).toFixed(2)}`;
const moveNo = (ply) => `${Math.ceil(ply / 2)}${ply % 2 ? "." : "..."}`;
function numbered(firstPly, sans) {  // "12. Nf3 Nc6 13. Bb5" for moves starting at a ply
  return sans.map((san, i) => ((firstPly + i) % 2 || i === 0 ? moveNo(firstPly + i) + " " : "") + san).join(" ");
}
const GLYPH = { blunder: "??", mistake: "?", inaccuracy: "?!" };

// ---------- routing ----------
function route() {
  const m = /^#\/game\/([0-9a-f]+)/.exec(location.hash);
  if (m) showGame(m[1]); else showHome();
}
addEventListener("hashchange", route);

async function loadMeta() {
  if (!S.meta) {
    S.meta = await api("GET", "/api/meta");
    $("engine-name").textContent = S.meta.engine;
  }
  return S.meta;
}

// ---------- home ----------
async function showHome() {
  leaveGame();
  $("game").hidden = true; $("home").hidden = false; $("crumb").textContent = "";
  document.title = "Chess Tutor";
  try {
    await loadMeta();
    renderForm();
    renderGames(await api("GET", "/api/games"));
  } catch (e) { $("f-err").textContent = e.message; }
}
function saveForm() { try { localStorage.setItem(FORM, JSON.stringify(S.form)); } catch (e) { /* no storage */ } }
function renderForm() {
  const f = S.form, m = S.meta;
  // Who plays: an engine that is not installed can be seen but not picked.
  let eng = m.engines.find((e) => e.key === f.engine && e.available) || m.engines[0];
  f.engine = eng.key;
  const off = m.engines.find((e) => !e.available);
  $("f-engine").innerHTML = m.engines.map((e) => `<button data-v="${e.key}" class="${e.key === eng.key ? "on" : ""}"${e.available ? "" : " disabled"} title="${esc(e.available ? "" : e.missing)}">${esc(e.name)}</button>`).join("");
  $("f-engine-hint").textContent = off ? `${off.name} is not installed: run \`chess-tutor allie install\` to play against it.` : "";
  if (!eng.full) f.full = false;
  f.elo = Math.max(eng.elo.min, Math.min(eng.elo.max, +f.elo || eng.elo.default));
  for (const b of $("f-color").children) b.classList.toggle("on", b.dataset.v === f.color);
  for (const el of [$("f-elo"), $("f-elo-n")]) { el.min = eng.elo.min; el.max = eng.elo.max; el.value = f.elo; el.disabled = f.full; }
  $("f-full").checked = f.full; $("f-full-label").hidden = !eng.full;
  // The opponent can only steer for an opening of the colour it plays.
  const side = f.color === "w" ? "b" : f.color === "b" ? "w" : null;
  const list = side ? m.openings[side] : [];
  if (!list.some((o) => o.key === f.opening)) f.opening = "";
  $("f-opening").innerHTML = `<option value="">Engine's choice</option>` + list.map((o) => `<option value="${o.key}"${o.key === f.opening ? " selected" : ""}>${esc(o.name)}</option>`).join("");
  $("f-opening").disabled = !side;
  if (!m.time_controls.includes(f.time)) f.time = "untimed";
  $("f-time-label").textContent = eng.key === "allie" ? "Clocks" : "Your clock";
  $("f-time").innerHTML = m.time_controls.map((t) => `<button data-v="${t}" class="${t === f.time ? "on" : ""}">${t === "untimed" ? "Untimed" : esc(t.replace("+", " + "))}</button>`).join("");
}
$("f-engine").onclick = (ev) => { const b = ev.target.closest("button"); if (b && !b.disabled) { S.form.engine = b.dataset.v; saveForm(); renderForm(); } };
$("f-color").onclick = (ev) => { const b = ev.target.closest("button"); if (b) { S.form.color = b.dataset.v; saveForm(); renderForm(); } };
$("f-time").onclick = (ev) => { const b = ev.target.closest("button"); if (b) { S.form.time = b.dataset.v; saveForm(); renderForm(); } };
$("f-elo").oninput = () => { S.form.elo = +$("f-elo").value; saveForm(); renderForm(); };
$("f-elo-n").onchange = () => { S.form.elo = +$("f-elo-n").value; saveForm(); renderForm(); };
$("f-full").onchange = () => { S.form.full = $("f-full").checked; saveForm(); renderForm(); };
$("f-opening").onchange = () => { S.form.opening = $("f-opening").value; saveForm(); renderForm(); };
$("f-start").onclick = async () => {
  const f = S.form;
  $("f-err").textContent = ""; $("f-start").disabled = true;
  try {
    const g = await api("POST", "/api/games", { engine: f.engine, color: f.color, elo: f.full ? null : f.elo, opening: f.opening || null, time_control: f.time });
    location.hash = `#/game/${g.id}`;
  } catch (e) { $("f-err").textContent = e.message; }
  $("f-start").disabled = false;
};

function thumb(fen, flipped) {
  const b = fenBoard(fen);
  let html = "";
  for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
    const file = flipped ? 7 - c : c, rank = flipped ? r : 7 - r, p = b[file + 8 * rank];
    html += `<div class="sq${(file + rank) % 2 ? "" : " dk"}">${p ? pieceSvg(p) : ""}</div>`;
  }
  return `<div class="thumb">${html}</div>`;
}
function outcome(g) {  // [label, class] of a game for you
  if (g.status !== "finished") return ["In progress", "live"];
  if (g.result === "1/2-1/2") return [`Draw · ${g.reason}`, ""];
  const won = (g.result === "1-0") === (g.user_color === "w");
  return [`${won ? "Won" : "Lost"} · ${g.reason === "time" ? "on time" : g.reason}`, won ? "won" : "lost"];
}
const strength = (g) => g.opponent;
function renderGames(games) {
  $("game-empty").hidden = games.length > 0;
  $("game-list").innerHTML = games.map((g) => {
    const [label, cls] = outcome(g), when = new Date(g.updated_at * 1000);
    return `<li data-id="${g.id}" data-testid="game-row">${thumb(g.fen, g.user_color === "b")}
      <div class="g-main"><div class="g-title">You (${g.user_color === "w" ? "White" : "Black"}) vs ${esc(strength(g))}</div>
      <div class="g-sub">${esc(g.opening.name)} · ${Math.ceil(g.plies / 2)} moves · ${g.time_control === "untimed" ? "untimed" : esc(g.time_control)}</div>
      <div class="g-sub">${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div></div>
      <span class="tag ${cls}">${esc(label)}</span>
      <button class="quiet" data-del="${g.id}" title="Delete this game" data-testid="game-delete">✕</button></li>`;
  }).join("");
}
$("game-list").onclick = async (ev) => {
  const del = ev.target.closest("[data-del]"), row = ev.target.closest("li");
  if (del) {
    if (!confirm("Delete this game and its analysis?")) return;
    await api("POST", `/api/games/${del.dataset.del}/delete`).catch(() => {});
    return showHome();
  }
  if (row) location.hash = `#/game/${row.dataset.id}`;
};

// ---------- a game ----------
function showGame(id) {
  $("home").hidden = true; $("game").hidden = false;
  if (S.gid === id) return;
  leaveGame();
  Object.assign(S, { gid: id, game: null, view: 0, follow: true, flip: false, sel: null, drag: null, promo: null, pending: null, reopen: true });
  $("board").innerHTML = ""; $("moves").innerHTML = ""; $("insight").innerHTML = ""; $("game-err").textContent = "";
  loadMeta().catch(() => {});
  startPoll();
}
function leaveGame() {
  stopPoll(); closeTerminal();
  S.gid = null; S.game = null;
}
function startPoll() {
  if (S.poll || !S.gid || document.hidden) return;
  const ctl = S.poll = new AbortController(), id = S.gid;
  (async () => {
    let since = null;
    while (!ctl.signal.aborted) {
      try {
        const g = await api("GET", `/api/games/${id}` + (since === null ? "" : `?since=${since}`), null, ctl.signal);
        if (ctl.signal.aborted) return;
        since = g.version;
        setGame(g);
      } catch (e) {
        if (ctl.signal.aborted) return;
        if (/^no game/.test(e.message)) { location.hash = "#/"; return; }
        $("game-err").textContent = "Lost touch with the server; trying again…";
        S.reopen = true;  // a server that was restarted has none of the terminals it had
        since = null;
        await sleep(1500);
      }
    }
  })();
}
function stopPoll() { if (S.poll) S.poll.abort(); S.poll = null; }
// The wait on the game is also what runs your clock: a hidden tab stops waiting, and the clock stops with it.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { stopPoll(); if (S.term) unlisten(S.term); }
  else { S.reopen = true; startPoll(); if (S.term) listen(S.term); }
});

function setGame(g) {
  if (g.id !== S.gid) return;
  if ($("game-err").textContent.startsWith("Lost touch")) $("game-err").textContent = "";
  S.game = g; S.clockAt = performance.now();
  if (S.pending && g.moves.length >= S.pending.ply) S.pending = null;
  if (S.follow || S.view > g.moves.length) S.view = g.moves.length;
  render();
  syncClaude();
}
function setView(v) {
  const n = S.game.moves.length;
  S.view = Math.max(0, Math.min(n, v));
  S.follow = S.view === n;
  S.sel = null; S.promo = null;
  render(true);
}

function render(scroll) {
  const g = S.game;
  if (!g) return;
  $("crumb").textContent = `You (${g.user_color === "w" ? "White" : "Black"}) vs ${strength(g)} · ${g.opening.name}`;
  document.title = `${g.status === "active" && g.turn === g.user_color ? "Your move · " : ""}Chess Tutor`;
  renderPlayers(); renderBoard(); renderEval(); renderGraph(); renderMoves(scroll); renderInsight();
  const [label] = outcome(g);
  $("result").hidden = g.status !== "finished";
  if (g.status === "finished") $("result").innerHTML = `${esc(label)} <a href="#/">New game</a>`;
  $("act-resign").disabled = g.status !== "active";
  $("act-takeback").disabled = !g.moves.some((m) => m.source === "user") || g.reason === "time";
  const n = g.moves.length;
  $("nav-first").disabled = $("nav-prev").disabled = S.view === 0;
  $("nav-next").disabled = $("nav-last").disabled = S.view === n;
}

function renderPlayers() {
  const g = S.game;
  const theirs = g.clock && g.clock.opponent, both = theirs ? "the clocks" : "your clock";
  const note = g.trouble ? `<span class="thinking" title="${esc(g.trouble)}">cannot move: ${esc(g.trouble)}</span>`
    : g.loading ? `<span class="thinking">loading Allie, about a minute…</span>` : g.thinking ? `<span class="thinking">thinking…</span>` : "";
  const offer = g.draw_offer ? `<span class="offer">offers a draw</span><button id="draw-accept" data-testid="draw-accept">Accept</button>` +
    `<button id="draw-decline" class="quiet" data-testid="draw-decline">Decline</button>` : "";
  $("p-top").innerHTML = `<span class="name">${esc(strength(g))}</span>` + note + offer +
    (theirs ? `<div class="clock" id="opp-clock"><span class="time" id="opp-clock-time" data-testid="opponent-clock"></span></div>` : "");
  let clock = "";
  if (g.clock) {
    clock = `<div class="clock" id="clock"><span class="time" id="clock-time" data-testid="clock"></span>` +
      (g.status === "active" ? `<button id="clock-pause" data-testid="clock-pause" title="${g.clock.paused ? `Start ${both} again` : `Stop ${both}, to talk to Claude or step away`}">${g.clock.paused ? "Resume" : "Pause"}</button>` : "") + `</div>`;
  }
  $("p-bottom").innerHTML = `<span class="name">You</span><span class="sub">${g.user_color === "w" ? "White" : "Black"}</span>${clock}`;
  if (g.clock) {
    tickClock();
    const b = $("clock-pause");
    if (b) b.onclick = () => act(() => api("POST", `/api/games/${g.id}/pause`, { paused: !g.clock.paused }));
  }
  if (g.draw_offer) for (const accept of [true, false]) {
    $(accept ? "draw-accept" : "draw-decline").onclick = () => act(() => api("POST", `/api/games/${g.id}/draw`, { accept }));
  }
}
function tickClock() {
  const g = S.game;
  if (!g || !g.clock) return;
  showClock($("clock"), $("clock-time"), g.clock);
  if (g.clock.opponent) showClock($("opp-clock"), $("opp-clock-time"), g.clock.opponent);
}
function showClock(box, el, c) {  // a clock as the server last read it, less the time since if it is running
  const g = S.game;
  if (!box || !el) return;
  const ms = Math.max(0, c.ms - (c.running ? performance.now() - S.clockAt : 0)), paused = g.clock.paused && g.status === "active";
  const s = Math.floor(ms / 1000), text = ms < 20000 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}.${Math.floor(ms % 1000 / 100)}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  el.textContent = paused ? `${text} ⏸` : text;
  box.classList.toggle("running", c.running);
  box.classList.toggle("paused", paused);
  box.classList.toggle("low", ms < 30000 && g.status === "active");
}
setInterval(tickClock, 100);

// The position on the board: the one being looked at, or the one your move leads to while the server answers.
function shown() {
  const g = S.game, n = g.moves.length, live = S.view === n;
  if (S.pending && live) return { fen: S.pending.fen, last: S.pending.uci, san: S.pending.san, live, ply: n + 1 };
  if (S.view === 0) return { fen: g.start_fen, last: null, san: "", live, ply: 0 };
  const m = g.moves[S.view - 1];
  return { fen: m.fen, last: m.uci, san: m.san, live, ply: S.view, move: m };
}
const flipped = () => (S.game.user_color === "b") !== S.flip;
const canMove = () => { const g = S.game; return g && S.view === g.moves.length && !S.pending && g.status === "active" && g.turn === g.user_color; };
const targets = (from) => canMove() ? Object.keys(S.game.legal).filter((u) => u.startsWith(from)).map((u) => u.slice(2, 4)) : [];

function renderBoard() {
  const g = S.game, pos = shown(), board = fenBoard(pos.fen), flip = flipped(), movable = canMove();
  const white = pos.fen.split(" ")[1] === "w";
  const inCheck = /[+#]$/.test(pos.san) ? sqName(board.indexOf(white ? "K" : "k")) : null;
  const marks = S.sel ? targets(S.sel) : [];
  let html = "";
  for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
    const file = flip ? 7 - c : c, rank = flip ? r : 7 - r, i = file + 8 * rank, name = sqName(i), p = board[i];
    const cls = ["sq"];
    if ((file + rank) % 2 === 0) cls.push("dk");
    if (pos.last && (pos.last.slice(0, 2) === name || pos.last.slice(2, 4) === name)) cls.push("last");
    if (S.sel === name) cls.push("sel");
    if (inCheck === name) cls.push("check");
    if (marks.includes(name)) cls.push("target", p ? "occupied" : "");
    if (movable && p && (p === p.toUpperCase()) === (g.user_color === "w")) cls.push("movable");
    html += `<div class="${cls.join(" ")}" data-sq="${name}">` +
      (c === 0 ? `<span class="coord rank">${rank + 1}</span>` : "") + (r === 7 ? `<span class="coord file">${FILES[file]}</span>` : "") +
      (p ? pieceSvg(p).replace('class="pc', `class="pc${S.drag && S.drag.moved && S.drag.from === name ? " dragging" : ""}`) : "") + `</div>`;
  }
  // Where the engine would have gone instead of the move shown (the server says only once the game is over).
  const m = pos.move;
  if (m && m.best && m.best.uci !== m.uci) html += arrow(m.best.uci, flip);
  if (S.promo) {
    const c = (flip ? 7 - FILES.indexOf(S.promo.to[0]) : FILES.indexOf(S.promo.to[0])), top = (+S.promo.to[1] === 8) !== flip;
    const pieces = (g.user_color === "w" ? "QRBN" : "qrbn").split("");
    html += `<div id="promo" style="left:${c * 12.5}%;${top ? "top:0" : "bottom:0;flex-direction:column-reverse"}">` +
      pieces.map((p) => `<button data-promo="${p.toLowerCase()}" title="${p}">${pieceSvg(p)}</button>`).join("") + `</div>`;
  }
  $("board").innerHTML = html;
  $("board").classList.toggle("review", !pos.live);
  $("evalbar").classList.toggle("flip", flip);
}
function arrow(uci, flip) {
  const at = (name) => { const f = FILES.indexOf(name[0]), r = +name[1] - 1; return [(flip ? 7 - f : f) + 0.5, (flip ? r : 7 - r) + 0.5]; };
  const [x1, y1] = at(uci.slice(0, 2)), [x2, y2] = at(uci.slice(2, 4));
  const len = Math.hypot(x2 - x1, y2 - y1), ux = (x2 - x1) / len, uy = (y2 - y1) / len;
  const ex = x2 - ux * 0.32, ey = y2 - uy * 0.32;  // the shaft stops where the head starts
  return `<svg id="arrows" viewBox="0 0 8 8"><g fill="#3d9a4a" stroke="#3d9a4a" opacity=".85">
    <line x1="${x1 + ux * 0.2}" y1="${y1 + uy * 0.2}" x2="${ex}" y2="${ey}" stroke-width=".17" stroke-linecap="round"/>
    <polygon stroke="none" points="${x2},${y2} ${ex - uy * 0.24},${ey + ux * 0.24} ${ex + uy * 0.24},${ey - ux * 0.24}"/></g></svg>`;
}

// ---------- moving pieces: drag one, or click it and then its square ----------
const squareAt = (x, y) => { const el = document.elementFromPoint(x, y); const s = el && el.closest && el.closest(".sq"); return s ? s.dataset.sq : null; };
$("board").addEventListener("pointerdown", (ev) => {
  const promo = ev.target.closest("[data-promo]");
  if (promo) return sendMove(S.promo.from + S.promo.to + promo.dataset.promo);
  if (S.promo) { S.promo = null; return renderBoard(); }
  const sq = squareAt(ev.clientX, ev.clientY);
  if (!sq || ev.button || !canMove()) return;
  ev.preventDefault();
  if (S.sel && targets(S.sel).includes(sq)) return tryMove(S.sel, sq);
  const p = fenBoard(shown().fen)[sqIndex(sq)];
  if (p && (p === p.toUpperCase()) === (S.game.user_color === "w")) {
    S.drag = { from: sq, piece: p, x: ev.clientX, y: ev.clientY, moved: false, again: S.sel === sq };
    S.sel = sq;
    try { $("board").setPointerCapture(ev.pointerId); } catch (e) { /* a synthetic event */ }
  } else S.sel = null;
  renderBoard();
});
$("board").addEventListener("pointermove", (ev) => {
  const d = S.drag;
  if (!d) return;
  if (!d.moved && Math.hypot(ev.clientX - d.x, ev.clientY - d.y) < 5) return;
  if (!d.moved) {
    d.moved = true;
    const ghost = document.createElement("div");
    ghost.id = "ghost"; ghost.innerHTML = pieceSvg(d.piece);
    document.body.appendChild(ghost);
    renderBoard();
  }
  const ghost = $("ghost");
  ghost.style.left = `${ev.clientX}px`; ghost.style.top = `${ev.clientY}px`;
});
function endDrag(ev, cancelled) {
  const d = S.drag;
  if (!d) return;
  S.drag = null;
  const ghost = $("ghost");
  if (ghost) ghost.remove();
  if (d.moved && !cancelled) {
    const to = squareAt(ev.clientX, ev.clientY);
    if (to && targets(d.from).includes(to)) return tryMove(d.from, to);
    S.sel = null;
  } else if (d.again) S.sel = null;  // a second click on the piece puts it down
  renderBoard();
}
$("board").addEventListener("pointerup", (ev) => endDrag(ev, false));
$("board").addEventListener("pointercancel", (ev) => endDrag(ev, true));

function tryMove(from, to) {
  const found = Object.keys(S.game.legal).filter((u) => u.startsWith(from + to));
  if (found.length > 1) { S.promo = { from, to }; S.sel = null; return renderBoard(); }  // a pawn reaching the last rank
  if (found.length === 1) return sendMove(found[0]);
  S.sel = null; renderBoard();
}
async function sendMove(uci) {
  const g = S.game, m = g.legal[uci];
  if (!m) return;
  S.pending = { uci, fen: m.fen, san: m.san, ply: g.moves.length + 1 };
  S.sel = null; S.promo = null; S.follow = true;
  $("game-err").textContent = "";
  render();
  try { await api("POST", `/api/games/${g.id}/move`, { move: uci }); }  // the wait on the game brings the new state
  catch (e) { S.pending = null; $("game-err").textContent = e.message; render(); }
}
async function act(fn) {
  $("game-err").textContent = "";
  try { await fn(); } catch (e) { $("game-err").textContent = e.message; }
}

// ---------- the engine's view ----------
function evalAt(ply) { const g = S.game; return ply === 0 ? g.start_eval : (g.moves[ply - 1] || {}).eval; }
function renderEval() {
  const pos = shown(), e = pos.ply <= S.game.moves.length ? evalAt(pos.ply) : null;
  if (!e) { $("evalbar-text").textContent = "…"; return; }  // not analysed yet: the bar stays where it was
  const pct = winPct(e), whiteAhead = e.value >= 0, flip = flipped();
  $("evalbar-white").style.height = `${pct}%`;
  const t = $("evalbar-text");
  t.textContent = e.mate !== null && e.mate !== undefined ? (e.mate === 0 ? "#" : `M${Math.abs(e.mate)}`) : (Math.abs(e.cp) / 100).toFixed(1);
  t.classList.toggle("onblack", !whiteAhead);
  t.classList.toggle("top", whiteAhead === flip);  // written at the end of the side that is ahead
  $("evalbar").title = `Engine evaluation: ${showEval(e)} for White (depth ${e.depth})`;
}
function renderGraph() {
  const g = S.game, n = g.moves.length, w = 100, h = 40;
  const x = (p) => n ? (p / n) * w : 0;
  let pts = "", lastY = h / 2;
  for (let p = 0; p <= n; p++) {
    const e = evalAt(p);
    if (e) lastY = h - winPct(e) * h / 100;
    pts += `${x(p).toFixed(2)},${lastY.toFixed(2)} `;
  }
  const marks = g.moves.filter((m) => m.source === "user" && (m.verdict === "blunder" || m.verdict === "mistake"))
    .map((m) => `<line x1="${x(m.ply)}" x2="${x(m.ply)}" y1="0" y2="${h}" stroke="${m.verdict === "blunder" ? "#d9453d" : "#e0683c"}" stroke-width=".5" opacity=".7" vector-effect="non-scaling-stroke"/>`).join("");
  $("graph").innerHTML = `<title>White's winning chances over the game (click to go to a move)</title>
    <polygon points="0,${h} ${pts}${w},${h}" fill="#eceff1"/>${marks}
    <line x1="0" x2="${w}" y1="${h / 2}" y2="${h / 2}" stroke="#e0683c" stroke-width="1" opacity=".5" vector-effect="non-scaling-stroke"/>
    <line x1="${x(S.view)}" x2="${x(S.view)}" y1="0" y2="${h}" stroke="#5b9dd9" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
}
$("graph").onclick = (ev) => {
  const box = $("graph").getBoundingClientRect();
  setView(Math.round((ev.clientX - box.left) / box.width * S.game.moves.length));
};
function renderMoves(scroll) {
  const g = S.game;
  let html = "";
  g.moves.forEach((m, i) => {
    if (i % 2 === 0) html += `<div class="num">${i / 2 + 1}.</div>`;
    const book = m.source === "book", graded = m.verdict !== undefined;
    const loss = book ? "book" : graded ? (m.verdict === "best" ? "0" : m.cpl) : "…";
    html += `<div class="mv${m.source === "user" ? " mine" : ""}${m.ply === S.view ? " on" : ""} v-${book ? "book" : m.verdict || "wait"}" data-ply="${m.ply}" data-testid="move"
      title="${graded ? `${m.verdict}: lost ${m.cpl} centipawns` + (m.best && m.best.uci !== m.uci ? `; the engine preferred ${esc(m.best.san)}` : "") : "being analysed"}">
      <span class="san">${esc(m.san)}${book ? "" : GLYPH[m.verdict] || ""}</span><span class="loss">${loss}</span></div>`;
  });
  const el = $("moves"), atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  el.innerHTML = html;
  const on = el.querySelector(".mv.on");
  if (scroll && on) on.scrollIntoView({ block: "nearest" });
  else if (atEnd) el.scrollTop = el.scrollHeight;
  const a = g.acpl;
  $("acpl").textContent = a.user === null ? "" : `avg loss ${a.user}` + (a.opponent === null ? "" : ` · opp. ${a.opponent}`);
}
$("moves").onclick = (ev) => { const m = ev.target.closest(".mv"); if (m) setView(+m.dataset.ply); };
function renderInsight() {
  const g = S.game, m = S.view > 0 ? g.moves[S.view - 1] : null;
  if (!m) { $("insight").innerHTML = g.moves.length ? "The starting position." : (g.turn === g.user_color ? "Your move." : ""); return; }
  const who = m.source === "user" ? "You played" : m.source === "book" ? "Opponent (opening book)" : "Opponent played";
  let html = `${who} <b>${moveNo(m.ply)} ${esc(m.san)}${GLYPH[m.verdict] || ""}</b>`;
  if (m.verdict === undefined) html += " · the engine is looking at it…";
  else {
    html += ` · ${m.verdict}` + (m.cpl ? ` · lost ${m.cpl} cp` : "") + ` · ${showEval(evalAt(m.ply - 1))} → ${showEval(m.eval)}`;
    if (m.best && m.best.uci !== m.uci) html += `<br>Engine preferred <b>${esc(m.best.san)}</b> (${showEval(m.best)}): ${esc(numbered(m.ply, m.best.line))}`;
  }
  $("insight").innerHTML = html;
}

$("nav-first").onclick = () => setView(0);
$("nav-prev").onclick = () => setView(S.view - 1);
$("nav-next").onclick = () => setView(S.view + 1);
$("nav-last").onclick = () => setView(S.game.moves.length);
$("act-flip").onclick = () => { S.flip = !S.flip; render(); };
$("act-takeback").onclick = () => act(() => api("POST", `/api/games/${S.gid}/takeback`));
$("act-resign").onclick = () => { if (confirm("Resign this game?")) act(() => api("POST", `/api/games/${S.gid}/resign`)); };
$("act-pgn").onclick = async () => {
  const text = S.game.pgn;
  try { await navigator.clipboard.writeText(text); }
  catch (e) {  // no clipboard without HTTPS or localhost
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  $("act-pgn").textContent = "Copied"; setTimeout(() => { $("act-pgn").textContent = "Copy PGN"; }, 1200);
};
document.addEventListener("keydown", (ev) => {
  if (!S.game || $("game").hidden || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.target.closest("input, textarea, select, #terminal")) return;
  const to = { ArrowLeft: S.view - 1, ArrowRight: S.view + 1, Home: 0, End: S.game.moves.length }[ev.key];
  if (to !== undefined) { ev.preventDefault(); setView(to); }
});

// ---------- Claude ----------
// A Claude Code session in a terminal, shown with xterm.js. Nothing starts one but a message sent from the box
// here; once a game has one, it is shown whenever the game is.
function paneSize() {
  if (S.term) return { cols: S.term.xterm.cols, rows: S.term.xterm.rows };
  // No terminal yet: the pane holds only the box. The size is that of the terminal it grows to hold (style.css).
  const font = TOUCH.matches ? 12 : 13, width = $("claude-pane").clientWidth;
  const height = (PHONE.matches ? innerHeight * 0.78 : $("game").clientHeight - 24) - $("compose").offsetHeight;
  return { cols: Math.max(40, Math.floor((width - 24) / (font * 0.61))), rows: Math.max(12, Math.floor((height - 12) / (font * 1.25))) };
}
function syncClaude() {
  const c = S.game.claude, id = c.terminal;
  if (id && (!S.term || S.term.id !== id)) openTerminal(id);
  if (!id && S.term) closeTerminal();
  $("terminal").hidden = !id;
  renderKeys();
  if (id) S.reopen = false;
  else if (S.reopen && c.started && !document.hidden) { S.reopen = false; reopen(); }
}
// A game you come back to shows its session as you left it: one whose terminal has ended since (nobody was
// watching it, the server was restarted) is resumed. Once each time, so one you leave with /exit stays closed.
async function reopen() {
  const gid = S.gid;
  try {
    const r = await api("POST", `/api/games/${gid}/claude/resume`, paneSize());
    if (S.gid === gid && S.game) { S.game.claude.terminal = r.terminal; syncClaude(); }
  } catch (e) { /* nothing to resume (its transcript is gone), or no Claude Code: a message sent says which */ }
}
async function claude(path, body) {
  $("claude-err").textContent = "";
  try {
    const r = await api("POST", `/api/games/${S.gid}/claude/${path}`, { ...paneSize(), ...(body || {}) });
    if (r.terminal && S.game) { S.game.claude.terminal = r.terminal; S.game.claude.started = true; syncClaude(); }
    return r;
  } catch (e) { $("claude-err").textContent = e.message; throw e; }
}
async function composeSend() {
  const text = $("compose-text").value;
  if (!text.trim() || !S.gid) return;
  $("compose-text").value = "";
  try { await claude("say", { text }); } catch (e) { $("compose-text").value = text; }
}
$("compose-send").onclick = composeSend;
$("compose-text").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); composeSend(); } });

const addon = (load) => { try { load(); } catch (e) { /* not there, or no GPU: the terminal works without it */ } };
function openTerminal(id) {
  closeTerminal();
  $("terminal").hidden = false;
  const xterm = new Terminal({
    fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, "DejaVu Sans Mono", monospace', fontSize: TOUCH.matches ? 12 : 13, cursorBlink: true,
    scrollback: 5000, allowProposedApi: true, theme: { background: "#16181d", foreground: "#d7dae0" },
  });
  const fit = new FitAddon.FitAddon();
  xterm.loadAddon(fit);
  // Claude Code lays a line out with an emoji two columns wide; xterm's own table says one for most.
  addon(() => xterm.loadAddon(new UnicodeGraphemesAddon.UnicodeGraphemesAddon()));
  xterm.open($("terminal"));
  // Drawn on the GPU: a full-screen program redraws most rows on every line it scrolls.
  addon(() => { const gl = new WebglAddon.WebglAddon(); gl.onContextLoss(() => gl.dispose()); xterm.loadAddon(gl); });
  const t = S.term = { id, xterm, fit, out: "", sending: false, size: "", sized: Promise.resolve(), opened: 0, wheel: 0, fresh: false };
  xterm.onData((data) => typed(t, data));
  xterm.attachCustomWheelEventHandler((ev) => wheel(t, ev));
  xterm.parser.registerOscHandler(52, clip);
  t.resize = (force) => {
    if (S.term !== t || $("terminal").hidden || !$("terminal").clientWidth) return;
    fit.fit();
    const size = `${xterm.cols}x${xterm.rows}`;
    // Asked for the size it already has, the server makes the program draw its whole screen again.
    if (size !== t.size || force) t.sized = t.sized.then(() => api("POST", `/api/terminals/${t.id}/resize`, { cols: xterm.cols, rows: xterm.rows })).catch(() => {});
    t.size = size;
  };
  t.observer = new ResizeObserver(() => { clearTimeout(t.timer); t.timer = setTimeout(() => t.resize(false), 80); });
  t.observer.observe($("terminal"));
  listen(t);
  renderKeys();
}
function closeTerminal() {
  const t = S.term;
  if (!t) return;
  S.term = null;
  unlisten(t); t.observer.disconnect(); clearTimeout(t.timer); t.xterm.dispose();
  $("terminal").innerHTML = "";
  renderKeys();
}
// The terminal's output, for as long as the tab is shown (a browser allows a site only a few connections).
function listen(t) {
  if (t.source || document.hidden) return;
  const src = t.source = new EventSource(`/api/terminals/${t.id}/stream`);
  // Each connection starts with the terminal's state: after the first, that is written over a cleared screen.
  src.onopen = () => { if (t.opened++) t.xterm.reset(); t.fresh = true; };
  src.onmessage = (ev) => {
    const bytes = Uint8Array.from(atob(ev.data), (c) => c.charCodeAt(0));
    if (!t.fresh) return t.xterm.write(bytes);
    t.fresh = false;
    t.xterm.write(bytes, () => t.resize(true));  // a viewer that has just arrived has none of a full-screen program's screen
  };
  const ended = () => { if (S.term === t) { closeTerminal(); if (S.game) { S.game.claude.terminal = null; syncClaude(); } } };
  src.addEventListener("exit", ended);
  src.onerror = () => { if (src.readyState === EventSource.CLOSED) ended(); };
}
function unlisten(t) { if (t.source) t.source.close(); t.source = null; }
// What is typed and what the mouse does: one request at a time, so it arrives in order.
function typed(t, data) { t.out += data; flush(t); }
async function flush(t) {
  if (t.sending) return;
  t.sending = true;
  while (t.out && S.term === t) {
    const data = t.out;
    t.out = "";
    try { await api("POST", `/api/terminals/${t.id}/input`, { data }); } catch (e) { /* it has ended: its stream says so */ }
  }
  t.sending = false;
}
// The wheel over a full-screen program, which takes the mouse: sent as one wheel event per line moved (Claude
// Code is started with one line per event), where the pointer is.
function wheel(t, ev) {
  const x = t.xterm;
  if (x.modes.mouseTrackingMode === "none") return true;  // xterm scrolls its own history
  const rowH = $("terminal").clientHeight / x.rows;
  const px = ev.deltaMode === 1 ? ev.deltaY * rowH : ev.deltaMode === 2 ? ev.deltaY * rowH * x.rows : ev.deltaY;
  scrollTui(t, px / rowH, ev.clientX, ev.clientY);
  ev.preventDefault();
  return false;
}
function scrollTui(t, lines, cx, cy) {
  const x = t.xterm;
  t.wheel += lines;
  const n = Math.min(Math.trunc(Math.abs(t.wheel)), x.rows);
  if (!n) return;
  const down = t.wheel > 0;
  t.wheel -= Math.sign(t.wheel) * Math.trunc(Math.abs(t.wheel));
  const screen = $("terminal").querySelector(".xterm-screen"), box = screen.getBoundingClientRect();
  const col = Math.max(1, Math.min(x.cols, 1 + Math.floor((cx - box.left) / (box.width / x.cols))));
  const row = Math.max(1, Math.min(x.rows, 1 + Math.floor((cy - box.top) / (box.height / x.rows))));
  typed(t, `\x1b[<${down ? 65 : 64};${col};${row}M`.repeat(n));
}
// A finger dragged over the terminal scrolls it the same way.
let touchY = null;
$("terminal").addEventListener("touchstart", (ev) => { touchY = ev.touches.length === 1 ? ev.touches[0].clientY : null; }, { passive: true });
$("terminal").addEventListener("touchmove", (ev) => {
  const t = S.term;
  if (!t || touchY === null || t.xterm.modes.mouseTrackingMode === "none") return;
  const y = ev.touches[0].clientY, rowH = $("terminal").clientHeight / t.xterm.rows;
  scrollTui(t, (touchY - y) / rowH, ev.touches[0].clientX, y);
  touchY = y;
  ev.preventDefault();
}, { passive: false });
// A phone's keyboard has no Esc, Tab, arrows or Ctrl: a row of them under the terminal.
const KEYS = { esc: "\x1b", enter: "\r", tab: "\t", ctrlc: "\x03", up: "A", down: "B" };
function renderKeys() { $("keys").hidden = !S.term || !TOUCH.matches; }
$("keys").onclick = (ev) => {
  const b = ev.target.closest("button"), t = S.term;
  if (!b || !t) return;
  const seq = KEYS[b.dataset.key];
  typed(t, seq.length === 1 && seq >= "A" ? (t.xterm.modes.applicationCursorKeysMode ? "\x1bO" : "\x1b[") + seq : seq);
};
// Claude Code copies a selection as OSC 52: taken here, so it lands on this machine's clipboard.
function clip(data) {
  const text = data.slice(data.indexOf(";") + 1);
  if (text === "?") return true;
  try { navigator.clipboard.writeText(new TextDecoder().decode(Uint8Array.from(atob(text), (c) => c.charCodeAt(0)))).catch(() => {}); } catch (e) { /* not base64 */ }
  return true;
}

route();
