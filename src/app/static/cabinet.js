// Кабинет клиента: компания → задача → продукт → заявка → решение.
const $ = (id) => document.getElementById(id);

const state = {
  company: null,     // карточка из реестра
  demo: {},          // ИНН → демо-сценарий
  purpose: null,     // код цели, выбранный кнопкой
  recs: null,        // ответ подбора продуктов
  product: null,     // выбранный продукт
  app: null,         // открытая заявка
};

// ---------------- шаги ----------------
const STEPS = ["company", "need", "product", "submit", "result"];

function goTo(step) {
  const idx = STEPS.indexOf(step);
  STEPS.forEach((s, i) => {
    if (i <= idx) $("step-" + s).hidden = false;
    const li = document.querySelector(`#rail li[data-step="${s}"]`);
    li.classList.toggle("is-done", i < idx);
    li.classList.toggle("is-current", i === idx);
  });
  $("step-" + step).scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetAfter(step) {
  const idx = STEPS.indexOf(step);
  STEPS.slice(idx + 1).forEach((s) => { $("step-" + s).hidden = true; });
}

// ---------------- 1. компания ----------------
async function init() {
  const [demo, purposes] = await Promise.all([api("GET", "/demo/companies"), api("GET", "/reference/purposes")]);
  const scenario = {
    approve: "одобрение", counter_offer: "другие условия", request_info: "нужны данные",
    manual_review: "ручная проверка", decline_stop: "отказ по стоп-фактору",
    decline_risk: "отказ по риску", alternative_product: "другой продукт",
  };
  demo.forEach((c) => {
    state.demo[c.inn] = c;
    const o = document.createElement("option");
    o.value = c.inn;
    o.textContent = `${c.name} — ${scenario[c.scenario] || c.scenario}`;
    $("demo").appendChild(o);
  });
  purposes.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.dataset.code = p.code;
    b.textContent = p.label; b.setAttribute("aria-pressed", "false");
    b.onclick = () => choosePurpose(p.code === state.purpose ? null : p.code);
    $("purposeChips").appendChild(b);
  });
  goTo("company");
}

function choosePurpose(code) {
  state.purpose = code;
  document.querySelectorAll("#purposeChips .chip").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.code === code)));
}

$("demo").onchange = () => {
  if (!$("demo").value) return;
  $("inn").value = $("demo").value;
  loadCompany();
};

$("loadCompany").onclick = loadCompany;
$("inn").addEventListener("keydown", (e) => { if (e.key === "Enter") loadCompany(); });

async function loadCompany() {
  hide($("companyError"));
  const inn = $("inn").value.trim();
  if (!/^\d{10}(\d{2})?$/.test(inn)) {
    showError($("companyError"), new Error("ИНН — 10 цифр для ООО или 12 цифр для ИП"));
    return;
  }
  try {
    const c = await api("GET", `/companies/${inn}`);
    state.company = c;
    const active = c.status === "active";
    $("companyCard").innerHTML = `
      <div class="name">${esc(c.name)}</div>
      <div><dt>ИНН</dt><dd>${esc(c.inn)}</dd></div>
      <div><dt>Форма</dt><dd>${c.legal_form === "OOO" ? "ООО" : "ИП"}</dd></div>
      <div><dt>Статус в реестре</dt><dd><span class="tag ${active ? "tag-ok" : "tag-bad"}">${active ? "действует" : "ликвидация или банкротство"}</span></dd></div>
      <div><dt>Отрасль</dt><dd>${esc(c.industry_label || c.industry)}</dd></div>
      <div><dt>Работает</dt><dd>${c.business_age_months} мес.</dd></div>
      <div><dt>Руководитель</dt><dd>${esc(c.director)}</dd></div>`;
    $("companyCard").hidden = false;
    $("fillDemo").hidden = !state.demo[inn];
    resetAfter("need");
    goTo("need");
    loadHistory();
  } catch (e) {
    $("companyCard").hidden = true;
    showError($("companyError"), e);
  }
}

