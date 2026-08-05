"use strict";

const MACROS = [
  { key: "calories", label: "Calories", unit: "kcal" },
  { key: "protein", label: "Protein", unit: "g" },
  { key: "carb", label: "Carb", unit: "g" },
  { key: "fat", label: "Fat", unit: "g" },
];
const ICONS = { climb: "🧗", run: "🏃", lift: "🏋️", dance: "💃", walk: "🚶", mixed: "🔀", rest: "🌙" };
const STATUS_EMOJI = { green: "🟢", yellow: "🟡", red: "🔴", none: "⚪" };

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

// per-tab state
let currentDay = null;  // shared day for the Nutrition + Training panes
let weekStart = null;   // Eating-week pane

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data && data.detail;
    const msg = (d && (d.error || d)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}
function jsonPost(body, method = "POST") {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function shift(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d + days);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}
function fmtDate(iso, opts) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, opts);
}
// Ring fill class driven by the SERVER's macro_status (5%/15% bands) so the ring
// and the summary-row emoji never disagree. green→on-track; off-target keeps its
// direction (under/over) for color, but the on-track threshold is the server's.
function ringClass(serverStatus, consumed, target) {
  if (serverStatus === "green") return "ontrack";
  if (serverStatus === "none" || !target) return "under";
  return consumed > target ? "over" : "under";
}
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

// Persistent header indicator: lit whenever the last estimate ran offline, so a
// degraded (lookup-table) session is impossible to miss across tabs.
function updateEngineFlag(engine) {
  const flag = $("#engineFlag");
  if (!flag) return;
  const offline = engine && engine.mode === "offline";
  flag.hidden = !offline;
  if (offline) {
    const label = { no_key: "no API key", auth_failed: "key rejected", rate_limited: "rate limited", unreachable: "offline", forced: "offline mode", bad_output: "engine error", error: "engine error" }[engine.reason] || "offline";
    flag.textContent = "⚠ lookup-table mode · " + label;
    if (engine.message) flag.title = engine.message;
  }
}
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ===================== PANE CONTROLLER =====================
// Two panes; each picks one of five views from a dropdown. Each view keeps its
// existing DOM block (with its own IDs), so the controller just MOVES the block
// into the chosen pane and runs its loader — every render function is reused
// unchanged. Duplicates are prevented by swapping the two panes.

const VIEWS = {
  nutrition:  { label: "Nutrition",   group: "Nutrition", load: loadNutrition },
  eatingweek: { label: "Eating week", group: "Nutrition", load: () => loadWeekly() },
  training:   { label: "Training",    group: "Training",  load: loadTrainingToday },
  program:    { label: "Program",     group: "Training",  load: loadMacro },
  habits:     { label: "Habits",      group: "",          load: loadHabits },
};
const paneState = { a: "nutrition", b: "training" };

function paneEl(key) { return document.querySelector(`.pane[data-pane="${key}"]`); }

function populatePicker(select) {
  const groups = {};
  for (const [id, v] of Object.entries(VIEWS)) (groups[v.group] ??= []).push([id, v.label]);
  select.innerHTML = Object.entries(groups).map(([g, items]) => {
    const opts = items.map(([id, label]) => `<option value="${id}">${label}</option>`).join("");
    return g ? `<optgroup label="${g}">${opts}</optgroup>` : opts;
  }).join("");
}

function placeView(key, viewId) {
  const pane = paneEl(key);
  const body = pane.querySelector(".pane-body");
  const store = document.getElementById("viewStore");
  // Evict whatever this pane currently holds back to the store, so views replace
  // rather than stack. (Swaps re-place the evicted block into the other pane.)
  Array.from(body.children).forEach((c) => store.appendChild(c));
  paneState[key] = viewId;
  body.appendChild(document.getElementById("view-" + viewId));
  pane.querySelector(".pane-picker").value = viewId;
  VIEWS[viewId].load();
}

function pickView(key, viewId) {
  const other = key === "a" ? "b" : "a";
  if (paneState[other] === viewId) {          // already shown elsewhere → swap panes
    const prev = paneState[key];
    placeView(other, prev);
  }
  placeView(key, viewId);
}

// Mount a view into a pane without a user click (e.g. "View macrocycle →").
function showView(viewId) {
  if (paneState.a === viewId || paneState.b === viewId) { VIEWS[viewId].load(); return; }
  pickView(paneState.a === "training" ? "b" : "a", viewId);
}

function initPanes() {
  document.querySelectorAll(".pane-picker").forEach((sel) => {
    populatePicker(sel);
    const key = sel.closest(".pane").dataset.pane;
    sel.addEventListener("change", () => pickView(key, sel.value));
  });
  placeView("a", "nutrition");
  placeView("b", "training");
}

// ===================== CHAT =====================

