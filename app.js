/* My Market Watch — read-only viewer for data files produced by GitHub Actions.
   All text from news feeds is inserted with textContent (never innerHTML) so a headline can't inject code. */
"use strict";

const CATEGORY_ORDER = ["watchlist", "fed", "deals", "products", "market", "crypto_gold"];
const DIRECTION = {
  positive: { sym: "➕", label: "ดี", cls: "pos" },
  negative: { sym: "➖", label: "ร้าย", cls: "neg" },
  unclear: { sym: "⚪", label: "ยังไม่ชัด", cls: "unc" },
  mixed: { sym: "⚪", label: "มีทั้งดีและร้าย", cls: "unc" },
};
const IMPORTANCE = { high: "สำคัญสูง", medium: "สำคัญกลาง", low: "สำคัญต่ำ" };
const IMPACT = {
  revenue: "รายได้", margin: "กำไร/margin", competitive_advantage: "ความได้เปรียบทางการแข่งขัน",
  management: "ผู้บริหาร", regulation: "กฎระเบียบ/กฎหมาย", macro: "เศรษฐกิจมหภาค", other: "อื่นๆ",
};
const MACRO = { id: "MACRO", name: "ภาพรวมเศรษฐกิจ / Fed" };
const TZ = "Asia/Bangkok";
const LOCALE = "th-TH-u-ca-gregory"; // Thai month names, Gregorian year (2026) to match the data files

const state = {
  news: null, watchlist: null, expiring: null, history: null, weeks: null,
  view: "news", cat: "watchlist", filter: "all", ticker: "", q: "", histId: "", histHigh: false,
};
const entities = new Map(); // id -> { name, tradingview }

/* ---------------- helpers ---------------- */
const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode etc. */ } },
};

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children) if (c != null) n.append(c);
  return n;
}

