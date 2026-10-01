/* CycleCare frontend — بلا مكتبات خارجية، يعمل مع FastAPI من نفس الأصل */
"use strict";

const API = ""; // نفس الأصل (يُخدم من FastAPI)

const state = {
  mode: "chat",
  loading: false,
  tab: "cycles",
  name: "",     // الاسم الذي يناديها به المساعد (محفوظ على الخادم)
  messages: [], // {role, text?, data?}
};

/* ---------------- مفاتيح التخزين المحلي ---------------- */

const SETTINGS_KEY = "cyclecare_context_v1";   // بيانات شخصية (تبقى في المتصفح)
const DEVICE_KEY = "cyclecare_device_v1";      // معرّف جهاز لعزل البيانات على الخادم

function deviceKey() {
  let key = localStorage.getItem(DEVICE_KEY);
  if (!key) {
    // معرّف عشوائي: ليس مصادقة، الغرض عزل صفوف قاعدة البيانات فقط
    key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
    localStorage.setItem(DEVICE_KEY, key);
  }
  return key;
}

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
  } catch {
    return {};
  }
}

/* ---------------- البيانات الشخصية ---------------- */

function fillSettingsForm(s) {
  document.getElementById("f-age").value = s.age ?? "";
  document.getElementById("f-country").value = s.country_code ?? "";
  document.getElementById("f-pregnancy").value = s.pregnancy_status ?? "";
  document.getElementById("f-contraception").value = s.contraception ?? "";
  document.getElementById("f-conditions").value =
    (s.conditions || []).map((c) => `${c.name} | ${c.status}`).join("\n");
}

function readSettingsForm() {
  const conditions = document.getElementById("f-conditions").value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, status] = line.split("|").map((x) => (x || "").trim());
      return { name, status: status || "مشخّصة" };
    })
    .filter((c) => c.name);

  const num = (id) => {
    const v = document.getElementById(id).value;
    return v === "" ? null : Number(v);
  };

  return {
    age: num("f-age"),
    country_code: document.getElementById("f-country").value || null,
    pregnancy_status: document.getElementById("f-pregnancy").value || null,
    contraception: document.getElementById("f-contraception").value || null,
    conditions,
  };
}

async function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(readSettingsForm()));
  await saveName(document.getElementById("f-name").value);
  togglePanel();
}

function clearSettings() {
  localStorage.removeItem(SETTINGS_KEY);
  fillSettingsForm({});
  document.getElementById("f-name").value = "";
  saveName("");
}

/* ---------------- الاسم (ملف المستخدمة على الخادم) ---------------- */

const profileUrl = () => `/v1/profile?user_key=${encodeURIComponent(deviceKey())}`;

function applyName(name) {
  state.name = name || "";
  document.getElementById("f-name").value = state.name;
  const title = document.querySelector("#welcome h2");
  if (title) {
    title.textContent = state.name
      ? window.i18n.t("ui.welcome_named", { name: state.name })
      : window.i18n.t("ui.welcome_title");
  }
}

async function loadName() {
  try {
    applyName((await api(profileUrl())).display_name);
  } catch { /* بلا اسم: المساعد يخاطبها بصيغة عامة */ }
}

async function saveName(raw) {
  try {
    /* الخادم هو من ينظّف الاسم؛ نعرض ما أعاده كما سيراه المساعد */
    applyName((await api(profileUrl(), "PUT", { display_name: raw || "" })).display_name);
  } catch (e) {
    alert(`${window.i18n.t("ui.server_error")}: ${e.message}`);
  }
}

/* ---------------- البلد ورقم الطوارئ ---------------- */

async function loadCountries() {
  const select = document.getElementById("f-country");
  try {
    const meta = await api("/v1/meta", "GET");
    const saved = loadSettings().country_code;
    select.innerHTML = `<option value="">${i18nText("ui.country_none", "غير محدد")}</option>` +
      (meta.countries || []).map((c) =>
        `<option value="${c.code}"${c.code === saved ? " selected" : ""}>`
        + `${c.name_ar} — ${c.emergency}</option>`
      ).join("");
    if (!saved && meta.default_country) select.value = meta.default_country;
  } catch {
    select.innerHTML = `<option value="">${i18nText("ui.country_unavailable", "غير متاح")}</option>`;
  }
}

/* ---------------- الوضع ---------------- */

function setMode(mode) {
  state.mode = mode;
  document.getElementById("mode-chat").classList.toggle("active", mode === "chat");
  document.getElementById("mode-summary").classList.toggle("active", mode === "summary");
  document.getElementById("input").placeholder =
    mode === "summary" ? "اطلب ملخصًا لدوراتي…" : "اكتبي سؤالك هنا…";
}