const chatComposer = $("#chatComposer");
chatComposer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#chatInput");
  const text = input.value.trim();
  if (!text) return;
  addBubble("user", escapeHtml(text));
  input.value = "";
  const btn = $("#chatSend");
  btn.disabled = true;
  const thinking = addBubble("assistant", `<span class="typing">estimating…</span>`);
  try {
    const est = await api("/api/log", jsonPost({ text }));
    thinking.remove();
    addEstimateCard(est, text);
  } catch (err) {
    thinking.innerHTML = `<span class="err">⚠ ${escapeHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    scrollThread();
  }
});

function addBubble(who, html) {
  const el = document.createElement("div");
  el.className = "bubble " + who;
  el.innerHTML = html;
  $("#chatThread").appendChild(el);
  scrollThread();
  return el;
}
function scrollThread() {
  // The thread lives inside a scrollable pane body — scroll that, not the window.
  const scroller = $("#chatThread").closest(".pane-scroll");
  if (scroller) scroller.scrollTop = scroller.scrollHeight;
}

function addEstimateCard(est, text) {
  const conf = (est.confidence || "medium").toLowerCase();
  const t = est.total || {};
  const rows = (est.items || []).map((it) =>
    `<tr><td>${escapeHtml(it.name)}</td><td class="n">${Math.round(it.calories || 0)} cal · ${Math.round(it.protein || 0)}p ${Math.round(it.carb || 0)}c ${Math.round(it.fat || 0)}f</td></tr>`).join("");
  const assumptions = (est.assumptions || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
  const swings = (est.swing_factors || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  // Log to the day Daily is showing, not always today — ConfirmItem.day supports it.
  const targetDay = currentDay || todayISO();
  const dayLabel = targetDay === todayISO() ? "today" : fmtDate(targetDay, { month: "short", day: "numeric" });
  const engine = est.engine || (est.offline ? { mode: "offline", reason: "error", message: "Offline estimate." } : { mode: "llm" });
  updateEngineFlag(engine);
  const banner = engine.mode === "offline"
    ? `<div class="engine-banner">⚠ ${escapeHtml(engine.message || "Lookup-table estimate — the reasoning engine didn't run.")}</div>`
    : "";
  const el = document.createElement("div");
  el.className = "bubble assistant";
  el.innerHTML = `
    <div class="card${engine.mode === "offline" ? " degraded" : ""}">
      ${banner}
      <div class="conf"><span class="pill ${conf}">${conf}</span> confidence ·
        <b class="approx">≈${Math.round(t.calories || 0)} cal</b> ${est.uncertainty_cal ? "±" + Math.round(est.uncertainty_cal) : ""}</div>
      ${est.clarify ? `<div class="clarify"><b>One question:</b> ${escapeHtml(est.clarify)}</div>` : ""}
      <table class="comp">${rows}
        <tr class="tot"><td>Total</td><td class="n">
          <input class="editable" data-k="calories" type="number" value="${Math.round(t.calories || 0)}"> cal ·
          <input class="editable" data-k="protein" type="number" value="${Math.round(t.protein || 0)}">p
          <input class="editable" data-k="carb" type="number" value="${Math.round(t.carb || 0)}">c
          <input class="editable" data-k="fat" type="number" value="${Math.round(t.fat || 0)}">f
        </td></tr></table>
      ${assumptions ? `<div class="meta"><b>Assuming:</b><ul>${assumptions}</ul></div>` : ""}
      ${swings ? `<div class="meta"><b>Biggest swing:</b><ul>${swings}</ul></div>` : ""}
      <div class="card-actions">
        <button class="secondary act-discard">Discard</button>
        <button class="act-confirm">Confirm &amp; log to ${dayLabel}</button>
      </div>
    </div>`;
  $("#chatThread").appendChild(el);
  el.querySelector(".act-discard").onclick = () => el.remove();
  el.querySelector(".act-confirm").onclick = async () => {
    const get = (k) => Number(el.querySelector(`[data-k="${k}"]`).value) || 0;
    const btn = el.querySelector(".act-confirm");
    btn.disabled = true;
    await api("/api/confirm", jsonPost({
      name: text.slice(0, 120), calories: get("calories"), protein: get("protein"),
      carb: get("carb"), fat: get("fat"), confidence: est.confidence || "medium",
      uncertainty_cal: est.uncertainty_cal || 0, assumptions: est.assumptions || [],
      source: "described", day: targetDay,
    }));
    el.querySelector(".card-actions").innerHTML = `<span class="logged">✓ Logged to ${dayLabel}</span>`;
    recentFoodNames.unshift(text.slice(0, 120));  // keep autocomplete fresh within the session
    loadNutrition();  // live-update the rings/log in this same pane
    scrollThread();
  };
  scrollThread();
}

// ===================== DAILY =====================

async function loadNutrition() {
  if (!currentDay) currentDay = todayISO();
  const v = await api(`/api/day?day=${currentDay}`);
  $("#dTitle").textContent = currentDay === todayISO() ? "Today" : fmtDate(currentDay, { weekday: "short", month: "short", day: "numeric" });
  renderSummary(v, $("#dSummary"));
  renderRings(v, $("#dRings"), $("#dDayline"));
  renderLog(v.items, $("#dLog"));
}

// The two day-scoped panes (Nutrition + Training) share `currentDay`; refresh
// whichever of them is mounted after a day change or an entry.
function refreshDay() {
  if (paneState.a === "nutrition" || paneState.b === "nutrition") loadNutrition();
  if (paneState.a === "training" || paneState.b === "training") loadTrainingToday();
}
function stepDay(delta) { currentDay = shift(currentDay || todayISO(), delta); refreshDay(); }

$("#dPrev").onclick = () => stepDay(-1);
$("#dNext").onclick = () => stepDay(1);
$("#dSleep").onclick = async () => { if (await logSleepPrompt(currentDay)) refreshDay(); };
$("#dWeigh").onclick = async () => { if (await weighInPrompt(currentDay)) refreshDay(); };
$("#dTarget").onclick = async () => { if (await editTargetPrompt(currentDay)) refreshDay(); };

function renderSummary(v, host) {
  const target = v.target || {}, total = v.total || {}, st = v.status || {}, rec = v.recovery || {};
  const trainingLine = v.training && v.training.length
    ? v.training.map((s) => `${ICONS[s.type] || ""} ${cap(s.type)}${s.intensity ? " · " + s.intensity : ""}${s.duration_min ? " · " + s.duration_min + "min" : ""}${s.est_burn ? " · ~" + Math.round(s.est_burn) + " kcal" : ""}${s.subjective_difficulty ? " · " + s.subjective_difficulty + "/10" : ""}`).join("<br>")
    : `<span class="muted">No training — rest day.</span>`;
  const macroStatus = MACROS.filter((m) => st[m.key] && st[m.key] !== "none").map((m) => `${STATUS_EMOJI[st[m.key]]} ${m.label.toLowerCase()}`).join("  ");
  const sleep = v.sleep;
  const sleepLine = sleep ? (sleep.duration_h ? `${sleep.duration_h}h` : "") + (sleep.deep_min ? `, deep ${sleep.deep_min} min` : "") + (sleep.manual_rating ? ` (rated ${sleep.manual_rating}/10)` : "") : `<span class="muted">not logged</span>`;
  const recLine = rec.score != null ? `<b>${Math.round(rec.score)}/100</b> (${rec.verdict}) — ${escapeHtml(rec.note)}` : `<span class="muted">${escapeHtml(rec.note || "no data")}</span>`;
  host.innerHTML = `
    <div class="srow"><span class="k">Training</span><span class="val">${trainingLine}</span></div>
    <div class="srow"><span class="k">Target</span><span class="val">${target.calories ? `${Math.round(target.calories)} cal · ${Math.round(target.protein)}p ${Math.round(target.carb)}c ${Math.round(target.fat)}f <span class="scope">${escapeHtml(target.scope || "")}</span>` : "—"}</span></div>
    <div class="srow"><span class="k">Logged</span><span class="val">${Math.round(total.calories || 0)} cal · ${Math.round(total.protein || 0)}p ${Math.round(total.carb || 0)}c ${Math.round(total.fat || 0)}f</span></div>
    ${macroStatus ? `<div class="srow"><span class="k">Status</span><span class="val">${macroStatus}</span></div>` : ""}
    <div class="srow"><span class="k">Weight</span><span class="val">${v.weight != null ? v.weight + " lb" : '<span class="muted">not logged</span>'}</span></div>
    <div class="srow"><span class="k">Sleep</span><span class="val">${sleepLine}</span></div>
    <div class="srow"><span class="k">Recovery</span><span class="val">${recLine}</span></div>`;
}

function renderRings(view, host, dayline) {
  host.innerHTML = "";
  const tpl = $("#ringTpl").content;
  for (const m of MACROS) {
    const node = tpl.cloneNode(true);
    const consumed = view.total[m.key] || 0;
    const target = (view.target && view.target[m.key]) || 0;
    const remaining = view.remaining[m.key];
    node.querySelector(".ring-remaining .num").textContent = Math.round(remaining);
    node.querySelector(".ring-remaining .unit").textContent = m.unit + (remaining >= 0 ? " left" : " over");
    node.querySelector(".ring-label").textContent = m.label;
    const fill = node.querySelector(".fill");
    fill.style.width = (target ? Math.min(100, (consumed / target) * 100) : 0) + "%";
    fill.classList.add(ringClass(view.status && view.status[m.key], consumed, target));
    node.querySelector(".ring-sub").textContent = `${Math.round(consumed)} / ${Math.round(target)} ${m.unit}`;
    host.appendChild(node);
  }
  const cal = Math.round(view.total.calories || 0), pm = view.uncertainty_cal || 0;
  const cc = view.confidence_calories || {}, est = Math.round((cc.medium || 0) + (cc.low || 0));
  const mix = cal > 0 ? ` · ${Math.round((est / cal) * 100)}% estimated` : "";
  dayline.innerHTML = cal ? `<b class="approx">≈${cal} cal</b> ${pm ? "±" + pm : ""} eaten${mix}` : "Nothing logged this day.";
}

function renderLog(items, host) {
  host.innerHTML = "";
  if (!items.length) { host.innerHTML = `<div class="empty">No food logged — describe a meal below to log it.</div>`; return; }
  for (const it of items) {
    const conf = (it.confidence || "medium").toLowerCase();
    const el = document.createElement("div");
    el.className = "entry";
    const assum = (it.assumptions && it.assumptions.length) ? `<span class="assum">${escapeHtml(it.assumptions[0])}</span>` : "";
    el.innerHTML = `<span class="dot ${conf}" title="${conf} confidence"></span>
      <div class="name"><b>${escapeHtml(it.name)}</b>${assum}</div>
      <div class="macros"><span class="cal">${Math.round(it.calories || 0)}${it.uncertainty_cal ? " ±" + Math.round(it.uncertainty_cal) : ""}</span> · ${Math.round(it.protein || 0)}p ${Math.round(it.carb || 0)}c ${Math.round(it.fat || 0)}f</div>
      <button class="entry-del" title="Remove this item" aria-label="Remove ${escapeHtml(it.name)}">✕</button>`;
    el.querySelector(".entry-del").onclick = async () => {
      const ok = await confirmSheet({ title: "Remove item?", message: `Delete “${it.name}” from this day?`, confirmLabel: "Remove", danger: true });
      if (!ok) return;
      await api(`/api/foods/${it.id}?day=${currentDay || todayISO()}`, { method: "DELETE" });
      refreshDay();
    };
    host.appendChild(el);
  }
}

// ===================== WEEKLY (eating) =====================

async function loadWeekly(start) {
  const wv = await api("/api/week" + (start ? `?start=${start}` : (weekStart ? `?start=${weekStart}` : "")));
  weekStart = wv.start;
  $("#wLabel").textContent = "Week of " + fmtDate(wv.start, { month: "short", day: "numeric" });
  const cal = $("#wCalendar");
  cal.innerHTML = "";
  const today = todayISO();
  for (const d of wv.days) {
    const cell = document.createElement("button");
    cell.className = "daycell" + (d.day === today ? " today" : "");
    cell.innerHTML = `
      <div class="dc-day">${d.weekday}</div>
      <div class="dc-icon">${ICONS[d.activity.type] || ""}</div>
      <div class="dc-act">${escapeHtml(d.activity.type === "rest" ? "Rest" : d.activity.label)}</div>
      <div class="dc-status">${STATUS_EMOJI[d.status] || "⚪"}</div>
      <div class="dc-wt">${d.weight != null ? d.weight : "—"}</div>
      <div class="dc-tgt">${d.target_calories ? Math.round(d.target_calories) : ""}</div>`;
    cell.onclick = () => { currentDay = d.day; showView("nutrition"); refreshDay(); };
    cal.appendChild(cell);
  }
  drawSparkline($("#sparkline"), wv.trend.series || []);
  $("#wContext").textContent = wv.weight_context || "";
  const def = wv.avg_daily_deficit;
  $("#wDeficit").textContent = def == null ? "" : def > 0 ? `Avg daily deficit: ${Math.round(def)} cal` : `Avg daily surplus: ${Math.round(-def)} cal`;
}
$("#wPrev").onclick = () => loadWeekly(shift(weekStart || todayISO(), -7));
$("#wNext").onclick = () => loadWeekly(shift(weekStart || todayISO(), 7));

function drawSparkline(svg, series) {
  svg.innerHTML = "";
  if (series.length < 2) return;
  const W = 300, H = 60, pad = 6;
  const min = Math.min(...series), max = Math.max(...series), span = max - min || 1;
  const x = (i) => pad + (i * (W - 2 * pad)) / (series.length - 1);
  const y = (v) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  svg.innerHTML = `<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` +
    series.map((v, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.2" fill="var(--accent)"/>`).join("");
}

