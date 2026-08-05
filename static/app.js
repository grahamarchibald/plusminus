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
let dailyDay = null;    // Daily tab
let weekStart = null;   // Weekly tab
let trainStart = null;  // Training tab

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
function statusOf(consumed, target) {
  if (!target) return "under";
  const pct = consumed / target;
  if (pct > 1.02) return "over";
  if (pct >= 0.9) return "ontrack";
  return "under";
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

// ===================== TABS =====================

function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => (p.hidden = p.id !== "panel-" + name));
  if (name === "daily") loadDaily();
  else if (name === "weekly") loadWeekly();
  else if (name === "training") showTrainSub(trainSub);
  else if (name === "habits") loadHabits();
}
$$(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));

// Training sub-tabs (Coach | Week | Macrocycle)
let trainSub = "coach";
function showTrainSub(name) {
  trainSub = name;
  $$(".subtab").forEach((t) => t.classList.toggle("active", t.dataset.sub === name));
  $$("#panel-training .subpanel").forEach((p) => (p.hidden = p.id !== "sub-" + name));
  if (name === "coach") initCoach();
  else if (name === "week") loadTraining();
  else if (name === "macro") loadMacro();
}
$$(".subtab").forEach((t) => (t.onclick = () => showTrainSub(t.dataset.sub)));

// ===================== CHAT =====================

const chatComposer = $("#chatComposer");
chatComposer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#chatInput");
  const text = input.value.trim();
  if (!text) return;
  $("#chatHero").classList.add("shrunk");
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
  const t = $("#chatThread");
  t.scrollTop = t.scrollHeight;
  window.scrollTo(0, document.body.scrollHeight);
}

function addEstimateCard(est, text) {
  const conf = (est.confidence || "medium").toLowerCase();
  const t = est.total || {};
  const rows = (est.items || []).map((it) =>
    `<tr><td>${escapeHtml(it.name)}</td><td class="n">${Math.round(it.calories || 0)} cal · ${Math.round(it.protein || 0)}p ${Math.round(it.carb || 0)}c ${Math.round(it.fat || 0)}f</td></tr>`).join("");
  const assumptions = (est.assumptions || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
  const swings = (est.swing_factors || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
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
        <button class="act-confirm">Confirm &amp; log to today</button>
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
      source: "described", day: todayISO(),
    }));
    el.querySelector(".card-actions").innerHTML = `<span class="logged">✓ Logged to today</span>`;
    scrollThread();
  };
  scrollThread();
}

// ===================== DAILY =====================

