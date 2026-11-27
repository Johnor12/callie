"use strict";
// The page. It knows no chess: the server sends, with every position where it is your move, each legal move and
// the position it leads to, and while it is the opponent's the moves that can be made ahead. The page draws boards
// from FENs and asks the server for everything else.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const TOUCH = matchMedia("(pointer: coarse)");
const PHONE = matchMedia("(max-width: 760px)");  // the one-column layout of style.css
const FILES = "abcdefgh";
const FORM = "callie.form";  // read from "chess-tutor.form" until saved here (the app's old name)
const FAST_MS = 3000;  // a card answered right this soon was easy: it is not asked how hard it was
const GRADES = ["hard", "good", "easy"];  // what a right answer is rated: how it was to remember
const LOOK_MS = [250, 1000, 4000];  // how long the engine looks at the position on a board that is not being played: asked again, each time for longer

const S = {
  meta: null,
  gid: null,       // the game on screen
  game: null,      // its state, as the server last sent it
  view: 0,         // the ply shown on the board (0: the start)
  follow: true,    // the board shows the latest position and moves on with the game
  flip: false,
  why: null,       // the ply of the move whose explanation is shown beside it
  sel: null,       // the square of the piece picked up
  drag: null,
  promo: null,     // a pawn move waiting for its piece to be chosen
  pending: null,   // your move, shown before the server has answered
  ahead: {},       // the moves that can be made ahead of the opponent's (the game's `premoves`), keyed as its `legal` is
  aheadSent: Promise.resolve(),  // the last of them sent: one request at a time, so they arrive in order
  poll: null,      // AbortController of the wait on the game
  clockAt: 0,      // when the clock in S.game was read
  term: null,
  shown: [],       // what Claude put on the board in the session on screen: positions, each with lines from it
  explored: [],    // the lines of your own tried on the board on screen, once it is not being played (see keepLine)
  exploredOf: null,  // whose those are: the board they were asked for
  shownAt: null,   // the one of all those that is listed (its id: see allLines)
  line: null,      // the line on the board in place of the game or the card: { set, line, path } (path: its moves played so far, as UCI)
  evals: new Map(),  // what the engine makes of the positions of a board that is not being played, by FEN (see look)
  legal: new Map(),  // and what can be moved in them
  reopen: false,   // the game's session is to be shown again if its terminal has ended (once each time you come back)
  review: null,    // the deck being reviewed and its card on screen (see showReview)
  rating: {},      // yours in each time format, as the server last reckoned them
  // elo: the opponent's strength. It is not kept: null until you set it, and until then it follows your rating
  form: { engine: "stockfish", color: "w", elo: null, full: false, opening: "", time: "untimed" },
};
try { Object.assign(S.form, JSON.parse(localStorage.getItem(FORM) || localStorage.getItem("chess-tutor.form") || "{}"), { elo: null }); } catch (e) { /* no storage */ }

async function api(method, path, body, signal) {
  const opt = { method, signal };
  if (method === "POST") { opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body || {}); }
  const r = await fetch(path, opt);
  // A request given up while its answer was being read has no answer: it fails, as one given up before that does.
  const data = await r.json().catch((e) => { if (signal && signal.aborted) throw e; return {}; });
  if (!r.ok) throw new Error(data.error || `the server answered ${r.status}`);
  return data;
}
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

// A page on screen says so, at once and every 20 seconds: the server keeps Allie in memory for as long as one does.
// A hidden one (another tab in front, the phone locked or on another app) says nothing, and so is not counted.
function here() { if (!document.hidden) api("POST", "/api/here").catch(() => {}); }
document.addEventListener("visibilitychange", here);
setInterval(here, 20000);
here();

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
const fenPly = (fen) => { const p = fen.split(" "); return 2 * (+p[5] - 1) + (p[1] === "w" ? 1 : 2); };  // the ply of the move to be made in a position
const lost = (cpl) => cpl ? `−${cpl}` : "0";  // what a move lost, as it is written
function numbered(firstPly, sans) {  // "12. Nf3 Nc6 13. Bb5" for moves starting at a ply
  return sans.map((san, i) => ((firstPly + i) % 2 || i === 0 ? moveNo(firstPly + i) + " " : "") + san).join(" ");
}
const GLYPH = { blunder: "??", mistake: "?", inaccuracy: "?!" };

// ---------- routing ----------
function route() {
  const m = /^#\/game\/([0-9a-f]+)/.exec(location.hash), deck = /^#\/review\/([a-z]+)/.exec(location.hash);
  if (m) showGame(m[1]); else if (deck) showReview(deck[1]); else showHome();
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
  leaveGame(); leaveReview();
  $("game").hidden = true; $("review").hidden = true; $("home").hidden = false; $("crumb").textContent = "";
  document.title = "Callie";
  try {
    const [, rating, games, decks] = await Promise.all([loadMeta(), api("GET", "/api/rating"), api("GET", "/api/games"), api("GET", "/api/cards")]);
    S.rating = rating; S.form.elo = null;
    renderForm();
    renderDecks(decks);
    renderRating();
    renderGames(games);
  } catch (e) { $("f-err").textContent = e.message; }
}
function saveForm() { try { localStorage.setItem(FORM, JSON.stringify({ ...S.form, elo: undefined })); } catch (e) { /* no storage */ } }
function renderForm() {
  const f = S.form, m = S.meta;
  // Who plays: an engine that is not installed can be seen but not picked.
  let eng = m.engines.find((e) => e.key === f.engine && e.available) || m.engines[0];
  f.engine = eng.key;
  const off = m.engines.find((e) => !e.available);
  $("f-engine").innerHTML = m.engines.map((e) => `<button data-v="${e.key}" class="${e.key === eng.key ? "on" : ""}"${e.available ? "" : " disabled"} title="${esc(e.available ? "" : e.missing)}">${esc(e.name)}</button>`).join("");
  $("f-engine-hint").textContent = off ? `${off.name} is not installed: run \`callie allie install\` to play against it.` : "";
  if (!eng.full) f.full = false;
  if (!m.time_controls.includes(f.time)) f.time = "untimed";
  // The strength: as you set it, and until you do, your own rating at the clock chosen.
  const elo = Math.max(eng.elo.min, Math.min(eng.elo.max, f.elo || Math.round((yourElo(m.formats[f.time]) || eng.elo.default) / 10) * 10));
  for (const b of $("f-color").children) b.classList.toggle("on", b.dataset.v === f.color);
  for (const el of [$("f-elo"), $("f-elo-n")]) { el.min = eng.elo.min; el.max = eng.elo.max; el.value = elo; el.disabled = f.full; }
  $("f-full").checked = f.full; $("f-full-label").hidden = !eng.full;
  // The opponent can only steer for an opening of the colour it plays.
  const side = f.color === "w" ? "b" : f.color === "b" ? "w" : null;
  const list = side ? m.openings[side] : [];
  if (!list.some((o) => o.key === f.opening)) f.opening = "";
  $("f-opening").innerHTML = `<option value="">Engine's choice</option>` + list.map((o) => `<option value="${o.key}"${o.key === f.opening ? " selected" : ""}>${esc(o.name)}</option>`).join("");
  $("f-opening").disabled = !side;
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
    const g = await api("POST", "/api/games", { engine: f.engine, color: f.color, elo: f.full ? null : +$("f-elo").value, opening: f.opening || null, time_control: f.time });
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
function outcome(g) {  // [label, class, the label without how it ended] of a game for you
  if (g.status !== "finished") return ["In progress", "live", "Live"];
  if (g.result === "1/2-1/2") return [`Draw · ${g.reason}`, "", "Draw"];
  const won = (g.result === "1-0") === (g.user_color === "w"), word = won ? "Won" : "Lost";
  return [`${word} · ${g.reason === "time" ? "on time" : g.reason}`, won ? "won" : "lost", word];
}
const strength = (g) => g.opponent;
const signed = (n) => (n > 0 ? "+" : n < 0 ? "−" : "±") + Math.abs(n);
const eloChange = (r) => `<span class="delta ${r.change > 0 ? "up" : r.change < 0 ? "down" : ""}" data-testid="game-elo">${signed(r.change)}</span>`;
// Your rating in a time format; where you have none yet, the one from the format you have played most.
function yourElo(format) {
  const most = Object.values(S.rating).sort((a, b) => b.games - a.games)[0];
  return (S.rating[format] || most || {}).elo;
}
function renderRating() {
  const all = Object.entries(S.rating);
  $("rating").hidden = !all.length;
  $("rating").innerHTML = "Your Elo" + all.map(([format, r]) =>
    `<span data-testid="rating-${format}">${format[0].toUpperCase() + format.slice(1)} <b>${r.elo}${r.provisional ? "?" : ""}</b></span>`).join("");
}
function renderDecks(decks) {  // each with its active cards; one with none cannot be opened
  $("decks").innerHTML = decks.map((d) =>
    `<button class="${d.key}" data-deck="${d.key}" data-testid="deck-${d.key}"${d.active ? "" : " disabled"}>${esc(d.name)} <b>${d.active}</b></button>`).join("");
}
$("decks").onclick = (ev) => { const b = ev.target.closest("button"); if (b && !b.disabled) location.hash = `#/review/${b.dataset.deck}`; };
function renderGames(games) {
  $("game-empty").hidden = games.length > 0;
  $("game-list").innerHTML = games.map((g) => {
    const [label, cls, short] = outcome(g), when = new Date(g.updated_at * 1000);
    return `<li data-id="${g.id}" data-testid="game-row">${thumb(g.fen, g.user_color === "b")}
      <div class="g-main"><div class="g-title">You (${g.user_color === "w" ? "White" : "Black"}) vs ${esc(strength(g))}</div>
      <div class="g-sub">${esc(g.opening.name)} · ${Math.ceil(g.plies / 2)} moves · ${g.time_control === "untimed" ? "untimed" : esc(g.time_control)}</div>
      <div class="g-sub">${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div></div>
      <div class="g-end"><span class="tag ${cls}" title="${esc(label)}" data-testid="game-outcome">${short}</span>${g.rating ? eloChange(g.rating) : ""}</div>
      <button class="quiet" data-del="${g.id}" title="Delete this game" data-testid="game-delete">✕</button></li>`;
  }).join("");
}
$("game-list").onclick = async (ev) => {
  const del = ev.target.closest("[data-del]"), row = ev.target.closest("li");
  if (del) {
    if (!confirm("Delete this game, its analysis and its cards?")) return;
    await api("POST", `/api/games/${del.dataset.del}/delete`).catch(() => {});
    return showHome();
  }
  if (row) location.hash = `#/game/${row.dataset.id}`;
};

// ---------- a game ----------
function showGame(id) {
  $("home").hidden = true; $("review").hidden = true; $("game").hidden = false;
  leaveReview();
  if (S.gid === id) return;
  leaveGame();
  Object.assign(S, { gid: id, game: null, view: 0, follow: true, flip: false, sel: null, drag: null, promo: null, pending: null, reopen: true });
  claudeIn($("game"), "Ask Claude about the game…");
  $("board").innerHTML = ""; $("moves").innerHTML = ""; $("insight").innerHTML = ""; $("game-err").textContent = "";
  loadMeta().catch(() => {});
  startPoll();
}
function leaveGame() {
  stopPoll(); closeTerminal(); hideWhy();
  S.gid = null; S.game = null; S.line = null;
  leaveBoard();
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
  else { S.reopen = true; startPoll(); if (S.term) listen(S.term); if (S.review && S.review.result) cardSession(S.review); }
});