// ===================== TRAINING (today overview) =====================
// Compact "what's on for this day": the planned session from the active
// program's current week + anything already logged for `currentDay`. The coach
// chat lives beneath it (initCoach / sendCoach), reused unchanged.

async function loadTrainingToday() {
  if (!currentDay) currentDay = todayISO();
  const host = $("#trainToday");
  $("#tTodayTitle").textContent = currentDay === todayISO()
    ? "Today" : fmtDate(currentDay, { weekday: "short", month: "short", day: "numeric" });

  const [day, progs] = await Promise.all([api(`/api/day?day=${currentDay}`), api("/api/programs")]);
  const logged = day.training || [];

  let planHtml;
  if (!progs.length) {
    planHtml = `<div class="tt-row muted">No program yet — ask the coach to build one below.</div>`;
  } else {
    const grid = await api(`/api/programs/${progs[0].id}`);
    const cur = grid.weeks.find((w) => w.is_current);
    const cell = cur && cur.days.find((d) => d.date === currentDay);
    const head = cur
      ? `<div class="tt-prog">${escapeHtml(grid.program.name)} — week ${cur.week}${cur.deload ? " <span class=\"scope\">deload</span>" : ""}</div>`
      : `<div class="tt-prog muted">${escapeHtml(grid.program.name)} — outside the program's dates</div>`;
    const plan = cell && cell.session
      ? `<div class="tt-row">${ICONS[cell.session.type] || ""} Planned: <b>${escapeHtml(cell.session.role || cell.session.type)}</b>${cell.session.intensity ? " · " + cell.session.intensity : ""}${cell.session.planned_km != null ? " · " + cell.session.planned_km + "km" : ""}</div>`
      : `<div class="tt-row muted">No planned session this day.</div>`;
    planHtml = head + plan;
  }

  const target = day.target || {};
  const targetLine = target.calories
    ? `<div class="tt-row"><span class="tt-k">Fuel</span> ${Math.round(target.calories)} cal · ${Math.round(target.protein)}p ${Math.round(target.carb)}c ${Math.round(target.fat)}f <span class="scope">${escapeHtml(target.scope || "")}</span></div>`
    : "";
  const loggedHtml = logged.length
    ? logged.map((s) => `<div class="tt-row">${ICONS[s.type] || ""} <b>${cap(s.type)}</b>${s.intensity ? " · " + s.intensity : ""}${s.duration_min ? " · " + s.duration_min + "min" : ""}${s.est_burn ? ` · <span class="muted">~${Math.round(s.est_burn)} kcal</span>` : ""}</div>`).join("")
    : `<div class="tt-row muted">Nothing logged ${currentDay === todayISO() ? "today" : "this day"} yet.</div>`;

  host.innerHTML = `<div class="card tt-card">
    <div class="tt-h">Plan</div>${planHtml}${targetLine}
    <hr class="tt-sep" />
    <div class="tt-h">Logged</div>${loggedHtml}
  </div>`;
  initCoach();
}
$("#tAddSession").onclick = async () => { if (await addTrainingPrompt(currentDay)) refreshDay(); };

