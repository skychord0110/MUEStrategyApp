// MUEStrategy モバイル（閲覧専用）
// データは同じオリジンの /api/* から読むだけ。外部のサーバには何も送らない。
// 画面の文字はすべて textContent で入れる（ログ由来の文字列をHTMLとして解釈しない）。
"use strict";

// ── 小道具 ───────────────────────────────────────────

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}
const $ = (id) => document.getElementById(id);
const fill = (id, ...nodes) => $(id).replaceChildren(...nodes.flat());

const yen = (v) => (v == null ? "—" : Math.round(v).toLocaleString("ja-JP") + "円");
const num = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString("ja-JP", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (v, d = 2) => (v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%");
const signed = (v, fmt) => el("span", { class: v > 0 ? "plus" : v < 0 ? "minus" : "" }, fmt(v));
const signedYen = (v) => signed(v, (x) => (x == null ? "—" : (x > 0 ? "+" : "") + yen(x)));
const hm = (iso) => (iso ? iso.slice(11, 16) : "");
const md = (iso) => (iso ? iso.slice(5, 10).replace("-", "/") : "");
const empty = (text) => el("div", { class: "empty" }, text);

function ymd(d) {
  const z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}

async function api(path) {
  const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  const type = r.headers.get("content-type") || "";
  return type.includes("json") ? r.json() : r.text();
}

function showError(where, err) {
  fill(where, el("div", { class: "alert ng" }, "読み込みに失敗しました: " + err.message));
}

// ── タブ ─────────────────────────────────────────────

const TITLES = { home: "状況", perf: "成績", trades: "履歴", orders: "注文", reports: "レポート" };
let current = "home";

function show(view) {
  current = view;
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("on", b.dataset.view === view);
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== "view-" + view;
  $("title").textContent = TITLES[view];
  try { localStorage.setItem("view", view); } catch (e) { /* 保存できなくても動く */ }
  LOADERS[view]();
}

for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => show(b.dataset.view));

function segment(id, onChange) {
  const root = $(id);
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    for (const x of root.querySelectorAll("button")) x.classList.toggle("on", x === b);
    onChange(b.dataset.v);
  });
  return () => root.querySelector("button.on").dataset.v;
}

// ── 状況 ─────────────────────────────────────────────

const STATE = { 1: "待機", 2: "処理中", 3: "処理済", 4: "訂正取消送信中", 5: "終了" };
const SIDE = { 1: "売", 2: "買" };

function orderRows(orders) {
  if (!orders || !orders.length) return [empty("注文はありません")];
  return orders.map((o) => el("div", { class: "row" },
    el("div", { class: "l" },
      el("div", { class: "t" }, `${o.symbol} ${o.name || ""}`),
      el("div", { class: "s" }, `${SIDE[o.side] || o.side} ${num(o.qty)}株 ${o.price ? num(o.price, 1) + "円" : "成行"} ・ ${hm(o.recv_time)}`)),
    el("div", { class: "r" },
      el("span", { class: "chip" + (o.state === 5 ? "" : " warn") }, STATE[o.state] || o.state),
      el("div", { class: "s" }, `約定 ${num(o.filled)}/${num(o.qty)}`))));
}