function setGame(g) {
  if (g.id !== S.gid) return;
  if ($("game-err").textContent.startsWith("Lost touch")) $("game-err").textContent = "";
  const was = S.game;
  S.game = g; S.clockAt = performance.now();
  S.ahead = Object.fromEntries(g.premoves.map((u) => [u, true]));
  if (S.pending && g.moves.length >= S.pending.ply) S.pending = null;
  if (S.follow || S.view > g.moves.length) S.view = g.moves.length;
  if (S.why !== S.view) hideWhy();
  render();
  syncClaude();
  if (!was || was.status !== g.status) { loadExplored(); renderLines(); look(); }  // over, or no longer: what is tried on its board, and the engine on it
}
function setView(v) {
  const n = S.game.moves.length;
  S.view = Math.max(0, Math.min(n, v));
  S.follow = S.view === n;
  S.sel = null; S.promo = null; S.line = null;  // a move of the game takes a line of Claude's off the board
  hideWhy();
  render(true);
  renderLines();
  look();
}

function render(scroll) {
  const g = S.game;
  if (!g) return;
  $("crumb").textContent = `You (${g.user_color === "w" ? "White" : "Black"}) vs ${strength(g)} · ${g.opening.name}`;
  document.title = `${g.status === "active" && g.turn === g.user_color ? "Your move · " : ""}Callie`;
  renderPlayers(); renderBoard(); renderEval(); renderGraph(); renderMoves(scroll); renderInsight();
  const [label] = outcome(g);
  $("result").hidden = g.status !== "finished";
  // With the result, what it did to your rating in the game's format, and where that leaves it.
  const elo = g.rating ? `<span class="rated" title="Your ${g.rating.format} Elo">${eloChange(g.rating)}<span class="now" data-testid="result-elo-after">${g.rating.after}</span></span>` : "";
  if (g.status === "finished") $("result").innerHTML = `<span>${esc(label)}</span>${elo}<a href="#/">New game</a>`;
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
// While the opponent is on its move, yours can be made ahead: the server plays it the moment the opponent has moved.
const canPremove = () => { const g = S.game; return g && S.view === g.moves.length && !S.pending && g.status === "active" && g.turn !== g.user_color; };
// The board in use, a game's or the card's under review: what is on it, whose pieces move and where to (`legal`:
// none when nothing can be moved), the arrow drawn on it, how it is marked, the move made ahead on it (`pre`),
// and what a move made on it does. A board that is not being played (`dead`: a game that is over, the back of a
// card) is one to try moves on: the pieces of the side to move are moved, from its own position or from the one a
// line has got to, and the arrow there is the engine's first choice (`explore`, `look`). With a line on a game
// that is being played: that position, the board turned as it was, and nothing on it to move.
const dead = () => S.review ? !!(S.review.card && S.review.result) : !!S.game && S.game.status === "finished";
function onBoard() {
  const b = ownBoard(), l = linePos();
  if (!l) return b;
  const open = dead(), e = open ? S.evals.get(l.fen) : null;
  return { el: b.el, fen: l.fen, last: l.last, san: l.san, color: open ? l.fen.split(" ")[1] : b.color, flip: b.flip,
    legal: open ? S.legal.get(l.fen) || {} : {}, arrow: e && e.best ? e.best.uci : null, mark: "line", send: open ? explore : () => {} };
}
function ownBoard() {
  const r = S.review;
  if (r) {
    // Until you answer, the move that led to the position is marked, as in a game. Your answer stands on the
    // board once made, unless it was wrong: then the position is back, with where you went marked on it and
    // an arrow for the right move. Either way other moves can then be tried from what is on it (and, with a
    // right answer on it, your own from the card's position: see `unanswer`).
    const c = r.card, res = r.result, made = res ? (res.right ? res.move : null) : r.pending, fen = made ? made.fen : c.fen;
    const e = res && res.right ? S.evals.get(fen) : null;
    return { el: $("card-board"), fen, last: made ? made.uci : res ? res.move.uci : c.last, san: made ? made.san : "",
      color: fen.split(" ")[1], flip: c.turn === "b", legal: res ? S.legal.get(fen) || {} : made ? {} : c.legal,
      arrow: res && !res.right ? res.best.uci : e && e.best ? e.best.uci : null,
      mark: res ? (res.right ? "right" : "wrong") : "", send: res ? explore : answerCard };
  }
  const g = S.game, pos = shown(), m = pos.move, pre = canPremove(), over = g.status === "finished";
  // The arrow: where the engine would have gone instead of the move shown (the server says only once the game is over).
  return { el: $("board"), fen: pos.fen, last: pos.last, san: pos.san, color: over ? pos.fen.split(" ")[1] : g.user_color, flip: flipped(),
    legal: over ? S.legal.get(pos.fen) || {} : canMove() ? g.legal : pre ? S.ahead : {},
    arrow: m && m.best && m.best.uci !== m.uci ? m.best.uci : null, mark: pos.live ? "" : "review", pre: pre ? g.premove : null,
    send: over ? explore : pre ? sendPremove : sendMove };
}
const targets = (from) => Object.keys(onBoard().legal).filter((u) => u.startsWith(from)).map((u) => u.slice(2, 4));

function renderBoard() {
  const b = onBoard(), board = fenBoard(b.fen), flip = b.flip, movable = Object.keys(b.legal).length > 0;
  const white = b.fen.split(" ")[1] === "w";
  const inCheck = /[+#]$/.test(b.san) ? sqName(board.indexOf(white ? "K" : "k")) : null;
  const marks = S.sel ? targets(S.sel) : [];
  let html = "";
  for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
    const file = flip ? 7 - c : c, rank = flip ? r : 7 - r, i = file + 8 * rank, name = sqName(i), p = board[i];
    const cls = ["sq"];
    if ((file + rank) % 2 === 0) cls.push("dk");
    if (b.last && (b.last.slice(0, 2) === name || b.last.slice(2, 4) === name)) cls.push("last");
    if (b.pre && (b.pre.slice(0, 2) === name || b.pre.slice(2, 4) === name)) cls.push("pre");
    if (S.sel === name) cls.push("sel");
    if (inCheck === name) cls.push("check");
    if (marks.includes(name)) cls.push("target", p ? "occupied" : "");
    if (movable && p && (p === p.toUpperCase()) === (b.color === "w")) cls.push("movable");
    html += `<div class="${cls.join(" ")}" data-sq="${name}">` +
      (c === 0 ? `<span class="coord rank">${rank + 1}</span>` : "") + (r === 7 ? `<span class="coord file">${FILES[file]}</span>` : "") +
      (p ? pieceSvg(p).replace('class="pc', `class="pc${S.drag && S.drag.moved && S.drag.from === name ? " dragging" : ""}`) : "") + `</div>`;
  }
  if (b.arrow) html += arrow(b.arrow, flip);
  if (S.promo) {
    const c = (flip ? 7 - FILES.indexOf(S.promo.to[0]) : FILES.indexOf(S.promo.to[0])), top = (+S.promo.to[1] === 8) !== flip;
    const pieces = (b.color === "w" ? "QRBN" : "qrbn").split("");
    html += `<div id="promo" style="left:${c * 12.5}%;${top ? "top:0" : "bottom:0;flex-direction:column-reverse"}">` +
      pieces.map((p) => `<button data-promo="${p.toLowerCase()}" title="${p}">${pieceSvg(p)}</button>`).join("") + `</div>`;
  }
  b.el.innerHTML = html;
  for (const mark of ["review", "right", "wrong", "line"]) b.el.classList.toggle(mark, b.mark === mark);
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
function pickUp(ev) {
  if (S.review ? !S.review.card : !S.game) return;
  let b = onBoard();
  const promo = ev.target.closest("[data-promo]");
  if (promo) return b.send(S.promo.from + S.promo.to + promo.dataset.promo);
  if (S.promo) { S.promo = null; return renderBoard(); }
  if (b.pre) sendPremove(null);  // a touch of the board takes back the move made ahead (and may start another)
  let sq = squareAt(ev.clientX, ev.clientY);
  if (!sq || ev.button) return;
  const taking = S.sel && targets(S.sel).includes(sq), back = taking ? null : unanswer(b, sq);
  if (back) { b = onBoard(); sq = back; }
  if (!Object.keys(b.legal).length) return;
  ev.preventDefault();
  if (taking) return tryMove(S.sel, sq);
  const p = fenBoard(b.fen)[sqIndex(sq)];
  if (p && (p === p.toUpperCase()) === (b.color === "w")) {
    S.drag = { from: sq, piece: p, x: ev.clientX, y: ev.clientY, moved: false, again: S.sel === sq };
    S.sel = sq;
    try { b.el.setPointerCapture(ev.pointerId); } catch (e) { /* a synthetic event */ }
  } else S.sel = null;
  renderBoard();
}
function carry(ev) {
  const d = S.drag;
  if (!d) return;
  if (!d.moved && Math.hypot(ev.clientX - d.x, ev.clientY - d.y) < 5) return;
  if (!d.moved) {
    d.moved = true;
    const ghost = document.createElement("div"), side = onBoard().el.clientWidth / 8;
    ghost.id = "ghost"; ghost.innerHTML = pieceSvg(d.piece);
    ghost.style.width = ghost.style.height = `${side}px`; ghost.style.margin = `${-side / 2}px 0 0 ${-side / 2}px`;
    document.body.appendChild(ghost);
    renderBoard();
  }
  const ghost = $("ghost");
  ghost.style.left = `${ev.clientX}px`; ghost.style.top = `${ev.clientY}px`;
}
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
for (const el of [$("board"), $("card-board")]) {
  el.addEventListener("pointerdown", pickUp);
  el.addEventListener("pointermove", carry);
  el.addEventListener("pointerup", (ev) => endDrag(ev, false));
  el.addEventListener("pointercancel", (ev) => endDrag(ev, true));
}

function tryMove(from, to) {
  const b = onBoard(), found = Object.keys(b.legal).filter((u) => u.startsWith(from + to));
  if (found.length > 1) { S.promo = { from, to }; S.sel = null; return renderBoard(); }  // a pawn reaching the last rank
  if (found.length === 1) return b.send(found[0]);
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
// Your next move, made while the opponent is on its own (null: the one made is taken back). It is marked on the
// board, where the pieces stay: the server plays it once the opponent has moved, or drops it if it cannot be played.
function sendPremove(uci) {
  const id = S.gid;
  S.game.premove = uci; S.sel = null; S.promo = null;
  $("game-err").textContent = "";
  renderBoard();
  S.aheadSent = S.aheadSent.then(() => api("POST", `/api/games/${id}/premove`, { move: uci }))
    .catch((e) => { if (S.gid === id && S.game) { S.game.premove = null; $("game-err").textContent = e.message; renderBoard(); } });
}
async function act(fn) {
  $("game-err").textContent = "";
  try { await fn(); } catch (e) { $("game-err").textContent = e.message; }
}

// ---------- the engine's view ----------
function evalAt(ply) { const g = S.game; return ply === 0 ? g.start_eval : (g.moves[ply - 1] || {}).eval; }
function renderEval() {
  const l = linePos(), pos = shown(), held = pos.move && pos.move.held;  // the opponent's move, not answered yet: the bar stays on the position before it
  if (l) drawEval($("evalbar"), dead() ? S.evals.get(l.fen) : null, !dead());  // a line on the board: the engine's view of it, once the game is over
  else drawEval($("evalbar"), pos.ply <= S.game.moves.length ? evalAt(held ? pos.ply - 1 : pos.ply) : null, false);
}
// The bar under a board, for the evaluation of the position on it: White's share of it from the left. `off`: the
// board shows something the bar says nothing of (a line, while the game is being played).
function drawEval(bar, e, off) {
  const t = bar.querySelector(".evalbar-text");
  bar.classList.toggle("off", off);
  if (!e) { t.textContent = off ? "" : "…"; return; }  // not analysed yet: the bar stays where it was
  const whiteAhead = e.value >= 0;
  bar.querySelector(".evalbar-white").style.width = `${winPct(e)}%`;
  t.textContent = e.mate !== null && e.mate !== undefined ? (e.mate === 0 ? "#" : `M${Math.abs(e.mate)}`) : (Math.abs(e.cp) / 100).toFixed(1);
  t.classList.toggle("onblack", !whiteAhead);  // written at the end of the side that is ahead
  bar.title = `Engine evaluation: ${showEval(e)} for White (depth ${e.depth})`;
}
// What the engine makes of a position it has looked at for this board: its evaluation, how deep it has looked,
// and the line it expects.
function engineText(fen) {
  const e = S.evals.get(fen);
  return !e ? "" : `<b>${showEval(e)}</b>` + (e.depth ? ` · depth ${e.depth}` : "") + (e.line.length ? ` · ${esc(numbered(fenPly(fen), e.line))}` : "");
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
  const g = S.game, cur = linePos() ? -1 : S.view;  // with a line of Claude's on the board, none of the game's moves is
  let html = "";
  g.moves.forEach((m, i) => {
    if (i % 2 === 0) html += `<div class="num">${i / 2 + 1}.</div>`;
    const book = m.source === "book", graded = m.verdict !== undefined;
    const loss = book ? "book" : graded ? (m.verdict === "best" ? "0" : m.cpl) : m.held ? "" : "…";
    html += `<div class="mv${m.source === "user" ? " mine" : ""}${m.ply === cur ? " on" : ""}${m.explanation ? " explained" : ""} v-${book ? "book" : m.verdict || "wait"}" data-ply="${m.ply}" data-testid="move"
      title="${graded ? `${m.verdict}: lost ${m.cpl} centipawns` + (m.best && m.best.uci !== m.uci ? `; the engine preferred ${esc(m.best.san)}` : "") : m.held ? "" : "being analysed"}">
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
$("moves").onclick = (ev) => {
  const m = ev.target.closest(".mv");
  if (!m) return;
  setView(+m.dataset.ply);
  $("board").scrollIntoView({ block: "nearest" });  // on a phone the list is below the board, and the move is to be seen on it
  showWhy(+m.dataset.ply);
};
// Why a move of yours was bad, where that has been written (its box in the list is yellow): beside the move,
// from a click on it until the board shows another move or something else is clicked. Where there is no room
// beside the move (a phone) it is kept below the board, to be read with the board in sight.
function showWhy(ply) {
  const m = S.game.moves[ply - 1], el = $("moves").querySelector(`.mv[data-ply="${ply}"]`), tip = $("why");
  if (!m || !m.explanation || !el) return;
  S.why = ply;
  tip.textContent = m.explanation; tip.hidden = false;
  const box = el.getBoundingClientRect(), w = tip.offsetWidth, beside = box.right + 8 + w <= innerWidth;
  const under = $("board").getBoundingClientRect().bottom + 6;
  const ceil = beside || under < 8 || under > innerHeight - 128 ? 8 : under;  // the highest it may start
  tip.style.maxHeight = `${innerHeight - 8 - ceil}px`;  // what does not fit is scrolled to
  const h = tip.offsetHeight;
  // Under the move if it fits there; else at the foot of the screen, off the board and the lines below it, or above the move.
  const top = beside ? box.top : box.bottom + 6 + h <= innerHeight ? box.bottom + 6 : ceil > 8 ? innerHeight : box.top - 6 - h;
  tip.style.left = `${beside ? box.right + 8 : Math.max(8, Math.min(box.left, innerWidth - w - 8))}px`;
  tip.style.top = `${Math.max(ceil, Math.min(top, innerHeight - h - 8))}px`;
}
function hideWhy() { S.why = null; $("why").hidden = true; }
// A tap elsewhere puts it away. A drag does not, so the page can be scrolled with it open: the browser cancels
// the pointer when it takes a touch for scrolling, and a pointer that is let go far from where it went down is no tap.
let tapAt = null;
document.addEventListener("pointerdown", (ev) => { tapAt = ev.isPrimary ? ev : null; });
document.addEventListener("pointercancel", () => { tapAt = null; });
document.addEventListener("pointerup", (ev) => {
  const d = tapAt;
  tapAt = null;
  if (S.why === null || !d || d.pointerId !== ev.pointerId || Math.hypot(ev.clientX - d.clientX, ev.clientY - d.clientY) > 10) return;
  if (!d.target.closest("#why, #moves .mv")) hideWhy();
});
function renderInsight() {
  const g = S.game, m = S.view > 0 ? g.moves[S.view - 1] : null, l = linePos();
  if (l) {  // a line on the board: once the game is over, what the engine makes of where it has got to; until then, which line it is
    $("insight").innerHTML = dead() ? engineText(l.fen) : esc(l.line.name) + (l.move ? `${l.line.name ? " · " : ""}<b>${esc(moveLabel(l.set, l.nodes.length, l.move.san, true))}</b>` : "");
    return;
  }
  if (!m) { $("insight").innerHTML = g.moves.length ? "The starting position." : (g.turn === g.user_color ? "Your move." : ""); return; }
  const who = m.source === "user" ? "You played" : m.source === "book" ? "Opponent (opening book)" : "Opponent played";
  let html = `${who} <b>${moveNo(m.ply)} ${esc(m.san)}${GLYPH[m.verdict] || ""}</b>`;
  if (m.verdict === undefined) html += m.held ? "" : " · the engine is looking at it…";  // held: told with your reply
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
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const typing = ev.target.closest("input, textarea, select, #terminal"), l = linePos();
  const by = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity }[ev.key];
  if (l && !typing) {  // a line on the board, a game's or a card's: the keys step through it, Esc takes it off
    if (ev.key === "Escape") { ev.preventDefault(); setLine(null); }
    else if (by !== undefined) { ev.preventDefault(); stepLine(by); }
    return;
  }
  if (S.review && !typing) {  // the back of a card with lines listed: the keys start one, from the card's position where it is in it
    if (Math.abs(by) === 1 && cursor()) { ev.preventDefault(); stepLine(by); }
    return;
  }
  if (!S.game || $("game").hidden) return;
  if (ev.key === "Escape") hideWhy();
  if (typing) return;
  const to = { ArrowLeft: S.view - 1, ArrowRight: S.view + 1, Home: 0, End: S.game.moves.length }[ev.key];
  if (to !== undefined) { ev.preventDefault(); setView(to); }
});

// ---------- review: the cards of a deck, one at a time ----------
// S.review: the deck (`deck`, `name`, `active`: how many of its cards are active), its card on screen (`card`:
// null once none is active), when that was shown (`at`), your move on it while the server answers (`pending`),
// what the server said of it (`result`), and the moves made in the card's position: the answer first, then any
// tried there since (`tries`: each with its `cpl`, what it lost, once the server has said; `told`: how many of
// them Claude has been told of, `session`: the card's Claude session was asked for, to show on its back). Once
// it is answered the board is one to try moves on, from the card's position or from any move of a line, and what
// is tried is kept with the card (`explore`). The card and what came of it are kept for the tab (`keepReview`): a page loaded again is on
// the same card, as it was, and the next one comes only with Next or a grade. Unless the card is no longer
// there (its game was deleted, or the card was) or no longer that position: then the page is on the next one.
const KEPT = (deck) => `callie.review.${deck}`;
function keepReview() {
  const r = S.review;
  try {
    if (!r.card) return sessionStorage.removeItem(KEPT(r.deck));
    const tries = r.tries.map(({ uci, san, cpl }) => ({ uci, san, cpl }));
    sessionStorage.setItem(KEPT(r.deck), JSON.stringify({ name: r.name, active: r.active, card: r.card, result: r.result, tries, fast: r.fast }));
  } catch (e) { /* no storage */ }
}
async function showReview(deck) {
  leaveGame(); leaveReview();
  $("home").hidden = true; $("game").hidden = true; $("review").hidden = false;
  $("card-pane").hidden = true; $("card-done").hidden = true; $("card-err").textContent = "";
  $("crumb").textContent = "Review"; document.title = "Review · Callie";
  S.review = { deck, name: "", active: 0, card: null, at: 0, pending: null, result: null, tries: [], told: 0, session: false, fast: false, timer: 0 };
  claudeIn($("card-side"), "Ask Claude about the position…");
  loadMeta().catch(() => {});
  let kept = null;
  try { kept = JSON.parse(sessionStorage.getItem(KEPT(deck)) || "null"); } catch (e) { /* no storage */ }
  if (!kept || !kept.card) return nextCard();
  const r = S.review, now = await api("GET", `/api/cards/${kept.card.id}`).catch(() => null);
  if (S.review !== r) return;
  if (now && now.fen !== kept.card.fen) return nextCard();
  Object.assign(r, kept, { at: performance.now() - FAST_MS });  // seen before: an answer now is not a fast one
  renderReview();
  look();
  for (const t of r.tries) if (t.cpl === undefined) cardLoss(r, t);  // asked when the page went: asked again
}
function leaveReview() {
  if (S.review) { clearTimeout(S.review.timer); closeTerminal(); S.review = null; S.line = null; leaveBoard(); renderLines(); }  // the card's own lines go with it
  $("card-delete").hidden = true;
}
async function nextCard() {
  const r = S.review;
  if (!r) return;
  $("card-err").textContent = "";
  try {
    const d = await api("GET", `/api/cards/${r.deck}` + (r.card ? `?skip=${r.card.id}` : ""));
    if (S.review !== r) return;
    Object.assign(r, { name: d.name, active: d.active, card: d.card, wait: d.wait, pending: null, result: null, tries: [], told: 0, session: false, fast: false });
    S.sel = null; S.promo = null; S.line = null;
    leaveBoard();  // what was tried on the last card, and the engine's view of it
    showTerminal(null); $("compose-text").value = ""; $("claude-err").textContent = "";  // the last card's
    renderReview();
    r.at = performance.now();
    clearTimeout(r.timer);
    if (!d.card && d.wait !== null) r.timer = setTimeout(nextCard, Math.min(d.wait * 1000 + 500, 3600000));  // a card in a step: here when it is back
  } catch (e) { if (S.review === r) $("card-err").textContent = e.message; }
}
// Your move on the card. One made within FAST_MS is sent as an easy one: if it is right, the card is put away
// without the question of how hard it was, and stays on screen, like any other, until Next.
async function answerCard(uci) {
  const r = S.review, c = r.card, m = c.legal[uci];
  if (!m || r.pending || r.result) return;
  const fast = performance.now() - r.at < FAST_MS;
  r.pending = { uci, fen: m.fen, san: m.san }; S.sel = null; S.promo = null;
  $("card-err").textContent = "";
  renderReview();
  try {
    const res = await api("POST", `/api/cards/${c.id}/answer`, fast ? { move: uci, grade: "easy" } : { move: uci });
    if (S.review !== r) return;
    Object.assign(r, { pending: null, result: res, active: res.active, fast: fast && res.right });
    // The answer is the first of the lines tried on the card (unless it is a move of the card's own line), and
    // of the moves told what they lost.
    const w = where(), line = res.right ? w.nodes : [...w.nodes, res.move], set = allLines().find((b) => b.id === w.id);
    if (!set || lineOf(set, w.line, line.map((n) => n.uci)) < 0) keepLine({ tree: w.tree, fen: w.root, moves: line.map(({ uci, san, fen }) => ({ uci, san, fen })) });
    tried(uci);
    look();
    if (!res.right || r.fast) $("card-next").focus();
  } catch (e) { if (S.review === r) { r.pending = null; renderReview(); $("card-err").textContent = e.message; } }
}
// A move made in the position of a card that was answered: the answer itself, right or wrong, and any tried
// there since. Each is told what it lost, as the card reckons it. The card was put away by the answer: these
// change nothing of it.
function tried(uci) {
  const r = S.review;
  if (!r.result) return;
  let t = r.tries.find((t) => t.uci === uci);
  if (!t) r.tries.push(t = { uci, san: r.card.legal[uci].san });
  $("card-err").textContent = "";
  renderReview();
  if (t.cpl === undefined && !t.asked) cardLoss(r, t);
}
// What a move lost. Asked for after the move is shown: the engine may have to look at it first.
async function cardLoss(r, t) {
  const mine = () => S.review === r && r.tries.includes(t);  // still the card on screen
  t.asked = true;
  try {
    const d = await api("GET", `/api/cards/${r.card.id}/loss?move=${t.uci}`);
    t.cpl = d.cpl;
    if (mine()) { renderReview(); renderLines(); }
  } catch (e) { if (mine()) $("card-err").textContent = e.message; }
  t.asked = false;
}
async function gradeCard(grade) {
  const r = S.review;
  for (const g of GRADES) $(`card-${g}`).disabled = true;
  try {
    await api("POST", `/api/cards/${r.card.id}/answer`, { move: r.result.move.uci, grade });
    if (S.review === r) await nextCard();
  } catch (e) { if (S.review === r) $("card-err").textContent = e.message; }
  for (const g of GRADES) $(`card-${g}`).disabled = false;
}
for (const g of GRADES) $(`card-${g}`).onclick = () => gradeCard(g);
$("card-next").onclick = nextCard;
// A card that is not one to study (a move made for want of time, say) is taken out of its deck, and the next one comes.
$("card-delete").onclick = async () => {
  const r = S.review;
  if (!r || !r.card || !confirm("Delete this card?")) return;
  $("card-delete").disabled = true;
  try {
    await api("POST", `/api/cards/${r.card.id}/delete`);
    if (S.review === r) await nextCard();
  } catch (e) { if (S.review === r) $("card-err").textContent = e.message; }
  $("card-delete").disabled = false;
};
function renderReview() {
  const r = S.review, c = r.card, res = r.result;
  $("card-pane").hidden = !c; $("card-done").hidden = !!c;
  const mins = Math.ceil((r.wait || 0) / 60);
  $("card-wait").textContent = c || r.wait === null ? "" : `Next card in ${mins < 2 ? "a minute" : `${mins} minutes`}.`;
  // The deck and how many of its cards are active go in the bar at the top: beside the board is only what came of your answer.
  $("crumb").innerHTML = "Review" + (r.name ? ` · <span id="card-deck" data-testid="card-deck">${esc(r.name)}</span> <b id="card-count" data-testid="card-count" title="Active cards in this deck">${r.active}</b>` : "");
  keepReview();
  renderLines();  // the card's own, once it is answered
  $("card-delete").hidden = !c;  // in the bar at the top, answered or not
  if (!c) return;
  renderBoard();
  renderEngine();
  $("card-result").hidden = !res;
  $("card-why").hidden = !res || !res.explanation; $("card-why").textContent = res && res.explanation || "";
  const shows = res && (res.heuristic || res.opener) || "";  // of a card no game made: the heuristic its position shows, or the opening it is of
  $("card-heuristic").hidden = !shows; $("card-heuristic").textContent = shows;
  for (const g of GRADES) {  // how it was to remember, each with how long it hides the card for
    $(`card-${g}`).hidden = !res || !res.right || r.fast;
    $(`card-${g}`).querySelector("small").textContent = res && res.next ? res.next[g] : "";
  }
  $("card-next").hidden = !res || (res.right && !r.fast);
  $("card-grade").hidden = !res;
  $("claude-pane").hidden = !res;  // Claude is asked from the back of the card, and what was asked before is there
  if (!res) return;
  if (!r.session) { r.session = true; cardSession(r); }
  loadExplored();
  // One line: your answer with what it lost, once that is known (and that it is the engine's first choice, when
  // it is), then the engine's first choice, after a right answer that was another move, and the move of the game
  // with what that lost (a heuristic's card has none, nor an opener's). What else is tried on the board is in the
  // lines listed (see renderLines).
  const p = res.played, mine = r.tries[0] || {}, best = res.best.uci === res.move.uci;
  $("card-mine").className = res.right ? "right" : "wrong";
  $("card-mine").textContent = `${res.right ? "✓" : "✗"} ${res.move.san}` + (mine.cpl === undefined ? "" : ` ${lost(mine.cpl)} cp`) + (best ? " · best" : "");
  $("card-played").innerHTML = [res.right && !best ? `best <b>${esc(res.best.san)}</b>` : "", p ? `you played <b>${esc(p.san)}${GLYPH[p.deck] || ""}</b> −${p.cpl} cp` : ""].filter(Boolean).join(" · ");
}
// The engine's view of what is on the back of a card: the bar under its board, and under that what it expects.
function renderEngine() {
  const r = S.review, back = !!(r && r.card && r.result);
  $("card-evalbar").hidden = $("card-engine").hidden = !back;
  if (!back) return;
  const fen = onBoard().fen;
  drawEval($("card-evalbar"), S.evals.get(fen), false);
  $("card-engine").innerHTML = engineText(fen);
}

// ---------- Claude ----------
// A Claude Code session in a terminal, shown with xterm.js. Nothing starts one but a message sent from the box
// here; once a game has one, it is shown whenever the game is. A card has one of its own, asked from the back of
// the card and shown there whenever that is. The page has the one pane, in what is on screen: the game, where it
// is the game's session, or the side of a card, where it is the card's.
function claudeIn(el, ask) {
  if ($("claude-pane").parentNode !== el) { el.append($("claude-pane")); $("compose-text").value = ""; }
  $("compose-text").placeholder = ask; $("claude-err").textContent = "";
  $("claude-pane").hidden = false;
  showTerminal(null);
}
function paneSize() {
  if (S.term) return { cols: S.term.xterm.cols, rows: S.term.xterm.rows };
  // No terminal yet: the pane holds only the box. The size is that of the terminal it grows to hold (style.css).
  const font = TOUCH.matches ? 12 : 13, width = $("claude-pane").clientWidth;
  const height = S.review ? (PHONE.matches ? innerHeight * 0.7 : Math.max($("card-board").clientHeight, 440) - $("card-side").offsetHeight)
    : (PHONE.matches ? innerHeight * 0.78 : $("game").clientHeight - 24) - $("compose").offsetHeight;
  return { cols: Math.max(40, Math.floor((width - 24) / (font * 0.61))), rows: Math.max(12, Math.floor((height - 12) / (font * 1.25))) };
}
function showTerminal(id) {  // the terminal of that number in the pane; null: none
  if (id && (!S.term || S.term.id !== id)) openTerminal(id);
  if (!id && S.term) closeTerminal();
  $("terminal").hidden = !id;
  renderKeys();
}
function syncClaude() {
  const c = S.game.claude, id = c.terminal;
  showTerminal(id);
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
// The session of the card on screen, if the card has one: on its back as it was left, each time that is shown.
async function cardSession(r) {
  const c = r.card;
  try {
    const d = await api("POST", `/api/cards/${c.id}/claude/resume`, paneSize());
    if (S.review === r && r.card === c && d.terminal) showTerminal(d.terminal);
  } catch (e) { /* no Claude Code: a message sent says so */ }
}
async function claude(path, body) {
  $("claude-err").textContent = "";
  const r = S.review, c = r && r.card;
  try {
    const d = await api("POST", `/api/${c ? `cards/${c.id}` : `games/${S.gid}`}/claude/${path}`, { ...paneSize(), ...(body || {}) });
    if (c) { if (S.review === r && r.card === c) showTerminal(d.terminal); }
    else if (d.terminal && S.game) { S.game.claude.terminal = d.terminal; S.game.claude.started = true; syncClaude(); }
    return d;
  } catch (e) { $("claude-err").textContent = e.message; throw e; }
}
async function composeSend() {
  const text = $("compose-text").value, r = S.review;
  if (!text.trim() || !(r ? r.result : S.gid)) return;
  $("compose-text").value = "";
  // From a card, with the moves made in its position since Claude last heard: "why not this?" is about one of them.
  const tried = r ? r.tries.slice(r.told).map((t) => ({ move: t.uci, cpl: t.cpl })) : [];
  // With a line on the board, what is on it: where the line starts and its moves to the position shown.
  const l = linePos(), board = l ? { fen: l.set.fen, moves: S.line.path } : undefined;
  try {
    await claude("say", r ? { text, tried, board } : { text, board });
    if (r) r.told += tried.length;
  } catch (e) { $("compose-text").value = text; }
}
$("compose-send").onclick = composeSend;
$("compose-text").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); composeSend(); } });

// ---------- lines: what Claude put on the board, and what you tried there ----------
// A session's `show_lines` tool gives the server a position and lines from it, and the session's terminal says so
// on its stream. They are listed above the terminal, each move a button: a click puts the position after it on
// the board beside the pane (a game's, or a card's) in place of what was there, and the buttons under the list,
// or the arrow keys, step through the line. Everything a session showed is kept, to page back through.
// On a phone the pane is a screen below the board, and what is used together is to be on one screen: there the
// list is directly under the board (a game's: under the buttons that step through its moves).
// The back of a card no game made has a line of its own, with no session: the moves that led to its position and
// the ones that follow from it (`result.shown`). It is listed in the same place, before what Claude shows there.
// Such a line says how many of its moves lead to the card's position (`at`): that move is marked in the list, and
// stepping starts from it, onwards or back, while the board is still the card's.
// On a board that is not being played (`dead`) a move can be made from any of those positions, and from the
// board's own. A move that is not the next of the line it is made on is a line of your own (`S.explored`: each
// the `tree` it grew from, "own" for a position of the board's own or "s" and the id of what Claude showed, the
// `fen` that starts from, and its `moves` from there), kept by the server with the board. What is listed is
// those grown together (`allLines`): each position with lines from it is a tree, in which a move has what
// follows it (`kids`): the line's own next move first, so that stepping on keeps to it, then what you tried
// there instead. A position of the board's own that you tried moves from is listed as one more, before Claude's.
// Each move is coloured by what it lost, as the engine reckons it, and the one on the board says the number.
const boardApi = () => S.review ? `cards/${S.review.card.id}` : `games/${S.gid}`;
function leaveBoard() {  // what was tried on a board and the engine's view of it go with the board
  if (looking) looking.abort();
  looking = null;
  S.explored = []; S.exploredOf = null;
  S.evals.clear(); S.legal.clear();
}
async function loadExplored() {  // the lines of your own that are kept for the board on screen, once it is not being played
  const of = dead() ? boardApi() : null;
  if (!of || S.exploredOf === of) return;
  S.exploredOf = of;
  try {
    const d = await api("GET", `/api/${of}/explored`), since = S.explored;
    if (S.exploredOf !== of) return;
    S.explored = d.explored;
    for (const x of since) addExplored(x);  // tried while the server was asked
    if (S.line && !linePos()) S.line = null;
    redraw(); renderLines(); look();
  } catch (e) { /* none to list */ }
}
// One that goes on from a line kept before takes its place, and one that is the start of a line kept before adds
// nothing: as the server keeps them (store.py), so that both list them in the same order.
function addExplored(x) {
  const same = (a) => a.tree === x.tree && a.fen === x.fen, ucis = (a) => a.moves.map((m) => m.uci).join(" ") + " ", mine = ucis(x);
  if (S.explored.some((a) => same(a) && ucis(a).startsWith(mine))) return;
  const i = S.explored.findIndex((a) => same(a) && mine.startsWith(ucis(a)));
  S.explored = i < 0 ? [...S.explored, x] : S.explored.map((a, k) => k === i ? x : a);
}
function keepLine(x) {
  addExplored(x);
  api("POST", `/api/${boardApi()}/explored`, { tree: x.tree, fen: x.fen, moves: x.moves.map((m) => m.uci) })
    .catch((e) => { $(S.review ? "card-err" : "game-err").textContent = e.message; });
}
function startTitle(fen) {  // what a position of the board's own is listed as, with the lines you tried from it
  if (S.review) return "From the card";
  const m = S.game.moves.find((m) => m.fen === fen);
  return m ? `After ${moveNo(m.ply)} ${m.san}` : "From the start";
}
// A position and its lines as a tree, with the lines of your own that grew from it: one goes with the line it
// follows furthest (the first of those), and one that follows none is a line by itself, with no name.
function grown(b, tried) {
  const node = (m, mine) => ({ uci: m.uci, san: m.san, fen: m.fen, mine, kids: [] });
  const lines = b.lines.map((line) => {
    const root = { kids: [] };
    let at = root;
    for (const m of line.moves) { const n = node(m, false); at.kids.push(n); at = n; }
    return { name: line.name, at: line.at, root };
  });
  const shared = (line, moves) => {  // how many of those moves the line has, from its start
    let at = line.root, n = 0;
    for (const m of moves) { at = at.kids.find((k) => k.uci === m.uci); if (!at) break; n++; }
    return n;
  };
  for (const x of tried) {
    let into = null, most = 0;
    for (const line of lines) { const n = shared(line, x.moves); if (n > most) { into = line; most = n; } }
    if (!into) lines.push(into = { name: "", mine: true, root: { kids: [] } });
    let at = into.root;
    for (const m of x.moves) {
      let k = at.kids.find((k) => k.uci === m.uci);
      if (!k) at.kids.push(k = node(m, true));
      at = k;
    }
  }
  return { ...b, lines };
}
let listedOf = [], listed = [];
function allLines() {  // every position with lines from it that the board on screen has: its own, then Claude's
  const r = S.review, of = [S.shown, S.explored, r && r.result, r ? r.card : S.game];
  if (of.every((x, i) => x === listedOf[i])) return listed;
  listedOf = of;
  const tree = dead(), own = (r && r.result && r.result.shown || []).map((b, i) => ({ ...b, id: `card-${i}`, tree: "own" }));
  for (const x of tree ? S.explored : []) {
    if (x.tree === "own" && !own.some((b) => b.fen === x.fen)) own.push({ id: `own ${x.fen}`, tree: "own", fen: x.fen, title: startTitle(x.fen), lines: [] });
  }
  const mine = (b) => !tree ? [] : S.explored.filter((x) => x.tree === b.tree && x.fen === b.fen && (b.tree !== "own" || own.find((o) => o.fen === b.fen) === b));
  listed = [...own, ...S.shown.map((b) => ({ ...b, tree: `s${b.id}` }))].map((b) => grown(b, mine(b)));
  return listed;
}
function placeLines() {
  const el = $("lines"), over = !PHONE.matches ? null : S.review ? $("card-engine") : $("nav");
  if (over) { if (over.nextElementSibling !== el) over.after(el); }
  else if ($("claude-pane").firstElementChild !== el) $("claude-pane").prepend(el);
}
PHONE.addEventListener("change", () => renderLines());
async function loadLines(t) {
  let d;
  try { d = await api("GET", `/api/terminals/${t.id}/lines`); } catch (e) { return; }  // it has ended: its stream says so
  if (S.term !== t) return;
  const last = (list) => list.length ? list[list.length - 1].id : null, more = last(d.shown) !== last(S.shown);
  S.shown = d.shown;
  if (S.line && !linePos()) { S.line = null; redraw(); }  // no longer kept
  // What is new is what is listed, unless a line is being stepped through.
  if (!allLines().some((b) => b.id === S.shownAt) || (more && !S.line)) S.shownAt = last(allLines());
  renderLines();
  look();
}
function follow(line, path) {  // the moves of a line along a path of them (null: it has no such moves)
  const out = [];
  let at = line.root;
  for (const u of path) {
    at = at.kids.find((k) => k.uci === u);
    if (!at) return null;
    out.push(at);
  }
  return out;
}
// Which line of a set has the moves of a path: the one said, if it has, else the first that has (-1: none).
const lineOf = (set, j, path) => set.lines[j] && follow(set.lines[j], path) ? j : set.lines.findIndex((ln) => follow(ln, path));
function linePos() {  // the line on the board and where it has got to; null: the board is the game's, or the card's
  const l = S.line, set = l && allLines().find((b) => b.id === l.set), line = set && set.lines[l.line], nodes = line && follow(line, l.path);
  if (!nodes) return null;
  const m = nodes[nodes.length - 1] || null, p = nodes[nodes.length - 2];
  // `from`: the position its last move was made in; `kids`: the moves that go on from where it has got to
  return { set, line, nodes, move: m, kids: (m || line.root).kids, fen: m ? m.fen : set.fen, last: m ? m.uci : null, san: m ? m.san : "", from: p ? p.fen : set.fen };
}
// A move of a set's lines as it is written: "12. Nf3", "Nc6", and "12... Nc6" where Black starts a row (`lead`).
// `depth`: how many moves into the line it is.
function moveLabel(set, depth, san, lead) {
  const ply = fenPly(set.fen) + depth - 1;
  return (lead || ply % 2 ? moveNo(ply) + " " : "") + san;
}
function redraw() {  // the board on screen, and what is written around it
  if (S.review) { if (S.review.card) { renderBoard(); renderEngine(); } }
  else if (S.game) render();
}
function setLine(l) {  // null: what was on the board is back
  S.line = l; S.sel = null; S.promo = null;
  if (l) S.shownAt = l.set;
  hideWhy();
  redraw();
  renderLines();
  look();
}
// Where the board is, as a place in a tree, for a move made there to go on from: the line on it and its moves so
// far (`nodes`); with none, the board's own position. That is the start of a tree of its own (`line` -1: of no
// line yet), but on a card with a line of its own it is `at` moves into that line; and after a right answer the
// answer has been made on it.
function where() {
  const l = linePos();
  if (l) return { id: l.set.id, tree: l.set.tree, root: l.set.fen, line: S.line.line, nodes: l.nodes };
  const r = S.review, fen = r ? r.card.fen : shown().fen, made = r && r.result.right ? [r.result.move] : [];
  for (const set of r ? allLines() : []) {
    const j = set.tree === "own" ? set.lines.findIndex((ln) => ln.at !== undefined) : -1;
    if (j < 0) continue;
    const led = [];
    for (let at = set.lines[j].root; led.length < set.lines[j].at; at = at.kids[0]) led.push(at.kids[0]);
    return { id: set.id, tree: "own", root: set.fen, line: j, nodes: [...led, ...made] };
  }
  return { id: `own ${fen}`, tree: "own", root: fen, line: -1, nodes: made };
}
// A move made on a board that is not being played. The next move of the line it is made on (or of the game) is
// stepped to, and so is one tried there before; any other is a new line of your own, kept from then on.
function explore(uci) {
  const b = onBoard(), m = b.legal[uci];
  if (!m) return;
  S.sel = null; S.promo = null;
  $(S.review ? "card-err" : "game-err").textContent = "";
  if (!S.review && !linePos() && (S.game.moves[S.view] || {}).uci === uci) return setView(S.view + 1);
  if (S.review && b.fen === S.review.card.fen) tried(uci);  // in the card's position: told what it lost as the card reckons it
  const w = where(), path = [...w.nodes.map((n) => n.uci), uci];
  const has = () => { const set = allLines().find((s) => s.id === w.id); return set ? lineOf(set, w.line, path) : -1; };
  if (has() < 0) keepLine({ tree: w.tree, fen: w.root, moves: [...w.nodes, { uci, ...m }].map(({ uci, san, fen }) => ({ uci, san, fen })) });
  setLine({ set: w.id, line: has(), path });
}
// A right answer stands on the back of its card, and what moves there is the other side. One of your own pieces
// picked up there (on `sq` of the board `b`) takes the answer back: the card's position is on the board, as the
// place before the answer in the lines listed, for another move of yours to be tried in it. What is returned is
// the square that piece is on there (the one that made the answer: where it came from); null: nothing was taken back.
function unanswer(b, sq) {
  const r = S.review, made = r && r.result && r.result.right && !linePos() ? r.result.move : null;
  const p = made && fenBoard(b.fen)[sqIndex(sq)];
  if (!p || (p === p.toUpperCase()) === (b.color === "w")) return null;
  const w = where(), path = w.nodes.map((n) => n.uci);
  const has = () => { const set = allLines().find((s) => s.id === w.id); return set ? lineOf(set, w.line, path) : -1; };
  if (has() < 0) keepLine({ tree: w.tree, fen: w.root, moves: w.nodes.map(({ uci, san, fen }) => ({ uci, san, fen })) });  // the answer's line, not there yet (a page just loaded)
  setLine({ set: w.id, line: has(), path: path.slice(0, -1) });
  return sq === made.uci.slice(2, 4) ? made.uci.slice(0, 2) : sq;
}
// Where stepping goes on from: the line on the board (`on`); with none, the board's own place in what is listed,
// if it is in it, and else the start of the first line listed. null: nothing is listed.
function cursor() {
  const l = linePos();
  if (l) return { set: l.set, line: S.line.line, path: S.line.path, kids: l.kids, on: true };
  const set = allLines().find((b) => b.id === S.shownAt);
  if (!set || !set.lines.length) return null;
  if (dead()) {
    const w = where(), path = w.nodes.map((n) => n.uci), j = w.id === set.id && path.length ? lineOf(set, w.line, path) : -1;
    if (j >= 0) { const nodes = follow(set.lines[j], path); return { set, line: j, path, kids: nodes[nodes.length - 1].kids, on: false }; }
  }
  return { set, line: 0, path: [], kids: set.lines[0].root.kids, on: false };
}
// A step along what is listed: back a move (-1), on a move (1: the line's own next, where you tried others
// too), to the start (-Infinity) or to the end (Infinity).
function stepLine(by) {
  const c = cursor();
  if (!c || (!c.on && !(by < 0 ? c.path : c.kids).length)) return;
  const path = by === -1 ? c.path.slice(0, -1) : by < 0 ? [] : [...c.path];
  for (let kids = c.kids; by > 0 && kids[0] && (by > 1 || path.length === c.path.length); kids = kids[0].kids) path.push(kids[0].uci);
  setLine({ set: c.set.id, line: c.line, path });
}
// What a move lost, in centipawns, made in the position `from`: the engine's view of that position against its
// view of the one after the move, from the mover's side and inside ten pawns either way, as a game's moves are
// reckoned. undefined: the engine has not looked at both yet.
function lossOf(from, m) {
  const r = S.review, t = r && r.card && from === r.card.fen && r.tries.find((t) => t.uci === m.uci);
  if (t && t.cpl !== undefined) return t.cpl;  // in the card's position: as the card reckons it
  const a = S.evals.get(from), b = S.evals.get(m.fen);
  if (!a || !b) return undefined;
  if (a.best && a.best.uci === m.uci) return 0;
  const side = from.split(" ")[1] === "w" ? 1 : -1, cap = (v) => Math.max(-1000, Math.min(1000, v));
  return Math.max(0, cap(side * a.value) - cap(side * b.value));
}
// The colour of a move for what it lost: deep green for nothing, through yellow at a pawn, to deep red at three or more.
const TINTS = [[0, 135], [50, 85], [100, 52], [200, 22], [300, 0]];  // centipawns lost, and the hue there
function tint(cpl) {
  const i = Math.max(1, TINTS.findIndex(([at]) => cpl <= at));
  if (cpl >= 300) return "hsl(0 80% 52%)";
  const [a, ha] = TINTS[i - 1], [b, hb] = TINTS[i];
  return `hsl(${Math.round(ha + (hb - ha) * (cpl - a) / (b - a))} ${cpl ? 66 : 72}% ${cpl ? 54 : 46}%)`;
}
function renderLines() {
  const all = allLines(), set = all.find((b) => b.id === S.shownAt) || all[all.length - 1];  // a card's own, before any is picked
  $("lines").hidden = !set;
  if (!set) return;
  S.shownAt = set.id;
  placeLines();
  const i = all.indexOf(set), n = all.length, l = linePos(), on = l && l.set.id === set.id ? S.line : null, at = on ? on.path.join(" ") : null, graded = dead();
  $("lines-title").textContent = set.title;
  $("lines-pager").hidden = n < 2;
  $("lines-n").textContent = `${i + 1}/${n}`;
  $("lines-older").disabled = i === 0; $("lines-newer").disabled = i === n - 1;
  // A line's moves in a row. Where more than one move was made in a position the row ends there, and under it is
  // a row for each of them with what follows it: the line's own first, then what you tried there.
  const row = (j, line, kids, path, from, lead) => {
    const one = (m, lead) => {
      const p = [...path, m.uci], cpl = graded ? lossOf(from, m) : undefined;
      const is = on && on.line === j && at === p.join(" "), cls = [is ? "on" : "", m.mine ? "mine" : line.at === p.length ? "here" : ""].join(" ").trim();  // `here`: the card's position
      // its colour is what it lost; the one on the board has it behind the move instead
      return `<button class="${cls}" data-path="${p.join(" ")}" data-testid="line-move"` + (cpl === undefined ? "" : ` style="${is ? "color:#0d1117;background" : "color"}:${tint(cpl)}" title="${lost(cpl)} cp"`) +
        `>${esc(moveLabel(set, p.length, m.san, lead))}</button>` + row(j, line, m.kids, p, m.fen, false);
    };
    return !kids.length ? "" : kids.length === 1 ? one(kids[0], lead) : `<div class="fork">` + kids.map((m) => `<div class="sub">${one(m, true)}</div>`).join("") + `</div>`;
  };
  $("lines-list").innerHTML = set.lines.map((line, j) =>
    `<div class="line" data-line="${j}" data-testid="line">` +
    (line.name ? `<button class="name${on && on.line === j && !at ? " on" : ""}${line.at === 0 ? " here" : ""}" data-path="">${esc(line.name)}</button>` : "") +
    row(j, line, line.root.kids, [], set.fen, true) + `</div>`).join("");
  const list = $("lines-list"), moved = list.querySelector("button.on");
  if (moved) {  // kept in sight in the list, which scrolls by itself: the page stays where it is
    const a = moved.getBoundingClientRect(), box = list.getBoundingClientRect();
    list.scrollTop += a.top < box.top ? a.top - box.top : Math.max(0, a.bottom - box.bottom);
  }
  const c = cursor();
  $("line-first").disabled = !l || !S.line.path.length;
  $("line-prev").disabled = !c || !c.path.length;  // with none on the board, these two start one
  $("line-next").disabled = !c || !c.kids.length;
  $("line-last").disabled = !l || !l.kids.length;
  // The move on the board first, with what it lost: where the row is short (a phone), it is the name that is cut.
  const cpl = l && l.move && graded ? lossOf(l.from, l.move) : undefined;
  $("line-at").innerHTML = !l ? "" : (l.move ? `<b>${esc(moveLabel(l.set, l.nodes.length, l.move.san, true))}</b>` : "") +
    (cpl === undefined ? "" : `<span class="lost" style="color:${tint(cpl)}" data-testid="line-lost">${lost(cpl)} cp</span>`) +
    (l.line.name ? `<span class="of">${l.move ? "· " : ""}${esc(l.line.name)}</span>` : "");
  $("line-close").hidden = !l;
  $("line-close").title = `Back to the ${S.review ? "card" : "game"} (Esc)`;
}
// A move put on the board from the list or its buttons is to be seen there, with the list still on screen. On a
// phone they are under the board and either may be partly off the screen: the list is brought on, then the board
// (which is the one to be whole, where the screen is too short for both).
function lineInSight() {
  $("lines").scrollIntoView({ block: "nearest" });
  onBoard().el.scrollIntoView({ block: "nearest" });
}
$("lines-list").onclick = (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  setLine({ set: S.shownAt, line: +b.closest(".line").dataset.line, path: b.dataset.path ? b.dataset.path.split(" ") : [] });
  lineInSight();
};
function pageLines(by) {  // what was shown before, or after: listed, and the line on the board put away
  const all = allLines(), i = all.findIndex((b) => b.id === S.shownAt) + by;
  if (i < 0 || i >= all.length) return;
  S.shownAt = all[i].id;
  setLine(null);
}
$("lines-older").onclick = () => pageLines(-1);
$("lines-newer").onclick = () => pageLines(1);
$("line-first").onclick = () => stepLine(-Infinity);
$("line-prev").onclick = () => stepLine(-1);
$("line-next").onclick = () => stepLine(1);
$("line-last").onclick = () => stepLine(Infinity);
for (const b of $("line-nav").querySelectorAll("button:not(#line-close)")) b.addEventListener("click", lineInSight);
$("line-close").onclick = () => setLine(null);

// ---------- the engine on a board that is not being played ----------
// The positions of the lines that are listed do not change, so the server analyses each once, in the background,
// and keeps that (`kept`): the page asks for what there is of them in one question, and it is what colours their
// moves and what is shown of one that is on the board, the same every time. A position that has none yet (a move
// just made, a line just shown) is asked about live meanwhile: the one on the board for the bar under it, the
// arrow and the line it expects, again and again while it stays, each time for longer (LOOK_MS), and, between
// the second look and the last, every other one of the lines listed, briefly. One question at a time: the
// server's engine drops a search for the next one asked. A position of the game itself has its analysis
// already, and is not asked about. What can be moved in the position on the board is asked for first.
let looking = null;  // the AbortController of the questions under way
async function seen(fen, ms, signal) {  // the engine's view of a position after a look that long: the longest look of each is kept
  const have = S.evals.get(fen);
  if (have && (have.over || have.ms >= ms)) return;
  const e = await api("GET", `/api/position/eval?fen=${encodeURIComponent(fen)}&ms=${ms}`, null, signal);
  if (e.best || e.over) S.evals.set(fen, { ...e, ms: e.kept ? Infinity : ms });  // neither: the search was cut short by another's (a second tab)
}
function look() {
  if (looking) looking.abort();
  looking = null;
  if (!dead()) return;
  const r = S.review, ctl = looking = new AbortController(), fen = onBoard().fen, own = !r && !linePos();
  const here = () => looking === ctl, drawn = () => { if (here()) { redraw(); renderLines(); } };
  if (r && !S.legal.has(r.card.fen)) S.legal.set(r.card.fen, r.card.legal);
  (async () => {
    try {
      if (!S.legal.has(fen)) {
        const p = await api("GET", `/api/position?fen=${encodeURIComponent(fen)}`, null, ctl.signal);
        S.legal.set(fen, p.legal);
        if (here()) renderBoard();
      }
      const set = allLines().find((b) => b.id === S.shownAt), rest = set ? [set.fen] : [];
      const walk = (kids) => { for (const m of kids) { rest.push(m.fen); walk(m.kids); } };
      for (const line of set ? set.lines : []) walk(line.root.kids);
      const fens = [...new Set(own ? rest : [fen, ...rest])].filter((f) => (S.evals.get(f) || {}).ms !== Infinity);  // a live look gives way to the analysis, once that is made
      if (fens.length) {
        const d = await api("POST", "/api/position/evals", { fens }, ctl.signal);
        for (const [f, e] of Object.entries(d.evals)) S.evals.set(f, { ...e, ms: Infinity });
        drawn();
      }
      for (const ms of own ? [] : LOOK_MS.slice(0, 2)) { await seen(fen, ms, ctl.signal); drawn(); }
      for (const f of rest) if (!S.evals.has(f)) { await seen(f, LOOK_MS[0], ctl.signal); drawn(); }
      if (!own) { await seen(fen, LOOK_MS[2], ctl.signal); drawn(); }
    } catch (e) { if (here()) $(S.review ? "card-err" : "game-err").textContent = e.message; }
  })();
}

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
  // What its session put on the board goes with it, off the board too.
  const was = S.line;
  S.shown = [];
  if (was && !linePos()) S.line = null;
  renderLines();
  if (was && !S.line) redraw();
}
// The terminal's output, for as long as the tab is shown (a browser allows a site only a few connections).
function listen(t) {
  if (t.source || document.hidden) return;
  const src = t.source = new EventSource(`/api/terminals/${t.id}/stream`);
  // Each connection starts with the terminal's state: after the first, that is written over a cleared screen.
  src.onopen = () => { if (t.opened++) t.xterm.reset(); t.fresh = true; loadLines(t); };
  src.addEventListener("lines", () => loadLines(t));
  src.onmessage = (ev) => {
    const bytes = Uint8Array.from(atob(ev.data), (c) => c.charCodeAt(0));
    if (!t.fresh) return t.xterm.write(bytes);
    t.fresh = false;
    t.xterm.write(bytes, () => t.resize(true));  // a viewer that has just arrived has none of a full-screen program's screen
  };
  const ended = () => {
    if (S.term !== t) return;
    closeTerminal();
    if (S.game) { S.game.claude.terminal = null; syncClaude(); } else showTerminal(null);
  };
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