// ===================== HABITS =====================

async function loadHabits() {
  const [grid, series] = await Promise.all([api("/api/habit_grid?days=14"), api("/api/series?days=14")]);
  renderHabitGrid(grid);
  // map date -> {sleep, recovery} for the aligned journal chart
  const byDate = {};
  series.dates.forEach((d, i) => (byDate[d] = { sleep: series.sleep[i], recovery: series.recovery[i] }));
  // Double rAF so the freshly-mounted block is laid out before we measure row geometry.
  requestAnimationFrame(() => requestAnimationFrame(() => drawJournalChart(grid.dates, byDate)));
}

function renderHabitGrid(grid) {
  const table = $("#habitGrid");
  const habits = grid.habits;
  const head = `<thead><tr><th class="corner">Date</th>${habits.map((h) =>
    `<th class="hcol"><span class="hname" data-id="${h.id}" title="Click to rename">${escapeHtml(h.name)}</span><button class="hx" data-del="${h.id}" title="Remove">✕</button></th>`).join("")}</tr></thead>`;
  const rows = grid.dates.map((day) => {
    const isToday = day === todayISO();
    const cells = habits.map((h) => {
      const done = grid.cells[day] && grid.cells[day][String(h.id)];
      return `<td class="hcell"><button class="mark ${done ? "on" : ""}" data-h="${h.id}" data-d="${day}" aria-label="toggle">${done ? "✗" : ""}</button></td>`;
    }).join("");
    return `<tr class="${isToday ? "today" : ""}" data-day="${day}"><td class="datecol">${fmtDate(day, { weekday: "short" })} <span class="muted">${fmtDate(day, { month: "short", day: "numeric" })}</span></td>${cells}</tr>`;
  }).join("");
  table.innerHTML = head + `<tbody>${rows}</tbody>`;

  table.querySelectorAll(".mark").forEach((b) => b.onclick = async () => {
    const on = !b.classList.contains("on");
    b.classList.toggle("on", on); b.textContent = on ? "✗" : "";
    await api(`/api/habits/${b.dataset.h}/toggle`, jsonPost({ day: b.dataset.d, done: on }));
  });
  table.querySelectorAll(".hname").forEach((s) => s.onclick = async () => {
    const vals = await openSheet({
      title: "Rename habit", submitLabel: "Rename",
      fields: [{ name: "name", label: "Name", type: "text", value: s.textContent }],
    });
    const name = vals && vals.name.trim();
    if (name) { await api(`/api/habits/${s.dataset.id}/rename`, jsonPost({ name })); loadHabits(); }
  });
  table.querySelectorAll(".hx").forEach((b) => b.onclick = async () => {
    if (await confirmSheet({ title: "Remove habit?", message: "This deletes the habit and its history.", confirmLabel: "Remove", danger: true })) {
      await api(`/api/habits/${b.dataset.del}`, { method: "DELETE" }); loadHabits();
    }
  });
}
$("#addHabit").onclick = async () => {
  const vals = await openSheet({
    title: "New habit", submitLabel: "Add",
    fields: [{ name: "name", label: "Habit", type: "text", placeholder: "Meditate, 3L water, No alcohol" }],
  });
  const name = vals && vals.name.trim();
  if (name) { await api("/api/habits", jsonPost({ name })); loadHabits(); }
};

