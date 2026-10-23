// Общие помощники для страниц кабинета: запросы к API, форматирование, безопасный HTML.
const API = "/api/v1";

async function api(method, path, body) {
  const res = await fetch(API + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const err = new Error((data && data.message) || `Ошибка ${res.status}`);
    err.detail = data && data.detail;
    throw err;
  }
  return data;
}

const fmt = {
  rub: (x) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(x) + " ₽",
  pct: (x, d = 1) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d }).format(x * 100) + "%",
  date: (s) => new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
};

// Экранирование: данные из API (названия, причины) вставляем только через esc()
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Цвет статуса: зелёный — деньги будут, жёлтый — на других условиях, синий — ждём действий, красный — отказ
const TONE = {
  OFFER_READY: "ok", OFFER_ACCEPTED: "ok", ISSUED: "ok",
  COUNTER_OFFER: "warn", ALTERNATIVE_OFFER: "warn",
  INFO_REQUESTED: "info", MANUAL_REVIEW: "info", SUBMITTED: "info",
  DECLINED: "bad", CANCELLED: "muted",
};

const ACTOR = { client: "клиент", system: "система", analyst: "аналитик" };

function showError(el, err) {
  el.hidden = false;
  let text = err.message || String(err);
  if (Array.isArray(err.detail)) {
    text += ": " + err.detail.map((d) => d.msg).join("; ");
  }
  el.textContent = text;
}

function hide(el) { el.hidden = true; el.textContent = ""; }

function statusCell(app) {
  return `<span class="status" data-tone="${TONE[app.status] || "muted"}">${esc(app.status_label)}</span>`;
}

function timelineHtml(events) {
  return `<ol class="timeline">${events.map((e) => `
    <li><time>${fmt.date(e.created_at)}</time><strong>${esc(e.to_status_label)}</strong>
      <span class="small"> — ${esc(ACTOR[e.actor] || e.actor)}${e.comment ? ": " + esc(e.comment) : ""}</span></li>`).join("")}
  </ol>`;
}

function checksHtml(checks) {
  const mark = { passed: "✓", failed: "✕", warning: "!" };
  return `<ul class="trace">${checks.map((c) => `
    <li class="${c.status}"><span class="mark" aria-label="${c.status}">${mark[c.status] || "·"}</span>
      <span>${esc(c.name)}</span><span class="det">${esc(c.details)}</span></li>`).join("")}</ul>`;
}

function reasonsHtml(reasons) {
  const cls = { negative: "neg", positive: "pos", neutral: "neu" };
  return `<ul class="reasons">${reasons.map((r) => `
    <li class="${cls[r.impact] || "neu"}">${esc(r.text)}${r.advice ? `<span class="advice">${esc(r.advice)}</span>` : ""}</li>`).join("")}</ul>`;
}

function offerTerms(o) {
  return `<table class="terms">
    <tr><th>Продукт</th><td>${esc(o.product_name)}</td></tr>
    <tr><th>Сумма</th><td>${fmt.rub(o.amount)}</td></tr>
    <tr><th>Срок</th><td>${o.term_months} мес.</td></tr>
    <tr><th>Ставка</th><td>${o.rate != null ? fmt.pct(o.rate) : "—"}</td></tr>
    <tr><th>Платёж в месяц</th><td>${o.monthly_payment != null ? fmt.rub(o.monthly_payment) : "—"}</td></tr>
    <tr><th>Обеспечение</th><td>${esc(o.collateral)}</td></tr>
  </table>`;
}
