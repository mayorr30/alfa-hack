// Кабинет аналитика: очередь заявок и решение по ручной проверке.
const $ = (id) => document.getElementById(id);

const FILTERS = [
  ["MANUAL_REVIEW", "На проверке"],
  ["", "Все"],
  ["INFO_REQUESTED", "Ждут данных"],
  ["DECLINED", "Отказы"],
  ["ISSUED", "Выданы"],
];
let current = "MANUAL_REVIEW";
let openId = null;

const FEATURE_LABELS = {
  business_age_months: "Срок бизнеса, мес.",
  log_revenue: "Масштаб выручки (log10)",
  net_margin: "Рентабельность",
  revenue_trend: "Динамика выручки",
  revenue_volatility: "Колебания оборотов",
  debt_to_revenue: "Долг / выручка",
  dscr: "Покрытие платежей (DSCR)",
  loan_to_revenue: "Запрос / выручка",
  bki_max_overdue_days_12m: "Макс. просрочка, дн.",
  bki_overdue_count_12m: "Число просрочек",
  bki_inquiries_3m: "Запросы в БКИ за 3 мес.",
  director_changes_12m: "Смены директора",
  legal_form: "Форма",
  industry: "Отрасль",
  tax_arrears: "Налоговый долг",
  mass_address: "Массовый адрес",
};

function renderFilters() {
  $("filters").innerHTML = FILTERS.map(([code, label]) =>
    `<button class="chip" data-code="${code}" aria-pressed="${code === current}">${label}</button>`).join("");
  document.querySelectorAll("#filters .chip").forEach((b) => {
    b.onclick = () => { current = b.dataset.code; renderFilters(); loadRows(); };
  });
}

async function loadRows() {
  const rows = await api("GET", "/bank/applications" + (current ? `?status=${current}` : ""));
  $("rows").innerHTML = rows.length ? rows.map((r) => `
    <tr data-id="${r.id}" tabindex="0" class="${r.id === openId ? "is-open" : ""}">
      <td>${r.id}</td><td>${esc(r.company_name)}</td><td class="num">${fmt.rub(r.requested_amount)}</td>
      <td class="hide-sm">${esc(r.grade || "—")}</td><td>${statusCell(r)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="empty">${current === "MANUAL_REVIEW" ? "Очередь пуста: все заявки на проверке разобраны." : "Заявок с таким статусом нет."}</td></tr>`;
  document.querySelectorAll("#rows tr[data-id]").forEach((tr) => {
    const open = () => openApp(Number(tr.dataset.id));
    tr.onclick = open;
    tr.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
}

const VALUE_LABELS = {
  yes: "да", no: "нет", OOO: "ООО", IP: "ИП",
  trade: "торговля", food: "общепит", construction: "строительство", it: "IT и связь",
  manufacturing: "производство", transport: "транспорт", services: "услуги", agro: "сельское хозяйство",
};

function fmtFeature(k, v) {
  if (typeof v !== "number") return esc(VALUE_LABELS[v] || v);
  if (["net_margin", "revenue_trend", "debt_to_revenue", "loan_to_revenue"].includes(k)) return fmt.pct(v);
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(v);
}

async function openApp(id) {
  openId = id;
  document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("is-open", Number(tr.dataset.id) === id));
  const app = await api("GET", `/applications/${id}`);
  const a = app.assessment;
  const feats = a && a.features ? Object.entries(a.features).filter(([k]) => FEATURE_LABELS[k]) : [];
  $("panel").innerHTML = `
    <h2>Заявка №${app.id}</h2>
    <p class="small">${esc(app.company_name)}, ИНН ${esc(app.inn)}</p>
    <p><span class="status" data-tone="${TONE[app.status] || "muted"}">${esc(app.status_label)}</span>
      ${a && a.requires_manual_review ? `<br><span class="small">Флаги: ${esc(a.manual_review_flags.join("; "))}</span>` : ""}</p>
    ${a && a.offer ? offerTerms(a.offer) : ""}
    ${a && a.risk ? `<p class="small">Балл ${a.risk.score}, вероятность невозврата ${fmt.pct(a.risk.pd, 2)}, грейд ${esc(a.risk.grade)}. Модель ${esc(a.model_version)}, политика ${esc(a.policy_version)}.</p>` : ""}
    ${app.status === "MANUAL_REVIEW" ? `
      <div class="upload">
        <h3>Ваше решение</h3>
        <div class="field"><label for="comment">Комментарий для истории заявки</label>
          <textarea id="comment" class="input" rows="3" placeholder="Например: адрес проверен, офис по адресу есть"></textarea></div>
        <div class="actions"><button class="btn btn-primary" id="approve">Одобрить</button>
          <button class="btn" id="decline">Отказать</button></div>
        <div id="panelError" class="error" hidden></div>
      </div>` : ""}
    ${a ? `<h3 style="margin-top:24px">Проверки</h3>${checksHtml(a.checks)}
      <h3 style="margin-top:24px">Причины</h3>${a.reasons.length ? reasonsHtml(a.reasons) : `<p class="small">Нет.</p>`}` : ""}
    ${feats.length ? `<h3 style="margin-top:24px">Данные для модели</h3><table class="features">${feats.map(([k, v]) =>
      `<tr><td>${FEATURE_LABELS[k]}</td><td>${fmtFeature(k, v)}</td></tr>`).join("")}</table>` : ""}
    <h3 style="margin-top:24px">История</h3>${timelineHtml(app.events)}`;
  if (app.status === "MANUAL_REVIEW") {
    $("approve").onclick = () => decide(id, true);
    $("decline").onclick = () => decide(id, false);
  }
}

async function decide(id, approve) {
  hide($("panelError"));
  const comment = $("comment").value.trim();
  if (comment.length < 3) return showError($("panelError"), new Error("Напишите комментарий: он попадёт в историю заявки"));
  try {
    await api("POST", `/bank/applications/${id}/decision`, { approve, comment });
    await loadRows();
    await openApp(id);
  } catch (e) { showError($("panelError"), e); }
}

renderFilters();
loadRows();