// Vertical dual-line chart, aligned row-for-row with the grid (like the journal).
// dates: newest-first (grid order). Sleep (blue) + Recovery (red) run top->bottom.
function drawJournalChart(dates, byDate) {
  const svg = $("#journalChart");
  const table = $("#habitGrid");
  const tbody = table.tBodies[0];
  const thead = table.tHead;
  if (!tbody || !tbody.rows.length) return;
  const tbodyTop = tbody.getBoundingClientRect().top;
  const H = tbody.getBoundingClientRect().height;
  const W = 150, padX = 22;
  const SLEEP_MIN = 3, SLEEP_MAX = 10;      // hours band
  const REC_MIN = 0, REC_MAX = 100;         // recovery band
  const xS = (h) => padX + ((Math.max(SLEEP_MIN, Math.min(SLEEP_MAX, h)) - SLEEP_MIN) / (SLEEP_MAX - SLEEP_MIN)) * (W - 2 * padX);
  const xR = (r) => padX + ((Math.max(REC_MIN, Math.min(REC_MAX, r)) - REC_MIN) / (REC_MAX - REC_MIN)) * (W - 2 * padX);

  const yByRow = Array.from(tbody.rows).map((r) => {
    const rc = r.getBoundingClientRect();
    return rc.top - tbodyTop + rc.height / 2;
  });

  let s = "";
  // faint gridlines per row
  yByRow.forEach((y) => (s += `<line x1="0" y1="${y.toFixed(1)}" x2="${W}" y2="${y.toFixed(1)}" stroke="var(--line)" stroke-width="0.5" opacity="0.5"/>`));

  const build = (getVal, xmap, color) => {
    let seg = [], out = "";
    const flush = () => {
      if (seg.length >= 2) out += `<polyline points="${seg.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>`;
      seg = [];
    };
    dates.forEach((d, i) => {
      const v = byDate[d] && getVal(byDate[d]);
      if (v == null) { flush(); return; }
      const x = xmap(v), y = yByRow[i];
      seg.push([x.toFixed(1), y.toFixed(1)]);
      out += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.3" fill="${color}"/>`;
    });
    flush();
    return out;
  };

  const sleepColor = "var(--under)", recColor = "#d0655a";
  s += build((o) => o.sleep, xS, sleepColor);
  s += build((o) => o.recovery, xR, recColor);

  svg.setAttribute("viewBox", `0 0 ${W} ${Math.max(1, H)}`);
  svg.setAttribute("width", W);
  svg.style.height = H + "px";
  svg.innerHTML = s;

  // header: axis hints, aligned with the grid's header height
  const headH = thead ? thead.getBoundingClientRect().height : 40;
  const head = $("#chartHead");
  head.style.height = headH + "px";
  head.innerHTML = `<span class="axmin">3h·0</span><span class="axmax">10h·100</span>`;
}