async function loadHome() {
  let st, acc, today;
  try {
    [st, acc, today] = await Promise.all([
      api("/api/status"), api("/api/account"),
      api(`/api/trades?kind=paper&from=${ymd(new Date())}&to=${ymd(new Date())}`),
    ]);
  } catch (e) { return showError("home-alert", e); }

  const badge = $("runner-badge");
  badge.className = "badge " + (st.runner_running ? "ok" : "ng");
  badge.textContent = st.runner_running ? "ランナー稼働中" : "ランナー停止中";

  const alerts = [];
  if (acc.available && !acc.fresh) alerts.push(el("div", { class: "alert" }, `口座の情報が古くなっています（${Math.round(acc.age_seconds / 60)}分前）。ランナーが止まっている可能性があります`));
  const outage = st.notices.find((n) => n.kind === "runner_notice" && n.text.includes("応答しません"));
  if (outage) alerts.push(el("div", { class: "alert ng" }, `${hm(outage.at)} ${outage.text}`));
  fill("home-alert", alerts);

  // 口座
  const s = acc.fresh ? acc.snapshot : null;
  $("acc-age").textContent = s ? `${hm(s.updated_at)} 時点` : acc.available ? "（古い値）" : "（ランナー停止中）";
  $("acc-total").textContent = s ? yen(s.total_assets) : "—";
  $("acc-bp").textContent = s ? yen(s.buying_power) : "—";
  fill("acc-pl", s ? signedYen(s.total_pl) : "—");
  const pos = s ? s.positions : [];
  fill("acc-positions", el("div", { class: "rows" },
    s && s.error ? el("div", { class: "alert ng" }, s.error) : null,
    pos.length ? pos.map((p) => el("div", { class: "row" },
      el("div", { class: "l" },
        el("div", { class: "t" }, `${p.symbol} ${p.name}`),
        el("div", { class: "s" }, `${num(p.qty)}株 @${num(p.price, 1)} → ${num(p.current, 1)}`)),
      el("div", { class: "r" }, signedYen(p.pl), el("div", { class: "s" }, pct(p.pl_rate))))) :
      s ? empty("保有している建玉はありません") : null));

  // 自動売買
  const at = st.config.autotrade;
  const mode = !at.enabled ? ["無効", ""] : at.dry_run ? ["DRY-RUN（発注なし）", "warn"] : ["実発注", "real"];
  fill("autotrade",
    el("div", {}, el("span", { class: "chip " + mode[1] }, mode[0]),
      at.strategies.length ? "対象: " + at.strategies.map((k) => labelOf(st, k)).join("、") : "対象なし"),
    st.autotrade_mode ? el("div", { class: "s dim" }, `ランナー起動時: ${st.autotrade_mode.text}（${md(st.autotrade_mode.at)} ${hm(st.autotrade_mode.at)}）`) : null);

  // 本日の仮想売買
  const pt = st.paper_today;
  $("pt-entries").textContent = num(pt.entries);
  $("pt-holding").textContent = num(pt.holding || 0);
  fill("pt-ret", pt.total_return == null ? "—" : signed(pt.total_return, pct));
  fill("pt-today", el("div", { class: "rows" }, today.length ? today.map(tradeRow) : empty("本日のエントリーはまだありません")));

  fill("orders-today", el("div", { class: "rows" }, s ? orderRows(s.orders) : empty("ランナー停止中のため取得できません")));

  fill("strategies", el("div", {}, st.config.strategies.map((x) =>
    el("span", { class: "chip" + (x.enabled ? " on" : "") }, x.label))));

  $("err-count").textContent = st.errors_today ? `エラー ${st.errors_today}件` : "";
  fill("notices", el("div", { class: "rows" }, st.notices.length ? st.notices.slice(0, 10).map((n) =>
    el("div", { class: "row" }, el("div", { class: "l" },
      el("div", { class: "s" }, `${hm(n.at)} ${n.kind === "error" ? "エラー" : "ランナー"}`),
      el("div", {}, n.text)))) : empty("ありません")));
}

function labelOf(st, key) {
  const x = st.config.strategies.find((s) => s.key === key);
  return x ? x.label : key;
}

// ── 売買1件 ───────────────────────────────────────────

function tradeRow(t) {
  const open = t.exit_at == null;
  const stale = open && t.trade_date < ymd(new Date());
  return el("div", { class: "row" },
    el("div", { class: "l" },
      el("div", { class: "t" }, t.symbol, " ", el("span", { class: "s" }, t.label)),
      el("div", { class: "s" },
        `${md(t.entry_at)} ${hm(t.entry_at)} ${num(t.entry_price, 1)}円`,
        open ? "" : ` → ${hm(t.exit_at)} ${num(t.exit_price, 1)}円`,
        t.qty ? ` ・ ${num(t.qty)}株` : "",
        t.estimated ? " ・ 推計" : "")),
    el("div", { class: "r" },
      open ? el("span", { class: "chip" + (stale ? "" : " warn") }, stale ? "決済記録なし" : "保有中")
        : signed(t.return_pct, pct),
      t.pnl != null ? el("div", {}, signedYen(t.pnl)) : null,
      el("div", { class: "s" }, t.exit_reason || "")));
}