// ---------------- 2. задача ----------------
$("fillDemo").onclick = () => {
  const d = state.demo[state.company.inn];
  if (!d) return;
  choosePurpose(d.demo_request.purpose);
  $("purposeText").value = "";
  $("amount").value = d.demo_request.requested_amount;
  $("term").value = d.demo_request.term_months;
};

$("findProducts").onclick = async () => {
  hide($("needError"));
  const amount = Number($("amount").value), term = Number($("term").value);
  const text = $("purposeText").value.trim();
  if (!state.purpose && !text) return showError($("needError"), new Error("Опишите задачу или выберите вариант"));
  if (!(amount > 0) || !(term > 0)) return showError($("needError"), new Error("Укажите сумму и срок"));
  const body = { inn: state.company.inn, requested_amount: amount, term_months: term };
  if (state.purpose) body.purpose = state.purpose; else body.purpose_text = text;
  const btn = $("findProducts"); btn.disabled = true;
  try {
    state.recs = await api("POST", "/recommendations", body);
    state.product = null;
    renderProducts();
    resetAfter("product");
    goTo("product");
  } catch (e) { showError($("needError"), e); }
  finally { btn.disabled = false; }
};

// ---------------- 3. продукт ----------------
const CHANCE = { "высокий": "ok", "средний": "warn", "низкий": "bad", "очень низкий": "bad" };