// ===================== INLINE ENTRY SHEETS =====================
// One reusable inline form replaces the old chain of blocking prompt()/alert()
// dialogs. Resolves to a {name: value} map on submit, or null on cancel/Escape.

function openSheet({ title, message = "", fields = [], submitLabel = "Save", cancelLabel = "Cancel", onRender }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "sheet-overlay";
    const rowsHtml = fields.map((f) => {
      const id = "sf_" + f.name;
      let control;
      if (f.type === "select") {
        control = `<select id="${id}" data-name="${f.name}">${f.options.map((o) => {
          const val = typeof o === "string" ? o : o.value;
          const lab = typeof o === "string" ? o : o.label;
          return `<option value="${escapeHtml(String(val))}" ${String(f.value) === String(val) ? "selected" : ""}>${escapeHtml(String(lab))}</option>`;
        }).join("")}</select>`;
      } else {
        const type = f.type === "number" ? "number" : "text";
        control = `<input id="${id}" data-name="${f.name}" type="${type}" ${f.step ? `step="${f.step}"` : ""} value="${f.value != null ? escapeHtml(String(f.value)) : ""}" placeholder="${escapeHtml(f.placeholder || "")}" autocomplete="off" />`;
      }
      return `<label class="sheet-row" data-row="${f.name}"><span class="sheet-label">${escapeHtml(f.label)}${f.optional ? ' <span class="muted">optional</span>' : ""}</span>${control}</label>`;
    }).join("");
    overlay.innerHTML = `<form class="sheet card">
      <div class="sheet-title">${escapeHtml(title)}</div>
      ${message ? `<p class="sheet-msg">${escapeHtml(message)}</p>` : ""}
      ${rowsHtml}
      <div class="card-actions">
        ${cancelLabel ? `<button type="button" class="secondary sheet-cancel">${escapeHtml(cancelLabel)}</button>` : ""}
        <button type="submit" class="sheet-ok">${escapeHtml(submitLabel)}</button>
      </div></form>`;
    document.body.appendChild(overlay);
    const form = overlay.querySelector("form");
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    const close = (val) => { overlay.remove(); document.removeEventListener("keydown", onKey); resolve(val); };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(null); });
    const cancelBtn = overlay.querySelector(".sheet-cancel");
    if (cancelBtn) cancelBtn.onclick = () => close(null);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const values = {};
      form.querySelectorAll("[data-name]").forEach((el) => { values[el.dataset.name] = el.value; });
      close(values);
    });
    const first = form.querySelector("[data-name]"); if (first) first.focus();
    if (onRender) onRender(form);
  });
}

// A yes/no confirmation as an inline sheet (replaces confirm()).
async function confirmSheet({ title, message, confirmLabel = "OK", danger = false }) {
  const vals = await openSheet({ title, message, submitLabel: confirmLabel });
  if (vals && danger) return true;  // (danger just documents intent; submit === confirmed)
  return vals != null;
}

const numOrNull = (v) => (v === "" || v == null ? null : Number(v));
const sheetDay = (day) => fmtDate(day, { weekday: "short", month: "short", day: "numeric" });