// ── 成績 ─────────────────────────────────────────────

const COLORS = ["#4a9eff", "#4ade80", "#f0a040", "#e879f9", "#f0605e", "#22d3ee", "#facc15", "#a3a3a3", "#fb7185", "#818cf8"];

function rangeOf(kind) {
  const now = new Date();
  if (kind === "week") {
    const d = new Date(now); d.setDate(now.getDate() - ((now.getDay() + 6) % 7));
    return [ymd(d), ymd(now)];
  }
  if (kind === "month") return [ymd(new Date(now.getFullYear(), now.getMonth(), 1)), ymd(now)];
  if (kind === "last") return [ymd(new Date(now.getFullYear(), now.getMonth() - 1, 1)), ymd(new Date(now.getFullYear(), now.getMonth(), 0))];
  if (kind === "custom") return [$("perf-from").value, $("perf-to").value];
  return ["", ""];
}

const perfKind = segment("perf-kind", () => loadPerf());
const perfRange = segment("perf-range", (v) => { $("perf-custom").hidden = v !== "custom"; loadPerf(); });
$("perf-from").addEventListener("change", loadPerf);
$("perf-to").addEventListener("change", loadPerf);
$("perf-est").addEventListener("change", loadPerf);

async function loadPerf() {
  const kind = perfKind();
  const [from, to] = rangeOf(perfRange());
  const q = new URLSearchParams({ kind });
  if (from) q.set("from", from);
  if (to) q.set("to", to);
  if ($("perf-est").checked) q.set("estimated", "true");
  $("perf-est").parentElement.hidden = kind !== "paper";
  let p;
  try { p = await api("/api/performance?" + q); } catch (e) { return showError("perf-list", e); }
  $("perf-period").textContent = from || to ? `${from || "最初"} 〜 ${to || "今日"}` : "全期間";

  const colorOf = {};
  p.strategies.forEach((s, i) => (colorOf[s.strategy] = COLORS[i % COLORS.length]));
  drawChart(p, colorOf);

  fill("perf-list", p.strategies.length ? p.strategies.map((s) => el("div", { class: "card" },
    el("div", { class: "card-h" }, el("i", { class: "swatch" }), s.label,
      el("span", { class: "dim" }, `${s.closed}件${s.open ? `・未決済${s.open}` : ""}`)),
    el("div", { class: "stat-grid" },
      kpi("勝率", s.win_rate == null ? "—" : num(s.win_rate, 1) + "%"),
      kpi("平均", signed(s.avg_return, pct)),
      kpi("合計", signed(s.total_return, (v) => pct(v, 1))),
      kind === "real" ? kpi("損益", signedYen(s.pnl)) : kpi("勝/負", `${s.wins || 0}/${s.losses || 0}`)),
    el("div", { class: "s dim" },
      `最大 ${pct(s.best)} ・ 最小 ${pct(s.worst)}`,
      Object.keys(s.exit_reasons).length ? " ・ " + Object.entries(s.exit_reasons).map(([k, v]) => `${k}${v}`).join(" ") : ""))
  ) : el("div", { class: "card" }, empty("この期間の売買はありません")));
  // 凡例の色（CSPでstyle属性を使わないため、CSSOMで入れる）
  document.querySelectorAll("#perf-list .swatch").forEach((sw, i) => {
    Object.assign(sw.style, { display: "inline-block", width: "10px", height: "10px", borderRadius: "3px", background: colorOf[p.strategies[i].strategy] });
  });
}

function kpi(k, v) {
  return el("div", { class: "kpi" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v));
}

