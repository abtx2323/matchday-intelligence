"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const state = { es: null, meta: null, mode: "casual", t: 0, running: false, overlays: [], teamColor: {}, lowerTimer: null, fanTimer: null };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function playerName(id) {
    if (!state.meta || !id) return id || "";
    for (const side of ["home", "away"]) {
      const p = state.meta[side].players.find((x) => x.id === id);
      if (p) return p.name;
    }
    return id;
  }
  function teamShort(id) {
    if (!state.meta) return id;
    if (state.meta.home.id === id) return state.meta.home.short;
    if (state.meta.away.id === id) return state.meta.away.short;
    return id;
  }

  // ---------------------------------------------------------------- controls
  async function loadMeta() {
    const seed = Number($("seed").value) || 0;
    const res = await fetch(`/api/match/${encodeURIComponent(seed)}`);
    if (!res.ok) return;
    state.meta = await res.json();
    const club = $("club"), player = $("player");
    const prevClub = club.value, prevPlayer = player.value;
    clear(club); clear(player);
    club.appendChild(new Option("None", ""));
    player.appendChild(new Option("None", ""));
    for (const side of ["home", "away"]) {
      const t = state.meta[side];
      state.teamColor[t.id] = t.color;
      club.appendChild(new Option(t.name, t.id));
      const group = document.createElement("optgroup");
      group.label = t.name;
      for (const p of t.players) group.appendChild(new Option(`${p.number}. ${p.name} (${p.position})`, p.id));
      player.appendChild(group);
    }
    if ([...club.options].some((o) => o.value === prevClub)) club.value = prevClub;
    if ([...player.querySelectorAll("option")].some((o) => o.value === prevPlayer)) player.value = prevPlayer;
  }

  function params(start) {
    const p = new URLSearchParams({
      seed: String(Number($("seed").value) || 0), mode: state.mode, lang: $("lang").value,
      speed: $("speed").value, start: String(Math.max(0, Math.floor(start || 0))),
      faults: $("faults").checked ? "0.35" : "0",
    });
    if ($("club").value) p.set("club", $("club").value);
    if ($("player").value) p.set("player", $("player").value);
    return p;
  }

  function stop() {
    if (state.es) { state.es.close(); state.es = null; }
    state.running = false;
    $("play").textContent = "▶ Resume";
  }

  function start(fromT) {
    stop();
    if (!fromT) resetView();
    const es = new EventSource(`/api/stream?${params(fromT)}`);
    state.es = es; state.running = true;
    $("play").textContent = "❚❚ Pause";
    es.addEventListener("meta", (m) => {
      const d = JSON.parse(m.data);
      $("providerBadge").textContent = `AI voice: ${d.provider}`;
    });
    es.addEventListener("overlays", (m) => handleEnvelope(JSON.parse(m.data)));
    es.addEventListener("end", (m) => {
      let d = {};
      try { d = JSON.parse(m.data) || {}; } catch (_e) { d = {}; }
      stop(); // close first so EventSource never auto-reconnects
      if (d.reason === "busy") {
        $("agentSummary").textContent = "Server busy: too many live streams. Try again shortly.";
        $("play").textContent = "▶ Retry";
        return;
      }
      renderAgentSummary(d.trace);
      $("play").textContent = "↺ Replay";
      state.t = 0;
    });
    es.onerror = () => { if (state.running) { stop(); } };
  }

  // Changing audience settings keeps the match position: same deterministic match, new viewer.
  function restartKeepingPosition() {
    if (state.running) start(state.t);
  }

  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    state.mode = b.dataset.mode;
    restartKeepingPosition();
  }));
  ["lang", "club", "player", "speed", "faults"].forEach((id) => $(id).addEventListener("change", restartKeepingPosition));
  $("seed").addEventListener("change", async () => { stop(); state.t = 0; await loadMeta(); resetView(); $("play").textContent = "▶ Kick off"; });
  $("play").addEventListener("click", () => {
    if (state.running) { stop(); return; }
    start(state.t);
  });
  $("compareBtn").addEventListener("click", showCompare);
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${t.dataset.tab}`));
  }));

  function resetView() {
    clear($("ticker")); clear($("trace")); clear($("markers")); clear($("tags"));
    ["scorebug", "momentum", "lowerThird", "fanAlert", "recap", "playerCard", "compare"].forEach((id) => $(id).classList.add("hidden"));
    $("statPanel").textContent = "Stats appear as the match runs.";
    $("json").textContent = "Machine-readable overlay events appear here (schema: /api/schema).";
    state.overlays = [];
  }

  // ---------------------------------------------------------------- rendering
  function handleEnvelope(env) {
    state.t = env.t;
    const ev = env.event;
    if (ev) {
      $("clock").textContent = ev.clock;
      if (typeof ev.x === "number") {
        const ball = $("ball"), line = $("passLine");
        const ex = typeof ev.end_x === "number" ? ev.end_x : ev.x;
        const ey = typeof ev.end_y === "number" ? ev.end_y : ev.y;
        line.setAttribute("x1", ev.x); line.setAttribute("y1", ev.y);
        line.setAttribute("x2", ex); line.setAttribute("y2", ey);
        ball.setAttribute("cx", ex); ball.setAttribute("cy", ey);
      }
    }
    for (const o of env.overlays) {
      state.overlays.push(o);
      if (state.overlays.length > 40) state.overlays.shift();
      render(o);
    }
    if (env.overlays.length) {
      $("json").textContent = JSON.stringify(state.overlays.slice(-8).reverse(), null, 2);
    }
  }

  function render(o) {
    switch (o.type) {
      case "scorebug": return renderScorebug(o);
      case "momentum": return renderMomentum(o);
      case "stat_panel": return renderStats(o);
      case "player_card": return renderPlayerCard(o);
      case "insight": renderLowerThird(o); return addTicker(o);
      case "commentary": if (o.display.slot === "lower_third") renderLowerThird(o); return addTicker(o);
      case "milestone": return addTicker(o);
      case "player_tag": renderTag(o); return addTicker(o);
      case "fan_alert": return renderFan(o);
      case "event_marker": return renderMarker(o);
      case "recap": return renderRecap(o);
      case "agent_trace": return renderTrace(o);
      default: return undefined;
    }
  }

  function renderScorebug(o) {
    const sb = $("scorebug"); clear(sb); sb.classList.remove("hidden");
    const { home, away, status } = o.data;
    const h = el("div", "team"); const hs = el("span", "swatch"); hs.style.background = home.color;
    h.append(hs, el("span", null, home.short));
    const sc = el("div", "score", `${home.score} - ${away.score}`);
    const a = el("div", "team"); const as = el("span", "swatch"); as.style.background = away.color;
    a.append(el("span", null, away.short), as);
    sb.append(h, sc, a);
    if (status !== "live") sb.append(el("div", "status", status === "half_time" ? "HT" : "FT"));
  }

  function renderMomentum(o) {
    const box = $("momentum"); clear(box); box.classList.remove("hidden");
    const v = o.data.momentum;
    const persp = teamShort(o.data.perspective_team);
    box.append(el("div", null, o.text || `Momentum (${persp} view): ${v > 0 ? "+" : ""}${v}`));
    const bar = el("div", "mom-bar"), fill = el("div", "mom-fill");
    const pct = Math.min(50, Math.abs(v) / 2);
    fill.style.left = v >= 0 ? "50%" : `${50 - pct}%`;
    fill.style.width = `${pct}%`;
    fill.style.background = v >= 0 ? (state.teamColor[o.data.perspective_team] || "#4cc9f0") : "#ef476f";
    bar.append(fill, el("div", "mom-mid"));
    box.append(bar);
    if (o.data.series) {
      const sp = el("div", "spark");
      for (const p of o.data.series) {
        const s = el("span", p.value < 0 ? "neg" : "");
        s.style.height = `${Math.max(2, Math.abs(p.value) / 100 * 26)}px`;
        s.title = `${p.minute}': ${p.value}`;
        sp.append(s);
      }
      box.append(sp);
    }
  }

  function renderStats(o) {
    const panel = $("statPanel"); clear(panel);
    const { teams, labels } = o.data;
    const ids = [state.meta.home.id, state.meta.away.id];
    const tbl = el("table"); const head = el("tr");
    head.append(el("th", null, `${o.data.minute}'`), el("th", null, teamShort(ids[0])), el("th", null, teamShort(ids[1])));
    tbl.append(head);
    for (const k of Object.keys(teams[ids[0]])) {
      const tr = el("tr");
      const fmt = (v) => (k.endsWith("_pct") ? `${v}%` : v);
      tr.append(el("td", null, labels[k] || k.replace(/_/g, " ")), el("td", null, fmt(teams[ids[0]][k])), el("td", null, fmt(teams[ids[1]][k])));
      tbl.append(tr);
    }
    panel.append(tbl);
  }

  function renderPlayerCard(o) {
    const card = $("playerCard"); clear(card); card.classList.remove("hidden");
    card.append(el("div", "h", `${o.headline} #${o.data.number} · ${o.data.position}`));
    const g = el("div", "grid");
    for (const [k, v] of Object.entries(o.data.stats)) { g.append(el("span", "k", k.replace(/_/g, " ")), el("span", null, v)); }
    card.append(g);
  }

  function chips(evidence) {
    const box = el("div", "evidence");
    for (const [k, v] of Object.entries(evidence || {})) {
      if (v === null || typeof v === "object") continue;
      box.append(el("span", "chip", `${k.replace(/_/g, " ")}: ${v}`));
    }
    return box;
  }

  function renderLowerThird(o) {
    const lt = $("lowerThird"); clear(lt); lt.classList.remove("hidden");
    lt.append(el("div", "kicker", `${o.clock.display} · ${(o.data.key || o.type).replace(/_/g, " ")}${o.data.favorite_club ? " · your club" : ""}${o.data.focus_player ? " · your player" : ""}`));
    lt.append(el("div", "head", o.headline || o.text));
    if (o.headline && o.text) lt.append(el("div", "why", o.text));
    if (Object.keys(o.evidence || {}).length) lt.append(chips(o.evidence));
    clearTimeout(state.lowerTimer);
    state.lowerTimer = setTimeout(() => lt.classList.add("hidden"), Math.max(2500, (o.display.duration_ms || 6000) / Math.max(1, Number($("speed").value) / 30)));
  }

  function addTicker(o) {
    const li = el("li", o.type);
    if (o.data.key === "goal") li.classList.add("goal");
    if (o.data.favorite_club || o.data.focus_player) li.classList.add("fav");
    const meta = el("div", "meta");
    meta.append(el("span", null, `${o.clock.display} · ${o.type.replace("_", " ")}`), el("span", null, `${o.source_agents.join(" → ")} · ${o.provider}`));
    li.append(meta);
    if (o.headline) li.append(el("div", "h", o.headline));
    if (o.text) li.append(el("div", null, o.text));
    if (state.mode === "analyst" && o.type === "insight") li.append(chips(o.evidence));
    const t = $("ticker"); t.prepend(li);
    while (t.children.length > 60) t.removeChild(t.lastChild);
  }

  function pct(x, max) { return `${(x / max) * 100}%`; }
  function renderTag(o) {
    if (!o.display.anchor) return;
    const tag = el("div", "tag", o.text);
    tag.style.left = pct(o.display.anchor.x + 3, 111);
    tag.style.top = pct(o.display.anchor.y + 3, 74);
    $("tags").append(tag);
    setTimeout(() => tag.remove(), 4000);
  }

  function renderMarker(o) {
    const g = $("markers");
    const a = o.display.anchor; if (!a) return;
    const color = state.teamColor[o.data.team_id] || "#fff";
    if (o.data.kind === "shot" && o.data.end) {
      const l = document.createElementNS(SVGNS, "line");
      l.setAttribute("x1", a.x); l.setAttribute("y1", a.y); l.setAttribute("x2", o.data.end.x); l.setAttribute("y2", o.data.end.y);
      l.setAttribute("stroke", o.data.outcome === "goal" ? "#3ddc97" : color); l.setAttribute("class", "marker-shot");
      g.append(l);
    }
    const c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("cx", a.x); c.setAttribute("cy", a.y);
    c.setAttribute("r", o.data.kind === "shot" ? String(0.8 + (o.data.xg || 0) * 3) : "0.7");
    c.setAttribute("fill", color); c.setAttribute("class", "marker-dot");
    g.append(c);
    while (g.children.length > 40) g.removeChild(g.firstChild);
  }

  function renderFan(o) {
    const f = $("fanAlert"); f.textContent = o.text; f.classList.remove("hidden");
    f.classList.toggle("sad", !o.data.scored);
    clearTimeout(state.fanTimer);
    state.fanTimer = setTimeout(() => f.classList.add("hidden"), 2600);
  }

  function renderRecap(o) {
    const r = $("recap"); clear(r); r.classList.remove("hidden");
    r.append(el("div", "kicker", `${o.data.title} · ${o.provider}`), el("h2", null, o.headline), el("p", null, o.text));
    const km = el("div", "evidence");
    for (const m of o.data.key_moments || []) km.append(el("span", "chip", `${m.minute}' ${m.kind.replace(/_/g, " ")}`));
    r.append(km);
  }

  function renderTrace(o) {
    if (o.data.agents) { renderAgentSummary(o.data); return; }
    const li = el("li", o.data.status, `${o.clock.display} ${o.headline}${o.text ? ` — ${o.text}` : ""}`);
    const t = $("trace"); t.prepend(li);
    while (t.children.length > 80) t.removeChild(t.lastChild);
  }

  function renderAgentSummary(s) {
    s = s || {};
    const box = $("agentSummary"); clear(box);
    const tbl = el("table"); const h = el("tr");
    ["agent", "calls", "ok", "retried", "degraded", "avg ms"].forEach((x) => h.append(el("th", null, x)));
    tbl.append(h);
    for (const [name, c] of Object.entries(s.agents || {})) {
      const tr = el("tr");
      [name, c.calls, c.ok, c.retried, c.degraded, c.avg_ms].forEach((x) => tr.append(el("td", null, x)));
      tbl.append(tr);
    }
    box.append(tbl);
    const extra = [`handoffs: ${s.handoffs ?? 0}`];
    if (s.provider) extra.push(`voice provider: ${s.provider}`);
    if (s.voice) extra.push(`voice fallbacks: ${s.voice.fallbacks}, retries: ${s.voice.retries}, circuit opens: ${s.voice.circuit_opens}`);
    if (s.circuit_open) extra.push("circuit OPEN: using template voice");
    box.append(el("p", null, extra.join(" · ")));
  }

  async function showCompare() {
    const p = params(0);
    const q = new URLSearchParams({ lang: p.get("lang"), limit: "4" });
    if (p.get("club")) q.set("club", p.get("club"));
    if (p.get("player")) q.set("player", p.get("player"));
    const box = $("compare"); clear(box); box.classList.remove("hidden");
    box.append(el("h2", null, "Same match, same intelligence, two audiences"));
    box.append(el("p", "sub", "Loading…"));
    const res = await fetch(`/api/match/${encodeURIComponent(p.get("seed"))}/compare?${q}`);
    if (!res.ok) { box.lastChild.textContent = "Could not load comparison."; return; }
    const d = await res.json();
    box.removeChild(box.lastChild);
    const cols = el("div", "cols");
    for (const [name, label] of [["analyst", "Analyst mode"], ["casual", "Casual fan mode"]]) {
      const col = el("div", "col");
      col.append(el("h3", null, `${label} · ${d[name].total_overlays} overlays`));
      for (const o of d[name].top) {
        const it = el("div", "item");
        it.append(el("div", "h", `${o.clock.display} ${o.headline || ""}`), el("div", null, o.text));
        if (Object.keys(o.evidence || {}).length) it.append(chips(o.evidence));
        col.append(it);
      }
      cols.append(col);
    }
    box.append(cols);
  }

  loadMeta();
})();