async function addTrainingPrompt(day) {
  const vals = await openSheet({
    title: `Add training · ${sheetDay(day)}`,
    submitLabel: "Log session",
    fields: [
      { name: "type", label: "Type", type: "select", value: "climb", options: ["climb", "run", "lift", "dance", "walk", "mixed", "rest"] },
      { name: "intensity", label: "Intensity", type: "select", value: "moderate", options: ["easy", "moderate", "hard", "max"] },
      { name: "duration_min", label: "Duration (min)", type: "number", value: 60 },
      { name: "subjective_difficulty", label: "Difficulty 1–10", type: "number", optional: true },
      { name: "est_burn", label: "Calorie burn", type: "number", optional: true, placeholder: "blank = estimate from MET" },
    ],
    onRender: (form) => {
      const typeSel = form.querySelector('[data-name="type"]');
      const toggle = () => {
        const rest = typeSel.value === "rest";
        ["intensity", "duration_min", "subjective_difficulty", "est_burn"].forEach((n) => {
          const row = form.querySelector(`[data-row="${n}"]`);
          if (row) row.style.display = rest ? "none" : "";
        });
      };
      typeSel.addEventListener("change", toggle); toggle();
    },
  });
  if (!vals) return false;
  const rest = vals.type === "rest";
  await api("/api/training", jsonPost({
    day, type: vals.type,
    intensity: rest ? "easy" : vals.intensity,
    duration_min: rest ? 0 : (Number(vals.duration_min) || 0),
    subjective_difficulty: rest ? null : numOrNull(vals.subjective_difficulty),
    est_burn: rest ? null : numOrNull(vals.est_burn),
  }));
  return true;
}
async function logSleepPrompt(day) {
  const vals = await openSheet({
    title: `Log sleep · ${sheetDay(day)}`,
    submitLabel: "Save sleep",
    fields: [
      { name: "duration_h", label: "Hours slept", type: "number", step: "0.1", optional: true },
      { name: "manual_rating", label: "Quality 1–10", type: "number", optional: true },
      { name: "deep_min", label: "Deep sleep (min)", type: "number", optional: true },
    ],
  });
  if (!vals) return false;
  if (!vals.duration_h && !vals.manual_rating && !vals.deep_min) return false;
  await api("/api/sleep", jsonPost({ day, duration_h: numOrNull(vals.duration_h), manual_rating: numOrNull(vals.manual_rating), deep_min: numOrNull(vals.deep_min) }));
  return true;
}
async function weighInPrompt(day) {
  const vals = await openSheet({
    title: `Weigh-in · ${sheetDay(day)}`,
    submitLabel: "Save weight",
    fields: [{ name: "weight", label: "Weight (lb)", type: "number", step: "0.1" }],
  });
  if (!vals || !vals.weight) return false;
  await api("/api/weigh_in", jsonPost({ day, weight: Number(vals.weight), unit: "lb" }));
  return true;
}
async function editTargetPrompt(day) {
  const view = await api(`/api/day?day=${day}`);
  const cur = view.target || {}, scope = cur.scope || "default";
  const vals = await openSheet({
    title: `Target for ‘${scope}’ days`,
    submitLabel: "Save target",
    fields: [
      { name: "calories", label: "Calories", type: "number", value: Math.round(cur.calories || 0) },
      { name: "protein", label: "Protein (g)", type: "number", value: Math.round(cur.protein || 0) },
      { name: "carb", label: "Carb (g)", type: "number", value: Math.round(cur.carb || 0) },
      { name: "fat", label: "Fat (g)", type: "number", value: Math.round(cur.fat || 0) },
    ],
  });
  if (!vals) return false;
  await api("/api/target", jsonPost({ calories: Number(vals.calories), protein: Number(vals.protein), carb: Number(vals.carb), fat: Number(vals.fat), scope }));
  return true;
}

// ===================== TRAINING COACH =====================

function mdLite(s) {
  return escapeHtml(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
}

let coachInited = false;
function initCoach() {
  if (coachInited) return;
  coachInited = true;
  const chips = ["10-week running program, 30km, +10% a week, week-4 deload", "8-week climbing block", "What's this week?"];
  $("#coachChips").innerHTML = chips.map((c) => `<button class="chip">${escapeHtml(c)}</button>`).join("");
  $$("#coachChips .chip").forEach((b) => (b.onclick = () => sendCoach(b.textContent)));
  attachAutocomplete($("#coachInput"), $("#coachGhost"), () => COACH_SUGGESTIONS);
  if (!$("#coachThread").children.length) {
    addCoachBubble("assistant", "I'm your training coach. Tell me a block to build — sport, weeks, base volume, weekly increase, and any deload. Or tap a suggestion below.");
  }
}

$("#coachComposer").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#coachInput").value.trim();
  if (v) sendCoach(v);
});

async function sendCoach(text) {
  $("#coachInput").value = "";
  $("#coachGhost").innerHTML = "";
  addCoachBubble("user", escapeHtml(text));
  const thinking = addCoachBubble("assistant", `<span class="typing">thinking…</span>`);
  try {
    const data = await api("/api/coach", jsonPost({ text }));
    updateEngineFlag(data.engine);
    const banner = data.engine && data.engine.mode === "offline"
      ? `<div class="engine-banner">⚠ ${escapeHtml(data.engine.message || "Offline coach — pattern-matched, not reasoned.")}</div>`
      : "";
    thinking.innerHTML = banner + mdLite(data.reply);
    if (data.program_id) {
      const btn = document.createElement("button");
      btn.className = "secondary viewmacro";
      btn.textContent = "View macrocycle →";
      btn.onclick = () => { currentProgramId = data.program_id; showView("program"); };
      thinking.appendChild(document.createElement("br"));
      thinking.appendChild(btn);
    }
  } catch (err) {
    thinking.innerHTML = `<span class="err">⚠ ${escapeHtml(err.message)}</span>`;
  }
}

function addCoachBubble(who, html) {
  const el = document.createElement("div");
  el.className = "bubble " + who;
  el.innerHTML = html;
  $("#coachThread").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return el;
}

// ===================== MACROCYCLE =====================

let currentProgramId = null;

async function loadMacro() {
  const progs = await api("/api/programs");
  const sel = $("#programSelect");
  if (!progs.length) {
    sel.innerHTML = `<option>No programs yet</option>`;
    $("#macroGrid").innerHTML = `<div class="empty">No macrocycle yet. Create one with the buttons above, or ask the Coach.</div>`;
    return;
  }
  if (!currentProgramId || !progs.some((p) => p.id === currentProgramId)) currentProgramId = progs[0].id;
  sel.innerHTML = progs.map((p) => `<option value="${p.id}" ${p.id === currentProgramId ? "selected" : ""}>${escapeHtml(p.name)}</option>`).join("");
  sel.onchange = () => { currentProgramId = Number(sel.value); renderMacro(); };
  renderMacro();
}