async function loadDaily() {
  if (!dailyDay) dailyDay = todayISO();
  const v = await api(`/api/day?day=${dailyDay}`);
  $("#dTitle").textContent = dailyDay === todayISO() ? "Today" : fmtDate(dailyDay, { weekday: "short", month: "short", day: "numeric" });
  renderSummary(v, $("#dSummary"));
  renderRings(v, $("#dRings"), $("#dDayline"));
  renderLog(v.items, $("#dLog"));
}
$("#dPrev").onclick = () => { dailyDay = shift(dailyDay || todayISO(), -1); loadDaily(); };
$("#dNext").onclick = () => { dailyDay = shift(dailyDay || todayISO(), 1); loadDaily(); };
$("#dTrain").onclick = async () => { if (await addTrainingPrompt(dailyDay)) loadDaily(); };
$("#dSleep").onclick = async () => { if (await logSleepPrompt(dailyDay)) loadDaily(); };
$("#dWeigh").onclick = async () => { if (await weighInPrompt(dailyDay)) loadDaily(); };
$("#dTarget").onclick = async () => { if (await editTargetPrompt(dailyDay)) loadDaily(); };

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
    fill.classList.add(statusOf(consumed, target));
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
  if (!items.length) { host.innerHTML = `<div class="empty">No food logged — add it from the Chat tab.</div>`; return; }
  for (const it of items) {
    const conf = (it.confidence || "medium").toLowerCase();
    const el = document.createElement("div");
    el.className = "entry";
    const assum = (it.assumptions && it.assumptions.length) ? `<span class="assum">${escapeHtml(it.assumptions[0])}</span>` : "";
    el.innerHTML = `<span class="dot ${conf}" title="${conf} confidence"></span>
      <div class="name"><b>${escapeHtml(it.name)}</b>${assum}</div>
      <div class="macros"><span class="cal">${Math.round(it.calories || 0)}${it.uncertainty_cal ? " ±" + Math.round(it.uncertainty_cal) : ""}</span> · ${Math.round(it.protein || 0)}p ${Math.round(it.carb || 0)}c ${Math.round(it.fat || 0)}f</div>`;
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
    cell.onclick = () => { dailyDay = d.day; showTab("daily"); };
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

// ===================== TRAINING schedule =====================

async function loadTraining(start) {
  const wv = await api("/api/week" + (start ? `?start=${start}` : (trainStart ? `?start=${trainStart}` : "")));
  trainStart = wv.start;
  $("#tLabel").textContent = "Training · week of " + fmtDate(wv.start, { month: "short", day: "numeric" });
  const host = $("#tWeek");
  host.innerHTML = "";
  const today = todayISO();
  let totalBurn = 0;
  for (const d of wv.days) {
    const sessions = d.sessions || [];  // served inline by week_view — no per-day fetch
    totalBurn += (d.activity.total_burn || 0);
    const row = document.createElement("div");
    row.className = "trainrow" + (d.day === today ? " today" : "");
    const sHtml = sessions.length
      ? sessions.map((s) => `<div class="tsession">${ICONS[s.type] || ""} <b>${cap(s.type)}</b>${s.intensity ? " · " + s.intensity : ""}${s.duration_min ? " · " + s.duration_min + "min" : ""}${s.est_burn ? ` · <span class="muted">~${Math.round(s.est_burn)} kcal</span>` : ""}${s.detail ? `<div class="tdetail">${escapeHtml(s.detail)}</div>` : ""}</div>`).join("")
      : `<span class="muted">Rest / no session</span>`;
    row.innerHTML = `
      <div class="tr-day"><div class="tr-wd">${d.weekday}</div><div class="tr-date">${fmtDate(d.day, { month: "short", day: "numeric" })}</div>
        <div class="tr-tier">${d.target_calories ? Math.round(d.target_calories) + " cal" : ""}${d.scope ? ` <span class="scope">${d.scope}</span>` : ""}</div></div>
      <div class="tr-body">${sHtml}</div>
      <button class="secondary tr-add">+ Add</button>`;
    row.querySelector(".tr-add").onclick = async () => { if (await addTrainingPrompt(d.day)) loadTraining(trainStart); };
    host.appendChild(row);
  }
  const foot = document.createElement("p");
  foot.className = "hint";
  foot.textContent = `Week total burn: ~${Math.round(totalBurn)} kcal`;
  host.appendChild(foot);
}
$("#tPrev").onclick = () => loadTraining(shift(trainStart || todayISO(), -7));
$("#tNext").onclick = () => loadTraining(shift(trainStart || todayISO(), 7));

// ===================== HABITS =====================

async function loadHabits() {
  const [grid, series] = await Promise.all([api("/api/habit_grid?days=14"), api("/api/series?days=14")]);
  renderHabitGrid(grid);
  // map date -> {sleep, recovery} for the aligned journal chart
  const byDate = {};
  series.dates.forEach((d, i) => (byDate[d] = { sleep: series.sleep[i], recovery: series.recovery[i] }));
  requestAnimationFrame(() => drawJournalChart(grid.dates, byDate));
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
    const name = prompt("Rename habit:", s.textContent);
    if (name && name.trim()) { await api(`/api/habits/${s.dataset.id}/rename`, jsonPost({ name })); loadHabits(); }
  });
  table.querySelectorAll(".hx").forEach((b) => b.onclick = async () => {
    if (confirm("Remove this habit and its history?")) { await api(`/api/habits/${b.dataset.del}`, { method: "DELETE" }); loadHabits(); }
  });
}
$("#addHabit").onclick = async () => {
  const name = prompt("New habit (e.g. Meditate, 3L water, No alcohol):", "");
  if (name && name.trim()) { await api("/api/habits", jsonPost({ name })); loadHabits(); }
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

// ===================== SHARED ENTRY PROMPTS =====================

async function addTrainingPrompt(day) {
  const type = (prompt("Training type (climb / run / lift / dance / walk / mixed / rest):", "climb") || "").trim().toLowerCase();
  if (!type) return false;
  let intensity = "moderate", duration = 0, subjective = null, burn = null;
  if (type !== "rest") {
    intensity = (prompt("Intensity (easy / moderate / hard / max):", "moderate") || "moderate").trim().toLowerCase();
    duration = Number(prompt("Duration (minutes):", "60")) || 0;
    const s = prompt("Subjective difficulty 1–10 (optional):", ""); subjective = s ? Number(s) : null;
    const b = prompt("Calorie burn (blank = estimate from MET formula):", ""); burn = b ? Number(b) : null;
  }
  await api("/api/training", jsonPost({ day, type, intensity, duration_min: duration, subjective_difficulty: subjective, est_burn: burn }));
  return true;
}
async function logSleepPrompt(day) {
  const h = prompt("Hours slept (optional):", "");
  const rating = prompt("Sleep quality 1–10 (optional):", "");
  const deep = prompt("Deep sleep minutes (optional):", "");
  if (!h && !rating && !deep) return false;
  await api("/api/sleep", jsonPost({ day, duration_h: h ? Number(h) : null, manual_rating: rating ? Number(rating) : null, deep_min: deep ? Number(deep) : null }));
  return true;
}
async function weighInPrompt(day) {
  const w = prompt("Weight (lb):", ""); if (!w) return false;
  await api("/api/weigh_in", jsonPost({ day, weight: Number(w), unit: "lb" }));
  return true;
}
async function editTargetPrompt(day) {
  const view = await api(`/api/day?day=${day}`);
  const cur = view.target || {}, scope = cur.scope || "default";
  const cal = prompt(`Calorie target for '${scope}' days:`, Math.round(cur.calories || 0)); if (cal === null) return false;
  const pro = prompt("Protein (g):", Math.round(cur.protein || 0)); if (pro === null) return false;
  const carb = prompt("Carb (g):", Math.round(cur.carb || 0)); if (carb === null) return false;
  const fat = prompt("Fat (g):", Math.round(cur.fat || 0)); if (fat === null) return false;
  await api("/api/target", jsonPost({ calories: Number(cal), protein: Number(pro), carb: Number(carb), fat: Number(fat), scope }));
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
    thinking.innerHTML = mdLite(data.reply);
    if (data.program_id) {
      const btn = document.createElement("button");
      btn.className = "secondary viewmacro";
      btn.textContent = "View macrocycle →";
      btn.onclick = () => { currentProgramId = data.program_id; showTrainSub("macro"); };
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
    alert(`Logged “${res.detail}” to ${res.logged}. It now shows in Week & Daily.`);
  } catch (e) {
    alert(e.message);
  }
}

$("#newRun").onclick = async () => { const p = await api("/api/programs", jsonPost({ preset: "running" })); currentProgramId = p.id; loadMacro(); };
$("#newClimb").onclick = async () => { const p = await api("/api/programs", jsonPost({ preset: "climbing" })); currentProgramId = p.id; loadMacro(); };
$("#delProgram").onclick = async () => {
  if (currentProgramId && confirm("Delete this program?")) { await api(`/api/programs/${currentProgramId}`, { method: "DELETE" }); currentProgramId = null; loadMacro(); }
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

// boot
initFoodSuggestions();
showTab("chat");