function productHtml(p, i) {
  const notes = [...p.why, ...p.warnings, ...p.why_not];
  return `<div class="product${state.product === p.product_code ? " is-chosen" : ""}">
    <div>
      <h3>${esc(p.product_name)}</h3>
      <div class="fit">${esc(p.description)}. Ставка от ${fmt.pct(p.base_rate, 0)}, обеспечение: ${esc(p.collateral)}</div>
      ${notes.length ? `<ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    </div>
    <div class="side">
      <div class="pay">${fmt.rub(p.estimated_monthly_payment)}</div>
      <div class="small">в месяц по базовой ставке</div>
      <button class="btn ${p.eligible ? "" : "btn-quiet"}" data-i="${i}" style="margin-top:10px">
        ${state.product === p.product_code ? "Выбран" : "Выбрать"}</button>
    </div>
  </div>`;
}

function renderProducts() {
  const r = state.recs;
  $("purposeGuess").textContent = r.purpose.matched.length
    ? `Поняли задачу как «${r.purpose.label}» (по словам: ${r.purpose.matched.join(", ")}).`
    : `Цель: «${r.purpose.label}».`;
  if (r.prescore) {
    $("prescore").hidden = false;
    $("prescore").innerHTML = `Предварительная оценка: шанс одобрения <span class="status" data-tone="${CHANCE[r.prescore.approval_chance]}">${esc(r.prescore.approval_chance)}</span>. Это не решение банка: оно будет после отправки заявки.`;
  } else { $("prescore").hidden = true; }
  const fit = r.recommendations.filter((p) => p.eligible);
  const unfit = r.recommendations.filter((p) => !p.eligible);
  $("products").innerHTML = fit.length
    ? fit.map((p) => productHtml(p, r.recommendations.indexOf(p))).join("")
    : `<p class="empty">${esc(r.note || "Подходящих продуктов нет. Измените сумму, срок или задачу.")}</p>`;
  $("unfit").hidden = unfit.length === 0;
  $("unfit").querySelector("summary").textContent = `Не подходят сейчас: ${unfit.length}`;
  $("unfitList").innerHTML = unfit.map((p) => productHtml(p, r.recommendations.indexOf(p))).join("");
  document.querySelectorAll("#step-product button[data-i]").forEach((b) => {
    b.onclick = () => chooseProduct(r.recommendations[Number(b.dataset.i)]);
  });
}

function chooseProduct(p) {
  state.product = p.product_code;
  renderProducts();
  const amount = Number($("amount").value), term = Number($("term").value);
  $("summary").innerHTML = `
    <tr><th>Компания</th><td>${esc(state.company.name)}</td></tr>
    <tr><th>Задача</th><td>${esc(state.recs.purpose.label)}</td></tr>
    <tr><th>Продукт</th><td>${esc(p.product_name)}</td></tr>
    <tr><th>Сумма</th><td>${fmt.rub(amount)}</td></tr>
    <tr><th>Срок</th><td>${term} мес.</td></tr>`;
  hide($("submitError"));
  resetAfter("submit");
  goTo("submit");
}

// ---------------- 4. заявка ----------------
$("submitApp").onclick = async () => {
  hide($("submitError"));
  const body = {
    inn: state.company.inn,
    purpose: state.recs.purpose.purpose,
    purpose_text: $("purposeText").value.trim() || null,
    product_code: state.product,
    requested_amount: Number($("amount").value),
    term_months: Number($("term").value),
    consent_bki: $("consent").checked,
  };
  if ($("revenue").value) body.annual_revenue = Number($("revenue").value);
  if ($("margin").value) body.net_margin = Number($("margin").value) / 100;
  const btn = $("submitApp"); btn.disabled = true; btn.textContent = "Рассматриваем заявку…";
  try {
    const app = await api("POST", "/applications", body);
    showApp(app, true);
    loadHistory();
  } catch (e) { showError($("submitError"), e); }
  finally { btn.disabled = false; btn.textContent = "Отправить заявку"; }
};

// ---------------- 5. решение ----------------
function showApp(app, fresh = false) {
  state.app = app;
  renderSheet(app, fresh);
  $("step-result").hidden = false;
  goTo("result");
}

function renderSheet(app, fresh) {
  const a = app.assessment;
  const tone = TONE[app.status] || "muted";
  const decisionStatuses = ["OFFER_READY", "COUNTER_OFFER", "ALTERNATIVE_OFFER", "INFO_REQUESTED", "MANUAL_REVIEW", "DECLINED"];
  const msg = a && decisionStatuses.includes(app.status) ? a.client_message : app.status_hint;

  let body = "";
  if (["OFFER_READY", "COUNTER_OFFER", "ALTERNATIVE_OFFER"].includes(app.status) && a) {
    body += a.offers.map((o, i) => `
      <div class="offer">
        <div class="offer-head"><strong>${a.offers.length > 1 ? `Вариант ${i + 1}` : "Условия"}</strong><span class="small">${esc(o.comment)}</span></div>
        ${offerTerms(o)}
        <button class="btn btn-primary" data-accept="${i}">Принять эти условия</button>
      </div>`).join("");
  }
  if (["OFFER_ACCEPTED", "ISSUED"].includes(app.status) && app.accepted_offer) {
    body += `<div class="offer">${offerTerms(app.accepted_offer)}
      ${app.status === "OFFER_ACCEPTED" ? `<button class="btn btn-primary" id="signBtn">Подписать договор</button>` : ""}</div>`;
  }
  if (app.status === "MANUAL_REVIEW" && a && a.offer) {
    body += `<div class="offer"><div class="offer-head"><strong>Предварительные условия</strong>
      <span class="small">${esc(a.manual_review_flags.join("; "))}</span></div>${offerTerms(a.offer)}</div>`;
  }
  if (app.status === "INFO_REQUESTED" && a) body += infoForm(app, a);

  const risk = a && a.risk
    ? `<p class="small">Оценка риска: ${a.risk.score} баллов, вероятность невозврата ${fmt.pct(a.risk.pd, 2)}, грейд ${esc(a.risk.grade)}.</p>` : "";

  $("sheet").innerHTML = `
    <article class="sheet${fresh ? " is-new" : ""}" data-tone="${tone}">
      <div class="sheet-head"><span class="small">Заявка №${app.id}, ${esc(app.company_name)}</span>
        <span class="small">${fmt.rub(app.requested_amount)} на ${app.term_months} мес.</span></div>
      <div class="verdict">${esc(app.status_label)}</div>
      <p class="msg">${esc(msg)}</p>
      ${body}
      ${a ? `<div class="blocks">
        <div><h3>Почему такое решение</h3>${a.reasons.length ? reasonsHtml(a.reasons) : `<p class="small">Все условия выполнены.</p>`}</div>
        <div><h3>Как проверяли</h3>${checksHtml(a.checks)}${risk}</div>
      </div>` : ""}
      <h3 style="margin-top:28px">История заявки</h3>
      ${timelineHtml(app.events)}
      <div id="sheetError" class="error" hidden></div>
      ${app.actions.includes("cancel") ? `<div class="actions"><button class="btn btn-quiet" id="cancelBtn">Отозвать заявку</button></div>` : ""}
    </article>`;

  document.querySelectorAll("[data-accept]").forEach((b) => {
    b.onclick = () => act(`/applications/${app.id}/accept`, { offer_index: Number(b.dataset.accept) });
  });
  if ($("signBtn")) $("signBtn").onclick = () => act(`/applications/${app.id}/sign`);
  if ($("cancelBtn")) $("cancelBtn").onclick = () => act(`/applications/${app.id}/cancel`);
  if ($("infoBtn")) bindInfoForm(app);
}

function infoForm(app, a) {
  const needConsent = a.missing_data.some((m) => m.field === "consent_bki");
  const needFin = a.missing_data.some((m) => m.field === "statement_csv");
  const demo = state.demo[app.inn];
  return `<div class="upload">
    <h3>Чего не хватает</h3>
    <ul>${a.missing_data.map((m) => `<li><strong>${esc(m.label)}</strong>. ${esc(m.why)}</li>`).join("")}</ul>
    ${needConsent ? `<label class="check"><input type="checkbox" id="infoConsent"><span>Согласен на запрос кредитной истории</span></label>` : ""}
    ${needFin ? `
      <div class="field"><label for="infoFile">Выписка по расчётному счёту за 12 месяцев (CSV)</label>
        <input type="file" id="infoFile" accept=".csv,text/csv" class="input">
        <span class="hint">Колонки: date; amount; direction (in/out); counterparty; purpose</span></div>
      ${demo && demo.has_statement ? `<p><button type="button" class="btn-link" id="demoStmt">Подставить демо-выписку этой компании</button> <span class="small" id="demoStmtNote"></span></p>` : ""}` : ""}
    <button class="btn btn-primary" id="infoBtn">Отправить данные</button>
  </div>`;
}

function bindInfoForm(app) {
  let csv = null, filename = null;
  if ($("infoFile")) $("infoFile").onchange = async (e) => {
    const f = e.target.files[0];
    if (f) { csv = await f.text(); filename = f.name; }
  };
  if ($("demoStmt")) $("demoStmt").onclick = async () => {
    csv = await api("GET", `/companies/${app.inn}/demo-statement`);
    filename = "demo-vypiska.csv";
    $("demoStmtNote").textContent = "Выписка добавлена.";
  };
  $("infoBtn").onclick = () => {
    const body = {};
    if ($("infoConsent") && $("infoConsent").checked) body.consent_bki = true;
    if (csv) { body.statement_csv = csv; body.statement_filename = filename; }
    act(`/applications/${app.id}/info`, body);
  };
}

async function act(path, body) {
  const err = $("sheetError");
  hide(err);
  document.querySelectorAll("#sheet button").forEach((b) => { b.disabled = true; });
  try {
    const app = await api("POST", path, body || {});
    showApp(app, true);
    loadHistory();
  } catch (e) {
    showError(err, e);
    document.querySelectorAll("#sheet button").forEach((b) => { b.disabled = false; });
  }
}

// ---------------- история ----------------
async function loadHistory() {
  if (!state.company) return;
  const rows = await api("GET", `/applications?inn=${state.company.inn}`);
  $("history").hidden = false;
  $("historyRows").innerHTML = rows.length ? rows.map((r) => `
    <tr data-id="${r.id}" tabindex="0">
      <td>${r.id}</td><td class="num">${fmt.rub(r.requested_amount)}</td>
      <td class="num hide-sm">${r.term_months} мес.</td><td>${statusCell(r)}</td>
      <td class="hide-sm">${fmt.date(r.updated_at)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="empty">У компании пока нет заявок.</td></tr>`;
  document.querySelectorAll("#historyRows tr[data-id]").forEach((tr) => {
    const open = async () => showApp(await api("GET", `/applications/${tr.dataset.id}`));
    tr.onclick = open;
    tr.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
}

init().catch((e) => showError($("companyError"), e));