function safeUrl(u) {
  try {
    const url = new URL(u, location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch { return null; }
}

function tvUrl(id) {
  const e = entities.get(id);
  return e && e.tradingview ? `https://www.tradingview.com/symbols/${e.tradingview}/` : null;
}

const fmtDateTime = new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtShort = new Intl.DateTimeFormat(LOCALE, { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtDay = new Intl.DateTimeFormat(LOCALE, { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
const fmtMonth = new Intl.DateTimeFormat(LOCALE, { timeZone: "UTC", month: "long", year: "numeric" });

function ago(iso) {
  const d = new Date(iso);
  const min = Math.round((Date.now() - d) / 60000);
  if (min < 1) return "เมื่อสักครู่";
  if (min < 60) return `${min} นาทีที่แล้ว`;
  if (min < 24 * 60) return `${Math.floor(min / 60)} ชม.ที่แล้ว`;
  return fmtShort.format(d);
}
const dayLabel = (ymd) => fmtDay.format(new Date(ymd + "T00:00:00Z"));
const monthLabel = (ym) => fmtMonth.format(new Date(ym + "-01T00:00:00Z"));
function todayBkk() { // YYYY-MM-DD in Thai time
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
}

function matchesQuery(fields, q) {
  if (!q) return true;
  const hay = fields.filter(Boolean).join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
}

/* ---------------- shared bits ---------------- */
function tickerTag(id) {
  const url = tvUrl(id);
  const name = entities.get(id)?.name || id;
  if (!url) return el("span", { class: "tag", text: id, title: name });
  return el("a", { class: "tag", href: url, target: "_blank", rel: "noopener noreferrer", title: `กราฟ ${name} บน TradingView`, text: id });
}

function aiTags(ai) {
  const out = [];
  if (!ai) return out;
  if (ai.label === "noise") {
    out.push(el("span", { class: "tag noise", text: "เสียงรบกวน", title: "AI ประเมินว่าไม่กระทบพื้นฐานระยะยาว" }));
    return out;
  }
  const d = DIRECTION[ai.direction] || DIRECTION.unclear;
  out.push(el("span", { class: `tag ${d.cls}`, text: `${d.sym} ${d.label}`, title: "ทิศทางผลกระทบระยะยาว (AI ประเมิน)" }));
  if (ai.importance) out.push(el("span", { class: `tag${ai.importance === "high" ? " high" : ""}`, text: IMPORTANCE[ai.importance] || ai.importance }));
  if (ai.impact_area) out.push(el("span", { class: "tag", text: IMPACT[ai.impact_area] || ai.impact_area }));
  return out;
}

/* ---------------- banners ---------------- */
function renderBanners() {
  const box = $("banners");
  box.replaceChildren();
  const n = state.news;
  if (!n) return;

  if (!navigator.onLine) {
    box.append(el("div", { class: "banner warn" },
      el("span", { text: "📴" }),
      el("div", { class: "grow", text: "ออฟไลน์อยู่ — กำลังแสดงข้อมูลที่บันทึกไว้ล่าสุดในเครื่อง" })));
  }
  const ageH = (Date.now() - new Date(n.generated)) / 3.6e6;
  if (ageH > 30) {
    box.append(el("div", { class: "banner warn" },
      el("span", { text: "⏰" }),
      el("div", { class: "grow", text: `ข้อมูลไม่ได้อัปเดตมา ${Math.floor(ageH)} ชั่วโมงแล้ว — ระบบดึงข่าวอัตโนมัติอาจไม่ได้ทำงาน (ดูหัวข้อ "Actions ไม่รัน" ในคู่มือ)` })));
  }
  if (n.ai && !n.ai.ok) {
    box.append(el("div", { class: "banner err" },
      el("span", { text: "🤖" }),
      el("div", { class: "grow" },
        el("strong", { text: "AI ไม่ได้ติดป้ายข่าวรอบนี้" }),
        el("div", { text: `${n.ai.message || "ไม่ทราบสาเหตุ"} — ข่าวยังอ่านได้ตามปกติ แต่ไม่มีป้าย และระบบจะลองติดป้ายใหม่รอบหน้า` }))));
  }

  const x = state.expiring && state.expiring.next_deletion;
  if (x) {
    const days = Math.round((new Date(x.delete_on + "T00:00:00Z") - new Date(todayBkk() + "T00:00:00Z")) / 864e5);
    const key = `dismiss-expiring-${x.month}`;
    if (days > 0 && days <= (state.expiring.warning_days || 7) && !store.get(key)) {
      const actions = el("div", { class: "actions" });
      if (x.history_csv) actions.append(el("a", { class: "btn", href: x.history_csv, download: "", text: "⬇ ดาวน์โหลดประวัติข่าว (CSV)" }));
      if (x.weekly_csv) actions.append(el("a", { class: "btn", href: x.weekly_csv, download: "", text: "⬇ ดาวน์โหลดสรุปรายสัปดาห์ (CSV)" }));
      const banner = el("div", { class: "banner warn" },
        el("span", { text: "🗂️" }),
        el("div", { class: "grow" },
          el("strong", { text: `ข้อมูลเดือน${monthLabel(x.month)} จะถูกลบในอีก ${days} วัน` }),
          el("div", { text: `(วันที่ ${dayLabel(x.delete_on)}) ถ้าอยากเก็บไว้ ให้ดาวน์โหลดก่อน` }),
          actions),
        el("button", { class: "close", type: "button", "aria-label": "ปิดแถบเตือน", text: "✕",
          onclick: () => { store.set(key, "1"); banner.remove(); } }));
      box.append(banner);
    }
  }
}

/* ---------------- news view ---------------- */
function catCounts() {
  const counts = Object.fromEntries(CATEGORY_ORDER.map((c) => [c, 0]));
  for (const it of state.news.items) {
    if (state.filter === "fundamental" && it.ai?.label !== "fundamental") continue;
    for (const c of it.categories) if (c in counts) counts[c]++;
  }
  return counts;
}

function renderCatChips() {
  const names = state.news.categories || {};
  const counts = catCounts();
  const box = $("cat-chips");
  box.replaceChildren(...CATEGORY_ORDER.map((c) => el("button", {
    type: "button", class: `chip${!state.q && c === state.cat ? " on" : ""}`, "aria-pressed": String(c === state.cat),
    onclick: () => { state.cat = c; state.ticker = ""; store.set("cat", c); renderNews(); window.scrollTo({ top: 0 }); },
  }, names[c] || c, el("span", { class: "n", text: String(counts[c]) }))));
}

function renderTickerChips(items) {
  const box = $("ticker-chips");
  if (state.q || !["watchlist", "crypto_gold"].includes(state.cat)) { box.hidden = true; return; }
  const counts = new Map();
  for (const it of items) for (const t of it.tickers) counts.set(t, (counts.get(t) || 0) + 1);
  const ids = [...entities.keys()].filter((id) => counts.has(id));
  if (ids.length < 2) { box.hidden = true; return; }
  box.hidden = false;
  const chip = (id, label, n) => el("button", {
    type: "button", class: `chip${state.ticker === id ? " on" : ""}`,
    onclick: () => { state.ticker = state.ticker === id ? "" : id; renderNews(); },
  }, label, n != null ? el("span", { class: "n", text: String(n) }) : null);
  box.replaceChildren(chip("", "ทั้งหมด"), ...ids.map((id) => chip(id, id, counts.get(id))));
}

function newsCard(it) {
  const li = $("tpl-news").content.firstElementChild.cloneNode(true);
  if (it.ai?.label === "noise") li.classList.add("noise");
  const tickers = it.ai?.label === "fundamental" ? it.ai.tickers.filter((t) => t !== "MACRO") : it.tickers;
  li.querySelector(".meta-top").append(...aiTags(it.ai), ...tickers.map(tickerTag));
  const a = li.querySelector(".title");
  a.textContent = it.title;
  const href = safeUrl(it.link);
  if (href) a.href = href; else a.removeAttribute("href");
  li.querySelector(".summary").textContent = it.summary || "";
  if (it.ai?.label === "fundamental" && it.ai.reason) li.querySelector(".reason").textContent = "💡 " + it.ai.reason;
  const mb = li.querySelector(".meta-bottom");
  mb.append(el("span", { text: `${it.paywall ? "🔒 " : ""}${it.source}`, title: it.paywall ? "อาจต้องสมัครสมาชิกจึงอ่านเต็มได้" : null }));
  mb.append(el("span", { text: ago(it.published) }));
  if (it.also && it.also.length) {
    const also = el("span", {}, "อ่านจาก: ");
    it.also.forEach((x, i) => {
      const u = safeUrl(x.link);
      if (i) also.append(" · ");
      also.append(u ? el("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: `${x.paywall ? "🔒 " : ""}${x.source}` }) : x.source);
    });
    mb.append(also);
  }
  return li;
}

function renderNews() {
  renderCatChips();
  const q = state.q.trim();
  let items = state.news.items;
  if (q) {
    items = items.filter((it) => matchesQuery([it.title, it.summary, it.source, it.tickers.join(" "), it.ai?.reason,
      ...it.tickers.map((t) => entities.get(t)?.name)], q));
  } else {
    items = items.filter((it) => it.categories.includes(state.cat));
  }
  if (state.filter === "fundamental") items = items.filter((it) => it.ai?.label === "fundamental");
  renderTickerChips(items);
  if (state.ticker && !q) items = items.filter((it) => it.tickers.includes(state.ticker));

  $("news-count").textContent = q ? `ผลค้นหา ${items.length} ข่าว (ทุกหมวด)` : `${items.length} ข่าว · 7 วันล่าสุด`;
  const list = $("news-list");
  if (!items.length) {
    list.replaceChildren(el("li", { class: "empty", text: q ? "ไม่พบข่าวที่ตรงกับคำค้น" :
      state.filter === "fundamental" ? "ยังไม่มีข่าวที่ AI ประเมินว่ากระทบพื้นฐานในหมวดนี้" : "ยังไม่มีข่าวในหมวดนี้" }));
    return;
  }
  list.replaceChildren(...items.map(newsCard));
}

/* ---------------- history view ---------------- */
async function ensureHistory() {
  if (state.history) return true;
  try { state.history = await getJSON("history/index.json"); }
  catch { state.history = { months: [], items: [] }; }
  try { state.weeks = state.weeks || await getJSON("weekly/index.json"); } catch { /* optional */ }
  return true;
}

function histIds() {
  return [...[...entities.keys()], MACRO.id];
}

function countsFor(items) {
  const c = { positive: 0, negative: 0, unclear: 0, high: 0 };
  for (const r of items) { c[r.direction] = (c[r.direction] || 0) + 1; if (r.importance === "high") c.high++; }
  return c;
}

function renderHistorySelect() {
  const sel = $("hist-select");
  const opts = [el("option", { value: "", text: "ภาพรวมทุกตัว" })];
  for (const id of histIds()) {
    const name = id === MACRO.id ? MACRO.name : entities.get(id)?.name || id;
    opts.push(el("option", { value: id, text: id === MACRO.id ? name : `${id} — ${name}` }));
  }
  sel.replaceChildren(...opts);
  sel.value = state.histId;
}

async function renderHistory() {
  await ensureHistory();
  const all = state.history.items;
  const q = state.q.trim();
  const head = $("hist-summary");
  const list = $("hist-list");
  head.replaceChildren();
  list.replaceChildren();

  // CSV downloads
  const links = state.history.months.slice().reverse().map((m) => el("li", {},
    el("a", { href: `history/${m}.csv`, download: "", text: `ประวัติข่าว ${monthLabel(m)}` })));
  const weekMonths = new Set((state.weeks?.weeks || []).map((w) => w.date.slice(0, 7)));
  [...weekMonths].sort().reverse().forEach((m) => links.push(el("li", {},
    el("a", { href: `weekly/${m}.csv`, download: "", text: `สรุปรายสัปดาห์ ${monthLabel(m)}` }))));
  $("csv-links").replaceChildren(...(links.length ? links : [el("li", { text: "ยังไม่มีไฟล์" })]));

  let items = all;
  if (state.histHigh) items = items.filter((r) => r.importance === "high");
  if (q) items = items.filter((r) => matchesQuery([r.title, r.reason, r.source, r.tickers.join(" ")], q));

  if (!state.histId && !q) {
    // Overview table: one row per stock with counts.
    const rows = histIds().map((id) => {
      const mine = items.filter((r) => r.tickers.includes(id));
      return { id, c: countsFor(mine), n: mine.length };
    }).filter((r) => r.n).sort((a, b) => b.n - a.n);
    head.append(el("div", { class: "hist-head" },
      el("h2", { text: "ข่าวที่กระทบพื้นฐาน 12 เดือนล่าสุด" }),
      el("div", { class: "sub", text: `รวม ${items.length} รายการ${state.histHigh ? " (เฉพาะสำคัญสูง)" : ""} · แตะชื่อหุ้นเพื่อดูไทม์ไลน์` })));
    if (!rows.length) { list.append(el("li", { class: "empty", text: "ยังไม่มีข่าวที่กระทบพื้นฐานในประวัติ" })); return; }
    const table = el("table", { class: "overview" },
      el("thead", {}, el("tr", {}, el("th", { text: "หุ้น" }), el("th", { class: "num", text: "➕" }),
        el("th", { class: "num", text: "➖" }), el("th", { class: "num", text: "⚪" }), el("th", { class: "num", text: "สูง" }))),
      el("tbody", {}, ...rows.map((r) => el("tr", {
        "data-id": r.id, tabindex: "0",
        onclick: () => selectHist(r.id), onkeydown: (e) => { if (e.key === "Enter") selectHist(r.id); },
      },
        el("td", {}, el("strong", { text: r.id === MACRO.id ? "MACRO" : r.id }), " ",
          el("span", { class: "count", text: r.id === MACRO.id ? MACRO.name : entities.get(r.id)?.name || "" })),
        el("td", { class: "num pos", text: String(r.c.positive) }), el("td", { class: "num neg", text: String(r.c.negative) }),
        el("td", { class: "num unc", text: String(r.c.unclear) }), el("td", { class: "num", text: String(r.c.high) })))));
    list.append(el("li", {}, table));
    return;
  }

  if (state.histId) items = items.filter((r) => r.tickers.includes(state.histId));
  const id = state.histId;
  if (id) {
    const c = countsFor(all.filter((r) => r.tickers.includes(id)));
    const name = id === MACRO.id ? MACRO.name : entities.get(id)?.name || id;
    const url = tvUrl(id);
    head.append(el("div", { class: "hist-head" },
      el("h2", { text: id === MACRO.id ? name : `${id} — ${name}` }),
      el("div", { class: "counter" },
        el("span", { class: "tag pos", text: `➕ ${c.positive}` }), el("span", { class: "tag neg", text: `➖ ${c.negative}` }),
        el("span", { class: "tag unc", text: `⚪ ${c.unclear}` }), el("span", { class: "tag high", text: `สำคัญสูง ${c.high}` })),
      url ? el("div", { class: "sub" }, el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: "เปิดกราฟบน TradingView ↗" })) : null));
  } else {
    head.append(el("div", { class: "hist-head" }, el("h2", { text: `ผลค้นหาในประวัติ ${items.length} รายการ` })));
  }

  if (!items.length) { list.append(el("li", { class: "empty", text: "ไม่มีรายการ" })); return; }
  let month = "";
  for (const r of items) {
    const m = r.date.slice(0, 7);
    if (m !== month) { month = m; list.append(el("li", { class: "month-head", text: monthLabel(m) })); }
    const d = DIRECTION[r.direction] || DIRECTION.unclear;
    const href = safeUrl(r.link);
    list.append(el("li", { class: "card hist" },
      el("div", { class: "meta-top" },
        el("span", { class: `tag ${d.cls}`, text: `${d.sym} ${d.label}` }),
        el("span", { class: `tag${r.importance === "high" ? " high" : ""}`, text: IMPORTANCE[r.importance] || r.importance }),
        el("span", { class: "tag", text: IMPACT[r.impact_area] || r.impact_area }),
        ...(id ? [] : r.tickers.filter((t) => t !== MACRO.id).map(tickerTag))),
      el("p", { class: "reason", text: r.reason }),
      el("a", { class: "title", href, target: "_blank", rel: "noopener noreferrer", text: r.title }),
      el("div", { class: "meta-bottom" },
        el("span", { class: "date", text: dayLabel(r.date) }),
        el("span", { text: `${r.paywall ? "🔒 " : ""}${r.source}` }))));
  }
}

function selectHist(id) {
  state.histId = id;
  $("hist-select").value = id;
  renderHistory();
  window.scrollTo({ top: 0 });
}

/* ---------------- weekly view ---------------- */
async function renderWeekly() {
  const sel = $("week-select");
  const note = $("week-note");
  const body = $("week-body");
  if (!state.weeks) {
    try { state.weeks = await getJSON("weekly/index.json"); } catch { state.weeks = { weeks: [] }; }
  }
  const weeks = state.weeks.weeks || [];
  note.replaceChildren();
  const last = state.weeks.last_attempt;
  if (last && !last.ok && !weeks.some((w) => w.date === last.date)) {
    note.append(el("div", { class: "banner warn" }, el("span", { text: "ℹ️" }),
      el("div", { class: "grow", text: `สัปดาห์ล่าสุด (${dayLabel(last.date)}) ไม่มีสรุป — ${last.message || ""}` })));
  }
  if (!weeks.length) {
    sel.replaceChildren(el("option", { text: "ยังไม่มีสรุป" }));
    body.replaceChildren(el("p", { class: "empty", text: "ยังไม่มีสรุปรายสัปดาห์ — ฉบับแรกจะมาเช้าวันอาทิตย์ 07:00 น." }));
    return;
  }
  if (!sel.options.length || sel.options[0].value !== weeks[0].date) {
    sel.replaceChildren(...weeks.map((w) => el("option", { value: w.date, text: `${dayLabel(w.date)} — ${w.headline}` })));
  }
  const date = sel.value || weeks[0].date;
  let w;
  try { w = await getJSON(`weekly/${date}.json`); }
  catch { body.replaceChildren(el("p", { class: "empty", text: "โหลดสรุปสัปดาห์นี้ไม่ได้" })); return; }
  const s = w.summary;
  const section = (title, text) => text ? el("div", { class: "wk-card" }, el("h3", { text: title }), el("p", { text })) : null;
  const companies = s.companies.length ? el("div", { class: "wk-card" }, el("h3", { text: "รายบริษัท" }),
    ...s.companies.map((c) => {
      const d = DIRECTION[c.direction] || DIRECTION.unclear;
      return el("div", { class: "co" },
        el("div", { class: "co-head" }, tickerTag(c.t), el("span", { class: `tag ${d.cls}`, text: `${d.sym} ${d.label}` }),
          el("span", { class: "count", text: entities.get(c.t)?.name || "" })),
        el("p", { text: c.summary }));
    })) : null;
  body.replaceChildren(
    el("div", { class: "wk-card" },
      el("div", { class: "period", text: `สัปดาห์ ${dayLabel(w.period_start)} – ${dayLabel(w.period_end)}` }),
      el("h2", { text: s.headline }),
      el("p", { text: s.overview })),
    companies,
    section("เศรษฐกิจ / Fed", s.macro),
    section("BTC & ทองคำ", s.crypto_gold),
    section("เรื่องที่ควรติดตามต่อ", s.watch_next),
    el("p", { class: "foot", text: `สรุปจากข่าวที่กระทบพื้นฐาน ${w.news_count} ข่าว · AI: ${w.model} · ไม่ใช่คำแนะนำการลงทุน` }));
}

/* ---------------- stocks view (add / remove) ----------------
   The app never writes to GitHub itself (read-only rule, no keys in the page). It prepares the stock file and
   opens GitHub's own "new file" / "delete file" page; the signed-in user presses Commit there. */
const MARKET_COUNTRY = { NASDAQ: "US", NYSE: "US", AMEX: "US", OTC: "US", TSE: "JP", LSE: "UK", SET: "TH", HKEX: "HK" };
// Tickers that are also everyday words: only count them when a business word is in the same story.
const COMMON_WORDS = new Set(["A", "ALL", "AN", "ARE", "BE", "BIG", "BOX", "CAR", "CARS", "CAT", "COST", "DAY", "DOG", "EAT",
  "FAST", "FUN", "GO", "GOOD", "HAS", "HOPE", "IT", "KEY", "LIFE", "LOVE", "LOW", "MAIN", "MAN", "MOST", "NEW", "NOW", "ON",
  "ONE", "OPEN", "OUT", "PLAY", "REAL", "RUN", "SAFE", "SEE", "SHOP", "SO", "TEAM", "TRUE", "TWO", "UP", "WELL", "WIN", "YOU"]);
const DEFAULT_CONTEXT = ["stock*", "shares", "earnings", "revenue", "profit*", "CEO", "investor*", "guidance", "quarter*"];
const STOCK_ID_RE = /^[A-Z0-9][A-Z0-9._-]{0,11}$/;

function githubRepo() {
  const m = document.querySelector('meta[name="github-repo"]')?.content.trim();
  if (m) return m;
  if (location.hostname.endsWith(".github.io")) return `${location.hostname.split(".")[0]}/${location.pathname.split("/")[1]}`;
  return null;
}

const splitList = (s) => [...new Set(s.split(",").map((x) => x.replace(/["()]/g, "").trim()).filter((x) => x.length >= 2))];

function buildStock() {
  const ticker = $("f-ticker").value.trim().toUpperCase();
  const name = $("f-name").value.trim().replace(/["()]/g, "");
  const market = $("f-market").value;
  if (!STOCK_ID_RE.test(ticker)) return { error: "Ticker ใช้ได้เฉพาะตัวอักษรภาษาอังกฤษ ตัวเลข จุด หรือขีด เช่น AAPL, 6758, BRK.B" };
  if (name.length < 2) return { error: "กรุณาใส่ชื่อบริษัท" };
  if (entities.has(ticker)) return { error: `มี ${ticker} อยู่ในรายการแล้ว` };
  const tv = market === "OTHER" ? $("f-tv").value.trim().toUpperCase() : `${market}-${ticker}`;
  if (!tv) return { error: "กรุณาใส่สัญลักษณ์บน TradingView" };

  // "Apple Inc." -> "Apple": news rarely uses the legal suffix.
  const core = name.replace(/,?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|Limited|plc|PLC|Holdings?|Group|S\.A\.|N\.V\.|AG|SE|SA)$/i, "").trim() || name;
  const names = [...new Set([core, name, ...splitList($("f-names").value)])];
  // One-word names are matched case-sensitively ("Apple" but not "apple"); longer names in any case.
  const keywords = names.filter((n) => n.includes(" ") || n.includes("-"));
  const exact = names.filter((n) => !keywords.includes(n));
  const stock = { id: ticker, name, tickers: [ticker], country: MARKET_COUNTRY[market] || "", tradingview: tv };
  if (keywords.length) stock.keywords = keywords;
  if (!/^\d+$/.test(ticker)) {
    if (ticker.length >= 4 && !COMMON_WORDS.has(ticker)) exact.push(ticker);
    else { stock.weak_words = [ticker]; stock.context = [...DEFAULT_CONTEXT, core]; }
  }
  if (exact.length) stock.exact_words = [...new Set(exact)];
  const exclude = splitList($("f-exclude").value);
  if (exclude.length) stock.exclude = exclude;
  // Google News search: plain names joined with OR (brackets or extra words make it return nothing).
  stock.search = names.slice(0, 3).map((n) => (n.includes(" ") ? `"${n}"` : n)).join(" OR ");
  if ($("f-all").checked) stock.search_all_sources = true;
  stock.added = todayBkk();
  return { stock, words: [...(stock.keywords || []), ...(stock.exact_words || []), ...(stock.weak_words || []).map((w) => `${w} (เมื่อมีคำเกี่ยวกับหุ้น)`)] };
}

function updatePreview() {
  const r = buildStock();
  const ready = $("f-ticker").value.trim() && $("f-name").value.trim();
  $("f-tv-wrap").hidden = $("f-market").value !== "OTHER";
  $("f-preview").hidden = !(ready && r.stock);
  if (r.stock) {
    $("f-words").textContent = r.words.join(", ");
    $("f-tvlink").href = `https://www.tradingview.com/symbols/${encodeURIComponent(r.stock.tradingview)}/`;
  }
  if (!ready) $("f-error").textContent = "";
}

function pendingList() {
  try { return JSON.parse(store.get("pending-stocks") || "[]"); } catch { return []; }
}
function savePending(list) { store.set("pending-stocks", JSON.stringify(list)); }

function submitStock(e) {
  e.preventDefault();
  const r = buildStock();
  if (r.error) { $("f-error").textContent = r.error; return; }
  const repo = githubRepo();
  if (!repo) { $("f-error").textContent = "ไม่รู้ชื่อ repository — ตั้งค่า github-repo ใน index.html"; return; }
  const file = r.stock.id.replace(/[^A-Za-z0-9_-]/g, "_") + ".json";
  const url = `https://github.com/${repo}/new/main/stocks?filename=${encodeURIComponent(file)}` +
    `&value=${encodeURIComponent(JSON.stringify(r.stock, null, 2) + "\n")}`;
  savePending([...pendingList().filter((p) => p.id !== r.stock.id), { id: r.stock.id, name: r.stock.name, action: "add", at: Date.now() }]);
  window.open(url, "_blank", "noopener");
  $("add-form").reset();
  $("add-form").hidden = true;
  $("add-toggle").hidden = false;
  renderStocks();
}

function removeStock(s) {
  const repo = githubRepo();
  if (!s.file || !repo) {
    alert(`${s.id} อยู่ในไฟล์ watchlist.json — ลบได้โดยแก้ไฟล์นั้นบน GitHub (ดูคู่มือข้อ 6)`);
    return;
  }
  const ok = confirm(`เลิกติดตาม ${s.id} (${s.name})?\n\nแอปจะเปิดหน้า GitHub ให้กดปุ่มเขียว "Commit changes" เพื่อยืนยันการลบ\nประวัติข่าวเก่าของหุ้นนี้ยังอยู่จนครบ 12 เดือน`);
  if (!ok) return;
  savePending([...pendingList().filter((p) => p.id !== s.id), { id: s.id, name: s.name, action: "remove", at: Date.now() }]);
  window.open(`https://github.com/${repo}/delete/main/${s.file}`, "_blank", "noopener");
  renderStocks();
}

async function renderStocks() {
  const stocks = state.watchlist.stocks || [];
  const ids = new Set(stocks.map((s) => s.id));
  // Drop pending items that are done (or older than 2 hours — probably never committed).
  let pending = pendingList().filter((p) => (p.action === "add" ? !ids.has(p.id) : ids.has(p.id)) && Date.now() - p.at < 2 * 3.6e6);
  savePending(pending);

  const notes = $("stock-notes");
  notes.replaceChildren();
  for (const e of state.watchlist.errors || []) {
    notes.append(el("div", { class: "banner err" }, el("span", { text: "⚠️" }),
      el("div", { class: "grow", text: `ไฟล์ ${e.file} อ่านไม่ได้ (${e.error}) — ระบบข้ามหุ้นนี้ไปก่อน แก้ไฟล์บน GitHub หรือลบแล้วเพิ่มใหม่` })));
  }
  for (const p of pending) {
    notes.append(el("div", { class: "banner warn" }, el("span", { text: "⏳" }),
      el("div", { class: "grow" },
        el("strong", { text: `${p.action === "add" ? "กำลังเพิ่ม" : "กำลังลบ"} ${p.id} (${p.name})` }),
        el("div", { text: "ถ้ากด Commit บน GitHub แล้ว ระบบจะอัปเดตภายใน 3–5 นาที — ปิดแล้วเปิดแอปใหม่เพื่อดูผล" })),
      el("button", { class: "close", type: "button", "aria-label": "ซ่อน", text: "✕",
        onclick: () => { savePending(pendingList().filter((x) => x.id !== p.id)); renderStocks(); } })));
  }

  const newsCount = new Map();
  for (const it of state.news.items) for (const t of it.tickers) newsCount.set(t, (newsCount.get(t) || 0) + 1);
  await ensureHistory();
  const histCount = new Map();
  for (const r of state.history.items) for (const t of r.tickers) histCount.set(t, (histCount.get(t) || 0) + 1);

  const q = state.q.trim();
  const row = (s, removable) => {
    const url = tvUrl(s.id);
    return el("li", { class: "card" }, el("div", { class: "stock-row" },
      el("div", { class: "info" },
        url ? el("a", { class: "sym", href: url, target: "_blank", rel: "noopener noreferrer", text: s.id }) : el("strong", { text: s.id }),
        el("div", { class: "name", text: s.name }),
        el("div", { class: "stats", text: `ข่าว 7 วัน ${newsCount.get(s.id) || 0} · กระทบพื้นฐาน 12 เดือน ${histCount.get(s.id) || 0}` })),
      removable ? el("button", { class: "btn danger", type: "button", text: "เลิกติดตาม", onclick: () => removeStock(s) }) : null));
  };
  const shown = stocks.filter((s) => matchesQuery([s.id, s.name, ...(s.keywords || []), ...(s.exact_words || [])], q));
  $("stocks-title").textContent = `หุ้นที่ติดตาม (${stocks.length} ตัว)`;
  $("stock-list").replaceChildren(...(shown.length ? shown.map((s) => row(s, true)) : [el("li", { class: "empty", text: "ไม่พบหุ้น" })]));
  $("asset-list").replaceChildren(...(state.watchlist.assets || []).map((a) => row(a, false)));
}

/* ---------------- navigation, search, theme ---------------- */
const VIEWS = ["news", "history", "weekly", "stocks"];

function render() {
  if (!state.news) return;
  if (state.view === "news") renderNews();
  else if (state.view === "history") renderHistory();
  else if (state.view === "weekly") renderWeekly();
  else renderStocks();
}

function setView(v) {
  state.view = v;
  store.set("view", v);
  for (const s of VIEWS) {
    $(`view-${s}`).hidden = s !== v;
    const b = $(`nav-${s}`);
    b.classList.toggle("on", s === v);
    b.setAttribute("aria-current", s === v ? "page" : "false");
  }
  $("search").placeholder = v === "history" ? "ค้นหาในประวัติ 12 เดือน" : v === "stocks" ? "ค้นหาหุ้นในรายการ" : "ค้นหา หัวข่าว / หุ้น / สำนักข่าว";
  render();
  window.scrollTo({ top: 0 });
}

const THEMES = ["auto", "light", "dark"];
const THEME_ICON = { auto: "◐", light: "☀", dark: "☾" };
const THEME_LABEL = { auto: "ตามระบบ", light: "โหมดสว่าง", dark: "โหมดมืด" };
function applyTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  const b = $("theme-btn");
  b.textContent = THEME_ICON[t];
  b.title = `ธีม: ${THEME_LABEL[t]} (แตะเพื่อเปลี่ยน)`;
}

function init() {
  let theme = store.get("theme") || "auto";
  applyTheme(theme);
  $("theme-btn").addEventListener("click", () => {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    store.set("theme", theme);
    applyTheme(theme);
  });

  state.cat = CATEGORY_ORDER.includes(store.get("cat")) ? store.get("cat") : "watchlist";
  document.querySelectorAll(".bottom button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  const segs = document.querySelectorAll(".seg button");
  const setFilter = (f) => {
    state.filter = f;
    store.set("filter", f);
    segs.forEach((x) => { x.classList.toggle("on", x.dataset.filter === f); x.setAttribute("aria-pressed", String(x.dataset.filter === f)); });
  };
  setFilter(store.get("filter") === "fundamental" ? "fundamental" : "all");
  segs.forEach((b) => b.addEventListener("click", () => { setFilter(b.dataset.filter); renderNews(); }));
  let t;
  $("search").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.q = e.target.value; render(); }, 150);
  });
  $("hist-select").addEventListener("change", (e) => { state.histId = e.target.value; renderHistory(); });
  $("hist-high").addEventListener("change", (e) => { state.histHigh = e.target.checked; renderHistory(); });
  $("week-select").addEventListener("change", () => renderWeekly());
  $("add-toggle").addEventListener("click", () => {
    $("add-form").hidden = false;
    $("add-toggle").hidden = true;
    $("f-ticker").focus();
  });
  $("add-cancel").addEventListener("click", () => {
    $("add-form").reset();
    $("add-form").hidden = true;
    $("add-toggle").hidden = false;
    $("f-error").textContent = "";
    updatePreview();
  });
  $("add-form").addEventListener("input", () => { $("f-error").textContent = ""; updatePreview(); });
  $("add-form").addEventListener("submit", submitStock);

  load();
}

async function load() {
  try {
    // data/watchlist.json = stocks/ folder merged by the daily run; watchlist.json is the fallback before the first run.
    const [news, watchlist] = await Promise.all([
      getJSON("data/news.json"),
      getJSON("data/watchlist.json").catch(() => getJSON("watchlist.json")),
    ]);
    state.news = news;
    state.watchlist = watchlist;
    for (const s of watchlist.stocks || []) entities.set(s.id, { name: s.name, tradingview: s.tradingview });
    for (const a of watchlist.assets || []) entities.set(a.id, { name: a.name, tradingview: a.tradingview });
    $("updated").textContent = `อัปเดตล่าสุด ${fmtDateTime.format(new Date(news.generated))} น.`;
  } catch (e) {
    $("updated").textContent = "โหลดข้อมูลไม่ได้";
    $("banners").replaceChildren(el("div", { class: "banner err" }, el("span", { text: "⚠️" }),
      el("div", { class: "grow", text: "โหลดข่าวไม่ได้ — ตรวจอินเทอร์เน็ต หรือระบบยังไม่เคยดึงข่าวรอบแรก (ดูคู่มือ)" })));
    return;
  }
  try { state.expiring = await getJSON("history/expiring.json"); } catch { /* optional */ }
  renderHistorySelect();
  renderBanners();
  const v = store.get("view");
  setView(VIEWS.includes(v) ? v : "news");
  if (navigator.onLine) setTimeout(prefetchForOffline, 1500);
}

/* Download the other pages' data in the background so History and weekly summaries also work offline
   (the service worker keeps a copy of everything fetched). */
async function prefetchForOffline() {
  try {
    // news.json/expiring.json again: on the very first visit they load before the service worker takes control.
    await getJSON("data/news.json");
    await getJSON("data/watchlist.json").catch(() => null);
    await getJSON("history/expiring.json").catch(() => null);
    await getJSON("history/index.json");
    const idx = await getJSON("weekly/index.json");
    for (const w of (idx.weeks || []).slice(0, 8)) await getJSON(`weekly/${w.date}.json`);
  } catch { /* nothing to prefetch yet */ }
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}
window.addEventListener("online", () => { renderBanners(); });
window.addEventListener("offline", () => { renderBanners(); });

init();