/* ---------------- المحادثة ---------------- */

const chatEl = () => document.getElementById("chat");

function hideWelcome() {
  const w = document.getElementById("welcome");
  if (w) w.remove();
}

function addMessage(role, content) {
  hideWelcome();
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (typeof content === "string") {
    div.textContent = content;
  } else {
    renderRich(div, content);
  }
  chatEl().appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

function renderRich(container, data) {
  if (data.overview !== undefined) {
    container.classList.add("summary-card");
    const sections = [
      ["ui.summary_overview", "نظرة عامة", data.overview],
      ["ui.summary_changed", "ما الذي تغيّر", data.what_changed],
      ["ui.summary_patterns", "الأنماط المرصودة", data.patterns],
      ["ui.summary_alerts", "تنبيهات", data.medical_alerts],
      ["ui.summary_not_meaning", "ما لا يعنيه هذا", data.what_this_does_not_mean],
    ];
    sections.forEach(([key, fallback, text]) => {
      if (!text) return;
      const box = document.createElement("div");
      box.className = "summary-section";
      const h = document.createElement("h4");
      h.textContent = i18nText(key, fallback);
      const p = document.createElement("div");
      p.textContent = text;
      p.style.whiteSpace = "pre-line";
      box.append(h, p);
      container.appendChild(box);
    });
    if ((data.questions_for_doctor || []).length) {
      const box = document.createElement("div");
      box.className = "summary-section";
      const h = document.createElement("h4");
      h.textContent = i18nText("ui.summary_doctor_questions", "أسئلة لطبيبتِك");
      const ul = document.createElement("ul");
      data.questions_for_doctor.forEach((q) => {
        const li = document.createElement("li");
        li.textContent = q;
        ul.appendChild(li);
      });
      box.append(h, ul);
      container.appendChild(box);
    }
    appendMeta(container, data);
    return;
  }

  container.textContent = data.answer || "";
  container.style.whiteSpace = "pre-line";
  appendMeta(container, data);
}

function appendMeta(container, data) {
  const meta = document.createElement("div");
  meta.className = "msg-meta";

  const add = (cls, text) => {
    const b = document.createElement("span");
    b.className = `badge ${cls}`;
    b.textContent = text;
    meta.appendChild(b);
  };

  if (data.needs_doctor) {
    add("doctor", i18nText("ui.needs_doctor", "يستحق مراجعة طبية"));
  }
  /* صراحة دائمة: المقالات غير المراجَعة طبيًا تُعلَن، ولا يُخفى أن الرد بلا موديل */
  if (data.knowledge_review === "unreviewed") {
    add("unreviewed", i18nText("ui.badge_unreviewed", "معلومات تثقيفية عامة — غير مراجَعة طبيًا"));
  }
  if (data.decision === "extractive" || data.decision === "offline") {
    add("simple", i18nText("ui.badge_simple_mode", "وضع مبسّط: من غير نموذج لغوي"));
  }
  const rules = data.rule_codes || [];
  const sources = data.sources_used || [];
  /* العدّ بصيغة الجمع الصحيحة (مصدر واحد/مصدران/3 مصادر…) بدل «المصادر: 3» */
  if (sources.length) {
    add("source", window.i18n
      ? window.i18n.tn("units.sources_count", sources.length)
      : String(sources.length));
  }
  /* عناوين ما اعتُمد عليه فعلًا — تتبّع الرد إلى مقاطع المعرفة */
  (data.sources || []).forEach((src) => add("source", src.title));
  (data.missing_info || []).forEach((m) => add("missing", m));
  rules.forEach((r) => add("rule", r));

  if (meta.children.length) container.appendChild(meta);
}

function addError(text) {
  hideWelcome();
  const div = document.createElement("div");
  div.className = "msg msg-error";
  div.textContent = text;
  chatEl().appendChild(div);
}

/* ---------------- الطوارئ ---------------- */

function showOverlay(data) {
  const overlay = document.getElementById("overlay");
  const crisis = !!data.crisis;
  overlay.classList.toggle("crisis", crisis);
  document.getElementById("overlay-title").textContent = crisis
    ? i18nText("ui.overlay_crisis", "دواعي فورية لطلب المساعدة")
    : i18nText("ui.overlay_medical", "رعاية طبية عاجلة");

  /* نص الرد يأتي من الخادم كما هو (نص أمني مُراجَع — لا تُعاد صياغته في
   * الواجهة). الرقم وحده يُعزل اتجاهيًا حتى لا ينقلبه BiDi داخل جملة عربية. */
  const textEl = document.getElementById("overlay-text");
  const number = data.emergency_payload && data.emergency_payload.number;
  const answer = data.answer || "";
  textEl.textContent = "";
  if (number && answer.includes(number)) {
    const parts = answer.split(number);
    parts.forEach((part, index) => {
      textEl.appendChild(document.createTextNode(part));
      if (index < parts.length - 1) {
        const span = document.createElement("span");
        span.className = "phone-number";
        span.setAttribute("data-ltr", "");
        span.textContent = number;
        textEl.appendChild(span);
      }
    });
  } else {
    textEl.textContent = answer;
  }
  overlay.hidden = false;
}

function closeOverlay() {
  document.getElementById("overlay").hidden = true;
}

/* ---------------- الإرسال ---------------- */

async function ask(text) {
  if (!text || state.loading) return;
  setModeFromShortcut(text);
  addMessage("user", text);
  await send(text);
}

function setModeFromShortcut(text) {
  if (text.includes("ملخص") && state.mode !== "summary") setMode("summary");
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${API}${path}`, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const detail = typeof err.detail === "string" ? err.detail : `HTTP ${resp.status}`;
    throw new Error(detail);
  }
  return resp.json();
}

async function send(text) {
  state.loading = true;
  document.getElementById("send-btn").disabled = true;
  document.getElementById("typing").hidden = false;
  scrollBottom();

  try {
    const settings = readSettingsForm();
    /* مسار واحد للمحادثة والملخص: /api/v1/ai/*. الملخص يقرأ دوراتها وأعراضها من
     * الخادم بالمعرّف نفسه، فما تسجّله في اللوحة هو ما يلخّصه المساعد. */
    const summary = state.mode === "summary";
    const data = await api(summary ? "/api/v1/ai/summary" : "/api/v1/ai/chat", "POST", {
      message: summary ? "" : text,
      user_key: deviceKey(),
      user_name: state.name || null,
      country_code: settings.country_code,
      user_context: settings,
      language: window.i18n ? window.i18n.state.locale : null,
    });
    state.messages.push({ role: "user", text }, { role: "bot", data });

    const emergency = data.emergency || data.decision === "emergency_filter";
    if (emergency) {
      showOverlay({ ...data, answer: data.answer || data.overview });
      addMessage("bot", data.answer || data.overview);
    } else {
      addMessage("bot", data);
    }
  } catch (e) {
    const prefix = window.i18n ? window.i18n.t("ui.server_error") : "تعذّر الاتصال بالخادم";
    addError(`${prefix}: ${e.message}`);
  } finally {
    state.loading = false;
    document.getElementById("send-btn").disabled = false;
    document.getElementById("typing").hidden = true;
    scrollBottom();
  }
}

function scrollBottom() {
  const c = chatEl();
  c.scrollTop = c.scrollHeight;
}

function handleSubmit(event) {
  event.preventDefault();
  const input = document.getElementById("input");
  const text = input.value.trim();
  if (!text || state.loading) return false;
  input.value = "";
  input.style.height = "auto";
  addMessage("user", text);
  send(text);
  return false;
}

function handleKey(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    handleSubmit(event);
  }
}

/* ---------------- اللوحة الموحّدة ---------------- */

const PANEL_TABS = ["cycles", "symptoms", "insights", "profile"];

function togglePanel() {
  const el = document.getElementById("panel");
  el.hidden = !el.hidden;
  if (!el.hidden) refreshTracker();
}

function setPanelTab(tab) {
  state.tab = tab;
  PANEL_TABS.forEach((t) => {
    document.getElementById(`tab-${t}`).classList.toggle("active", t === tab);
    document.getElementById(`pane-${t}`).hidden = t !== tab;
  });
  if (tab === "insights") refreshInsights();
}

async function refreshTracker() {
  await Promise.all([loadCycles(), loadSymptoms()]);
  if (state.tab === "insights") await refreshInsights();
}

function fmtDate(iso) {
  try {
    return new Date(iso + "T00:00:00").toLocaleDateString("ar-EG",
      { year: "numeric", month: "long", day: "numeric" });
  } catch {
    return iso;
  }
}

async function loadCycles() {
  const list = document.getElementById("cycles-list");
  try {
    const rows = await api(`/v1/cycles?user_key=${encodeURIComponent(deviceKey())}`);
    document.getElementById("cycles-count").textContent =
      rows.length ? `${rows.length} مسجّل` : "";
    if (!rows.length) {
      list.innerHTML = '<li class="muted">لا يوجد تسجيل بعد.</li>';
      return;
    }
    list.innerHTML = "";
    rows.forEach((c) => {
      const li = document.createElement("li");
      const label = c.length_days
        ? `${fmtDate(c.start_date)} · ${c.length_days} يوم نزيف`
        : fmtDate(c.start_date);
      const span = document.createElement("span");
      span.textContent = label;
      const btn = document.createElement("button");
      btn.className = "link-danger";
      btn.textContent = "حذف";
      btn.onclick = () => removeCycle(c.id);
      li.append(span, btn);
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = `<li class="muted">تعذّر تحميل البيانات: ${e.message}</li>`;
  }
}

async function addCycle() {
  const start = document.getElementById("c-date").value;
  const len = document.getElementById("c-length").value;
  if (!start) return alert("اختاري تاريخ أول يوم نزيف.");
  try {
    await api(`/v1/cycles?user_key=${encodeURIComponent(deviceKey())}`, "POST", {
      start_date: start,
      length_days: len === "" ? null : Number(len),
    });
    document.getElementById("c-date").value = "";
    document.getElementById("c-length").value = "";
    await loadCycles();
  } catch (e) {
    alert(`تعذّرت الإضافة: ${e.message}`);
  }
}

async function removeCycle(id) {
  if (!confirm("حذف هذه الدورة؟")) return;
  try {
    await api(`/v1/cycles/${id}?user_key=${encodeURIComponent(deviceKey())}`, "DELETE");
    await loadCycles();
  } catch (e) {
    alert(`تعذّر الحذف: ${e.message}`);
  }
}

async function loadSymptoms() {
  const list = document.getElementById("symptoms-list");
  try {
    const rows = await api(`/v1/symptoms?user_key=${encodeURIComponent(deviceKey())}`);
    document.getElementById("symptoms-count").textContent =
      rows.length ? `${rows.length} سجل` : "";
    if (!rows.length) {
      list.innerHTML = '<li class="muted">لا يوجد تسجيل بعد.</li>';
      return;
    }
    list.innerHTML = "";
    rows.forEach((s) => {
      const li = document.createElement("li");
      const sev = s.severity ? ` · شدّة ${s.severity}/5` : "";
      const note = s.note ? ` · ${s.note}` : "";
      const span = document.createElement("span");
      span.textContent = `${fmtDate(s.log_date)} — ${s.symptom}${sev}${note}`;
      const btn = document.createElement("button");
      btn.className = "link-danger";
      btn.textContent = "حذف";
      btn.onclick = () => removeSymptom(s.id);
      li.append(span, btn);
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = `<li class="muted">تعذّر تحميل البيانات: ${e.message}</li>`;
  }
}

async function addSymptom() {
  const day = document.getElementById("s-date").value;
  const name = document.getElementById("s-name").value.trim();
  const sev = document.getElementById("s-severity").value;
  const note = document.getElementById("s-note").value.trim();
  if (!day) return alert("اختاري التاريخ.");
  if (!name) return alert("اكتبي اسم العرض.");
  try {
    await api(`/v1/symptoms?user_key=${encodeURIComponent(deviceKey())}`, "POST", {
      log_date: day,
      symptom: name,
      severity: sev === "" ? null : Number(sev),
      note: note || null,
    });
    document.getElementById("s-name").value = "";
    document.getElementById("s-severity").value = "";
    document.getElementById("s-note").value = "";
    await loadSymptoms();
  } catch (e) {
    alert(`تعذّرت الإضافة: ${e.message}`);
  }
}

async function removeSymptom(id) {
  if (!confirm("حذف هذا السجل؟")) return;
  try {
    await api(`/v1/symptoms/${id}?user_key=${encodeURIComponent(deviceKey())}`, "DELETE");
    await loadSymptoms();
  } catch (e) {
    alert(`تعذّر الحذف: ${e.message}`);
  }
}

async function refreshInsights() {
  const box = document.getElementById("insights-body");
  box.innerHTML = '<p class="muted">جارٍ التحليل…</p>';
  try {
    const data = await api(`/v1/insights?user_key=${encodeURIComponent(deviceKey())}`);
    box.innerHTML = "";

    const summary = document.createElement("p");
    summary.className = "muted";
    summary.textContent = data.cycles_recorded
      ? `عدد الدورات المسجّلة: ${data.cycles_recorded}` +
        (data.avg_cycle_days ? ` · متوسط الطول: ${data.avg_cycle_days} يومًا` : "")
      : "لا توجد دورات مسجّلة بعد؛ سجّلي دورة على الأقل ليبدأ التحليل.";
    box.appendChild(summary);

    if (data.needs_doctor) {
      const note = document.createElement("div");
      note.className = "insight-alert";
      note.textContent = "في بياناتكِ ما يستحق مراجعة طبية. هذا ليس تشخيصًا.";
      box.appendChild(note);
    }

    (data.findings || []).forEach((f) => {
      const card = document.createElement("div");
      card.className = "insight-card";
      const h = document.createElement("h4");
      h.textContent = `${f.title} — ${f.rule_code}`;
      const p = document.createElement("p");
      p.textContent = data.glossary?.[f.rule_code] || "";
      const ev = document.createElement("small");
      ev.textContent = `الأرقام: ${(f.evidence || []).join(" · ")}`;
      card.append(h, p, ev);
      box.appendChild(card);
    });

    if (!(data.findings || []).length) {
      box.innerHTML += '<p class="muted">لا نتائج بعد.</p>';
    }
  } catch (e) {
    box.innerHTML = `<p class="muted">تعذّر التحليل: ${e.message}</p>`;
  }
}

function showInsights() {
  document.getElementById("panel").hidden = false;
  setPanelTab("insights");
  refreshTracker();
}

async function deleteAllData() {
  if (!confirm("سيُحذف كل ما سجّلتِه من دورات وأعراض. متأكدة؟")) return;
  try {
    await api(`/v1/data?user_key=${encodeURIComponent(deviceKey())}`, "DELETE");
    await refreshTracker();
    alert("تم حذف كل بياناتك.");
  } catch (e) {
    alert(`تعذّر الحذف: ${e.message}`);
  }
}

/* ---------------- تهيئة ---------------- */

/* ---------------- التعريب ---------------- */
/* التهيئة قبل أي رسم: نقرأ إعداد اللغة من الخادم (/v1/meta) ثم نحمّل ملف
 * الموارد. لا قيم مكتوبة هنا: اللغة الافتراضية والأرقام وبداية الأسبوع كلها
 * من إعدادات الخادم حتى لا تختلف الواجهة عن الباك-إند. */
async function initI18n() {
  try {
    const meta = await api("/v1/meta", "GET");
    window.__LOCALE_CONFIG__ = meta.locale || {};
  } catch {
    window.__LOCALE_CONFIG__ = { default: "ar", supported: ["ar", "en"] };
  }
  await window.i18n.load();
  renderSuggestions();
}

/* نص من ملف الموارد. الاحتياطي يمنع ظهور مفتاح خام لو نادى كودٌ الترجمة قبل
 * جهوزها (التحميل غير متزامن). */
function i18nText(key, fallback) {
  try {
    const value = window.i18n ? window.i18n.t(key) : key;
    return value === key ? fallback : value;
  } catch {
    return fallback;
  }
}

function toggleLanguage() {
  const current = window.i18n.state.locale;
  const supported = (window.__LOCALE_CONFIG__ && window.__LOCALE_CONFIG__.supported) || ["ar", "en"];
  const index = supported.indexOf(current);
  const next = supported[(index + 1) % supported.length];
  window.i18n.setLocale(next).then(() => { renderSuggestions(); applyName(state.name); });
}

/* الاقتراحات تأتي من ملف الترجمة لا من HTML: تغيير اللغة يغيّرها فورًا */
function renderSuggestions() {
  const box = document.querySelector(".suggestions");
  if (!box || !window.i18n) return;
  const prompts = window.i18n.t("ui.suggestions");
  const labels = window.i18n.t("ui.suggestion_labels");
  if (!Array.isArray(prompts)) return;
  box.innerHTML = "";
  prompts.forEach((prompt, i) => {
    const button = document.createElement("button");
    button.className = "chip";
    button.textContent = Array.isArray(labels) ? labels[i] || prompt : prompt;
    button.onclick = () => ask(prompt);
    box.appendChild(button);
  });
  const insights = document.createElement("button");
  insights.className = "chip";
  insights.textContent = window.i18n.t("ui.insights");
  insights.onclick = () => showInsights();
  box.appendChild(insights);
}

/* الرقم داخل عزل LTR: عرضه في جملة عربية بلا عزل قد يقلبه BiDi */
function phoneHtml(number, verified) {
  const cls = verified ? "phone-number" : "phone-number unverified";
  return `<span class="${cls}" data-ltr>${number}</span>`;
}

fillSettingsForm(loadSettings());
initI18n().then(loadCountries).then(loadName);

const todayIso = () => new Date().toISOString().slice(0, 10);
document.getElementById("c-date").value = todayIso();
document.getElementById("s-date").value = todayIso();

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const panel = document.getElementById("panel");
    if (!panel.hidden) panel.hidden = true;
    else closeOverlay();
  }
});

scrollBottom();