async function renderMacro() {
  const grid = await api(`/api/programs/${currentProgramId}`);
  const sport = grid.program.sport;
  const cols = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let html = `<table class="macrogrid"><thead><tr><th class="wk">Wk</th>${cols.map((c) => `<th>${c}</th>`).join("")}<th class="vol">Volume</th></tr></thead><tbody>`;
  for (const w of grid.weeks) {
    html += `<tr class="${w.is_current ? "current" : ""} ${w.deload ? "deload" : ""}">`;
    html += `<td class="wk">${w.week}${w.deload ? '<span class="dlbadge">deload</span>' : ""}</td>`;
    for (const d of w.days) {
      if (!d.session) { html += `<td class="mc-empty"></td>`; continue; }
      const s = d.session;
      const km = s.planned_km != null ? `<span class="mc-km">${s.planned_km}km</span>` : "";
      html += `<td class="mc-cell ${d.is_today ? "today" : ""}" data-w="${w.week}" data-d="${d.weekday}" title="${escapeHtml(d.date)} — click to log">
        <span class="mc-role">${ICONS[s.type] || ""} ${escapeHtml(s.role || s.type)}</span>
        <span class="mc-int">${escapeHtml(s.intensity || "")}</span>${km}</td>`;
    }
    html += `<td class="vol">${w.km != null ? Math.round(w.km) + " km" : "—"}</td></tr>`;
  }
  html += `</tbody></table><p class="hint">${sport === "run" ? "Long run ~40% · speed ~20% · easy days ~20% each." : "Limit / ARC / volume block."} Click a session to log it to your calendar.</p>`;
  $("#macroGrid").innerHTML = html;
  $$("#macroGrid .mc-cell").forEach((c) => (c.onclick = () => logPlanned(c.dataset.w, c.dataset.d)));
}

async function logPlanned(week, weekday) {
  try {
    const res = await api(`/api/programs/${currentProgramId}/log_day`, jsonPost({ week: Number(week), weekday: Number(weekday) }));
    await openSheet({ title: "Session logged", message: `“${res.detail}” logged to ${res.logged}. It now shows in Week & Daily.`, submitLabel: "Got it", cancelLabel: null });
  } catch (e) {
    await openSheet({ title: "Couldn’t log", message: e.message, submitLabel: "OK", cancelLabel: null });
  }
}

$("#newRun").onclick = async () => { const p = await api("/api/programs", jsonPost({ preset: "running" })); currentProgramId = p.id; loadMacro(); };
$("#newClimb").onclick = async () => { const p = await api("/api/programs", jsonPost({ preset: "climbing" })); currentProgramId = p.id; loadMacro(); };
$("#delProgram").onclick = async () => {
  if (currentProgramId && await confirmSheet({ title: "Delete program?", message: "This removes the macrocycle and its planned sessions.", confirmLabel: "Delete", danger: true })) {
    await api(`/api/programs/${currentProgramId}`, { method: "DELETE" }); currentProgramId = null; loadMacro();
  }
};

// ===================== TAB AUTOCOMPLETE (ghost text) =====================

const COACH_SUGGESTIONS = [
  "Create a 10-week running program starting at 30km, +10% a week, week-4 deload",
  "8-week climbing block",
  "12-week running program, 40km, +8% a week, week-4 deload",
  "What's this week?",
];
const FOOD_SUGGESTIONS = [
  "3 eggs, a serving of Flourish, 50g blueberries",
  "Greek yogurt with berries and honey",
  "Chicken breast, rice, and salad",
  "Protein shake with a banana",
  "Oatmeal with peanut butter and a banana",
  "Salmon, potato, and greens",
];
let recentFoodNames = [];

function attachAutocomplete(input, ghostEl, getSuggestions) {
  const render = () => {
    const val = input.value;
    if (!val) { ghostEl.innerHTML = ""; input._sugg = ""; return; }
    const low = val.toLowerCase();
    const sugg = getSuggestions().find((s) => s.toLowerCase().startsWith(low) && s.length > val.length);
    if (!sugg) { ghostEl.innerHTML = ""; input._sugg = ""; return; }
    input._sugg = sugg;
    ghostEl.innerHTML = `<span class="g-typed">${escapeHtml(val)}</span><span class="g-rest">${escapeHtml(sugg.slice(val.length))}</span>`;
  };
  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("blur", () => (ghostEl.innerHTML = ""));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && input._sugg) {
      e.preventDefault();
      input.value = input._sugg;
      input._sugg = "";
      render();
    } else if (e.key === "Escape") {
      input._sugg = "";
      ghostEl.innerHTML = "";
    }
  });
}

async function initFoodSuggestions() {
  try {
    const v = await api("/api/day");
    recentFoodNames = (v.items || []).map((it) => it.name).filter(Boolean);
  } catch { /* ignore */ }
  attachAutocomplete($("#chatInput"), $("#chatGhost"), () => [...recentFoodNames, ...FOOD_SUGGESTIONS]);
}

// Width-dependent views (habit chart, weight sparkline) measure the live DOM, so
// re-render them when the pane size changes.
let _resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    for (const key of ["a", "b"]) {
      if (paneState[key] === "habits" || paneState[key] === "eatingweek") VIEWS[paneState[key]].load();
    }
  }, 150);
});

// boot
initFoodSuggestions();
initPanes();