function drawChart(p, colorOf) {
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs, ...kids) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    for (const c of kids) e.append(c);
    return e;
  };
  const dates = [...new Set(p.daily.map((d) => d.trade_date))];
  if (!dates.length) { fill("perf-chart", empty("データがありません")); fill("perf-legend"); return; }

  // 戦略ごとの累積
  const series = {};
  for (const s of p.strategies) series[s.strategy] = dates.map(() => 0);
  const total = dates.map(() => 0);
  for (const d of p.daily) {
    const i = dates.indexOf(d.trade_date);
    if (series[d.strategy]) series[d.strategy][i] += d.ret;
    total[i] += d.ret;
  }
  const cum = (a) => a.reduce((acc, v) => (acc.push((acc.length ? acc[acc.length - 1] : 0) + v), acc), []);
  const lines = Object.entries(series).map(([k, v]) => [k, cum(v)]);
  const totalLine = cum(total);

  const W = 340, H = 180, L = 34, R = 6, T = 8, B = 18;
  const all = [0, ...totalLine, ...lines.flatMap(([, v]) => v)];
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = (i) => L + (dates.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (dates.length - 1));
  const y = (v) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const path = (v) => v.map((val, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(val).toFixed(1)}`).join("");

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "累積損益率" },
    svgEl("line", { class: "axis", x1: L, x2: W - R, y1: H - B, y2: H - B }),
    svgEl("line", { class: "zero", x1: L, x2: W - R, y1: y(0), y2: y(0) }),
    svgEl("text", { x: L - 4, y: y(hi) + 4, "text-anchor": "end" }, `${hi.toFixed(0)}%`),
    svgEl("text", { x: L - 4, y: y(lo) + 4, "text-anchor": "end" }, `${lo.toFixed(0)}%`),
    svgEl("text", { x: L, y: H - 4 }, md(dates[0])),
    svgEl("text", { x: W - R, y: H - 4, "text-anchor": "end" }, md(dates[dates.length - 1])));
  for (const [k, v] of lines) svg.append(svgEl("path", { d: path(v), fill: "none", stroke: colorOf[k], "stroke-width": 1.4, "stroke-opacity": 0.85 }));
  svg.append(svgEl("path", { d: path(totalLine), fill: "none", stroke: "currentColor", "stroke-width": 2.4 }));
  fill("perf-chart", svg);

  const legendItem = (color, text) => {
    const i = el("i");
    i.style.background = color;
    return el("span", {}, i, text);
  };
  fill("perf-legend", legendItem("currentColor", `合計 ${pct(totalLine[totalLine.length - 1], 1)}`),
    p.strategies.map((s) => legendItem(colorOf[s.strategy], s.label)));
}

// ── 履歴 ─────────────────────────────────────────────

const trKind = segment("tr-kind", () => loadTrades());
for (const id of ["tr-from", "tr-to", "tr-strategy"]) $(id).addEventListener("change", loadTrades);
{
  const now = new Date();
  $("tr-from").value = ymd(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 13));
  $("tr-to").value = ymd(now);
  $("perf-to").value = ymd(now);
}

async function loadTrades() {
  const q = new URLSearchParams({ kind: trKind() });
  if ($("tr-from").value) q.set("from", $("tr-from").value);
  if ($("tr-to").value) q.set("to", $("tr-to").value);
  if ($("tr-strategy").value) q.set("strategy", $("tr-strategy").value);
  q.set("estimated", "true");
  let rows;
  try {
    rows = await api("/api/trades?" + q);
    if ($("tr-strategy").options.length === 1) {
      const st = await api("/api/status");
      for (const s of st.config.strategies) $("tr-strategy").append(el("option", { value: s.key }, s.label));
    }
  } catch (e) { return showError("tr-list", e); }
  // 日付ごとにまとめる
  const byDate = new Map();
  for (const t of rows) {
    if (!byDate.has(t.trade_date)) byDate.set(t.trade_date, []);
    byDate.get(t.trade_date).push(t);
  }
  fill("tr-list", byDate.size ? [...byDate].map(([d, ts]) => {
    const closed = ts.filter((t) => t.return_pct != null);
    const sum = closed.reduce((a, t) => a + t.return_pct, 0);
    return el("div", { class: "card" },
      el("div", { class: "card-h" }, d, el("span", { class: "dim" }, `${ts.length}件`),
        closed.length ? signed(sum, (v) => pct(v, 1)) : null),
      el("div", { class: "rows" }, ts.map(tradeRow)));
  }) : el("div", { class: "card" }, empty("この期間の売買はありません")));
}

// ── 注文 ─────────────────────────────────────────────

async function loadOrders() {
  let o;
  try { o = await api("/api/orders"); } catch (e) { return showError("orders-live", e); }
  fill("orders-live", el("div", { class: "rows" }, o.today ? orderRows(o.today) : empty("ランナー停止中のため取得できません")));
  fill("orders-history", el("div", { class: "rows" }, o.history.length ? o.history.map((h) =>
    el("div", { class: "row" },
      el("div", { class: "l" },
        el("div", { class: "t" }, `${h.symbol || ""} ${h.side || ""} ${h.qty != null ? num(h.qty) + "株" : ""}`.trim() || h.what),
        el("div", { class: "s" }, `${md(h.at)} ${hm(h.at)} ・ ${h.strategy || ""} ${h.note || h.text || ""}`)),
      el("div", { class: "r" }, el("span", { class: "chip" + (/失敗|エラー/.test(h.what) ? " real" : "") }, h.what),
        h.price != null ? el("div", { class: "s" }, num(h.price, 1) + "円") : null))) : empty("記録はありません")));
}

// ── レポート ──────────────────────────────────────────

async function loadReports() {
  let list;
  try { list = await api("/api/reports"); } catch (e) { return showError("rp-list", e); }
  $("rp-body").hidden = true;
  fill("rp-list", el("div", { class: "card" }, el("div", { class: "rows" }, list.length ? list.map((r) =>
    el("div", { class: "row" }, el("div", { class: "l" },
      el("div", { class: "t" }, r.dir),
      r.files.map((f) => el("div", {}, el("button", { class: "link", onclick: () => openReport(r.dir, f) }, f)))))) :
    empty("レポートはありません"))));
}

async function openReport(dir, file) {
  let text;
  try { text = await api(`/api/reports/${encodeURIComponent(dir)}/${encodeURIComponent(file)}`); } catch (e) { return showError("rp-list", e); }
  fill("rp-list", el("button", { class: "link", onclick: loadReports }, "← 一覧に戻る"));
  const body = $("rp-body");
  body.replaceChildren(...renderMarkdown(text));
  body.hidden = false;
  window.scrollTo(0, 0);
}

// 最小限のMarkdown表示（見出し・表・箇条書き・コード・太字）。
// 外部ライブラリを読み込まないために自前で持つ。HTMLは解釈しない。
function inline(text) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    out.push(t.startsWith("**") ? el("strong", {}, t.slice(2, -2)) : el("code", {}, t.slice(1, -1)));
    last = m.index + t.length;
  }
  out.push(text.slice(last));
  return out;
}

function renderMarkdown(src) {
  const lines = src.replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const buf = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) buf.push(lines[i]);
      out.push(el("pre", {}, el("code", {}, buf.join("\n"))));
      i++;
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(el("h" + Math.min(h[1].length, 4), {}, inline(h[2]))); i++; continue; }
    if (/^\s*\|/.test(line)) {
      const rows = [];
      for (; i < lines.length && /^\s*\|/.test(lines[i]); i++) rows.push(lines[i]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
      const [head, ...rest] = body;
      out.push(el("table", {},
        el("thead", {}, el("tr", {}, cells(head).map((c) => el("th", {}, inline(c))))),
        el("tbody", {}, rest.map((r) => el("tr", {}, cells(r).map((c) => el("td", {}, inline(c))))))));
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      for (; i < lines.length && /^\s*[-*]\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
      out.push(el("ul", {}, items.map((t) => el("li", {}, inline(t)))));
      continue;
    }
    if (line.trim()) out.push(el("p", {}, inline(line)));
    i++;
  }
  return out;
}

// ── 起動 ─────────────────────────────────────────────

const LOADERS = { home: loadHome, perf: loadPerf, trades: loadTrades, orders: loadOrders, reports: loadReports };

// 状況タブを開いているあいだは15秒ごとに更新（ランナーの書き出し間隔と同じ）
setInterval(() => { if (current === "home" && !document.hidden) loadHome(); }, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && current === "home") loadHome(); });

let start = "home";
try { start = localStorage.getItem("view") || "home"; } catch (e) { /* 既定のまま */ }
show(LOADERS[start] ? start : "home");

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
