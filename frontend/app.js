/* CycleCare frontend — بلا مكتبات خارجية، يعمل مع FastAPI من نفس الأصل */
"use strict";

const API = ""; // نفس الأصل (يُخدم من FastAPI)

const state = {
  mode: "chat",
  loading: false,
  tab: "cycles",
  authTab: "login",
  authUser: null,
  aiConsentGranted: true,
  messages: [], // {role, text?, data?}
  metaCache: null,
  welcomeHtml: "",
  confirmResolver: null,
};

/* ---------------- مفاتيح التخزين المحلي ---------------- */

const SETTINGS_KEY = "cyclecare_context_v1";   // بيانات شخصية (تبقى في المتصفح وتُزامن مع الخادم)
const DEVICE_KEY = "cyclecare_device_v1";      // معرّف جهاز لعزل البيانات على الخادم
const THEME_KEY = "cyclecare.theme";           // المظهر (فاتح / داكن)
const ACCESS_TOKEN_KEY = "cyclecare.access_token";
const REFRESH_TOKEN_KEY = "cyclecare.refresh_token";
const AUTH_USER_KEY = "cyclecare.auth_user";
const LOCAL_PIN_KEY = "cyclecare.local_pin";
const DISGUISE_KEY = "cyclecare.disguise";
const AI_CONSENT_KEY = "cyclecare.ai_consent";

function anonymousDeviceKey() {
  let key = localStorage.getItem(DEVICE_KEY);
  if (!key) {
    key = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
    localStorage.setItem(DEVICE_KEY, key);
  }
  return key;
}

function deviceKey() {
  if (state.authUser && state.authUser.user_key) {
    return state.authUser.user_key;
  }
  return anonymousDeviceKey();
}

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
  } catch {
    return {};
  }
}

/* ---------------- نص مترجم مع احتياطي آمن ---------------- */

function i18nText(key, fallback) {
  try {
    const value = window.i18n ? window.i18n.t(key) : key;
    return value === key || typeof value !== "string" ? fallback : value;
  } catch {
    return fallback;
  }
}

function currentLocale() {
  return (window.i18n && window.i18n.state && window.i18n.state.locale) || "ar";
}

/* ---------------- المظهر (فاتح / داكن) ---------------- */

function applyTheme(theme) {
  const resolved = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", resolved);
  const btn = document.getElementById("theme-btn");
  if (btn) btn.textContent = resolved === "dark" ? "☀️" : "🌙";
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || "light";
  applyTheme(saved);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const next = current === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

/* ---------------- التبديل بين صفحة التعريف (Landing) ووضع التطبيق ---------------- */

function selectCyclePhase(phaseNum) {
  [1, 2, 3, 4].forEach((n) => {
    const btn = document.getElementById(`pbtn-${n}`);
    const panel = document.getElementById(`ppanel-${n}`);
    if (btn) btn.classList.toggle("active", n === phaseNum);
    if (panel) panel.hidden = n !== phaseNum;
  });
}

function openTrackerTab(tab) {
  const el = document.getElementById("tracker");
  if (el && el.hidden) {
    toggleDrawer(el, () => {
      setTrackerTab(tab);
      refreshTracker();
    });
  } else {
    setTrackerTab(tab);
  }
}

function showLandingSection(event, sectionId) {
  if (event) event.preventDefault();
  const wrapper = document.getElementById("landing-sections");
  const nav = document.getElementById("lp-nav");
  if (wrapper && wrapper.hidden) {
    wrapper.hidden = false;
    if (nav) nav.hidden = false;
    updateViewToggleBtn(false);
  }
  const target = document.getElementById(sectionId);
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function enterWorkspace(event) {
  if (event) event.preventDefault();
  const target = document.getElementById("workspace-section") || document.getElementById("chat");
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const input = document.getElementById("input");
  if (input) {
    setTimeout(() => input.focus({ preventScroll: true }), 320);
  }
}

function updateViewToggleBtn(appOnly) {
  const btn = document.getElementById("view-toggle-btn");
  if (!btn) return;
  btn.textContent = appOnly ? "🏠" : "💬";
  const titleKey = appOnly ? "ui.landing.toggle_view_landing" : "ui.landing.toggle_view_app";
  const fallback = appOnly
    ? i18nText("ui.landing.nav_home", "الرئيسية")
    : i18nText("ui.landing.toggle_view_app", "وضع التطبيق فقط");
  btn.setAttribute("data-i18n", titleKey);
  btn.setAttribute("title", i18nText(titleKey, fallback));
}

function toggleLandingView() {
  const wrapper = document.getElementById("landing-sections");
  const nav = document.getElementById("lp-nav");
  if (!wrapper) return;
  const nextHidden = !wrapper.hidden;
  wrapper.hidden = nextHidden;
  if (nav) nav.hidden = nextHidden;
  updateViewToggleBtn(nextHidden);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------- الإشعارات السريعة والتأكيد المدمج ---------------- */

function showToast(message, kind = "ok", undoFn = null) {
  const box = document.getElementById("toast-container");
  if (!box) return;
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  const span = document.createElement("span");
  span.textContent = message;
  item.appendChild(span);
  if (typeof undoFn === "function") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "toast-undo-btn";
    btn.textContent = i18nText("ui.tracker.undo_btn", "تراجع ↩");
    btn.onclick = async () => {
      item.remove();
      await undoFn();
    };
    item.appendChild(btn);
  }
  box.appendChild(item);
  setTimeout(() => {
    item.style.opacity = "0";
    item.style.transform = "translateY(6px)";
    item.style.transition = "all 0.2s ease";
    setTimeout(() => item.remove(), 220);
  }, undoFn ? 5000 : 2800);
}

function askConfirm(message) {
  const modal = document.getElementById("confirm-modal");
  const msgEl = document.getElementById("confirm-message");
  if (!modal || !msgEl) return Promise.resolve(window.confirm(message));
  msgEl.textContent = message;
  modal.hidden = false;
  return new Promise((resolve) => {
    state.confirmResolver = resolve;
  });
}

function resolveConfirm(answer) {
  const modal = document.getElementById("confirm-modal");
  if (modal) modal.hidden = true;
  if (state.confirmResolver) {
    const fn = state.confirmResolver;
    state.confirmResolver = null;
    fn(Boolean(answer));
  }
}

/* ---------------- البيانات الشخصية ---------------- */

function fillSettingsForm(s) {
  const goalEl = document.getElementById("f-goal");
  if (goalEl) goalEl.value = s.goal ?? "track";
  document.getElementById("f-age").value = s.age ?? "";
  document.getElementById("f-country").value = s.country_code ?? "";
  document.getElementById("f-pregnancy").value = s.pregnancy_status ?? "";
  document.getElementById("f-contraception").value = s.contraception ?? "";
  document.getElementById("f-conditions").value =
    (s.conditions || []).map((c) => `${c.name} | ${c.status}`).join("\n");
}

function readSettingsForm() {
  const defaultStatus = i18nText("ui.settings.default_condition_status", "مشخّصة");
  const conditions = document.getElementById("f-conditions").value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, status] = line.split("|").map((x) => (x || "").trim());
      return { name, status: status || defaultStatus };
    })
    .filter((c) => c.name);

  const num = (id) => {
    const v = document.getElementById(id).value;
    return v === "" ? null : Number(v);
  };
  const goalEl = document.getElementById("f-goal");

  return {
    goal: goalEl ? goalEl.value || "track" : "track",
    age: num("f-age"),
    country_code: document.getElementById("f-country").value || null,
    pregnancy_status: document.getElementById("f-pregnancy").value || null,
    contraception: document.getElementById("f-contraception").value || null,
    conditions,
  };
}

async function saveSettings() {
  const form = readSettingsForm();
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(form));
  updateFixedEmergencyButton(form.country_code);
  try {
    await api(`/v1/profile?user_key=${encodeURIComponent(deviceKey())}`, "PUT", form);
  } catch {
    /* يعمل محليًا حتى لو تعذّر الاتصال المؤقت */
  }
  toggleSettings();
  showToast(i18nText("ui.feedback.settings_saved", "تم حفظ بياناتكِ الشخصية."), "ok");
}

async function clearSettings() {
  localStorage.removeItem(SETTINGS_KEY);
  fillSettingsForm({});
  updateFixedEmergencyButton(null);
  try {
    await api(`/v1/profile?user_key=${encodeURIComponent(deviceKey())}`, "PUT", {
      age: null,
      pregnancy_status: null,
      contraception: null,
      conditions: [],
    });
  } catch {
    /* تجاهل خطأ الشبكة عند المسح المحلي */
  }
  showToast(i18nText("ui.feedback.settings_cleared", "تم مسح البيانات الشخصية."), "ok");
}

function toggleDrawer(el, onOpen) {
  if (!el) return;
  if (el.hidden) {
    el.classList.remove("is-closing");
    el.hidden = false;
    if (onOpen) onOpen();
  } else {
    el.classList.add("is-closing");
    setTimeout(() => {
      el.hidden = true;
      el.classList.remove("is-closing");
    }, 190);
  }
}

function toggleSettings() {
  toggleDrawer(document.getElementById("settings"));
}

/* ---------------- البلد ورقم الطوارئ ---------------- */

function formatCountryName(country) {
  const loc = currentLocale();
  if (loc.indexOf("en") === 0 && typeof Intl !== "undefined" && Intl.DisplayNames) {
    try {
      const dn = new Intl.DisplayNames(["en"], { type: "region" });
      return dn.of(country.code) || country.name_ar;
    } catch {
      return country.name_ar;
    }
  }
  return country.name_ar;
}

async function loadCountries() {
  const select = document.getElementById("f-country");
  try {
    const meta = state.metaCache || await api("/v1/meta", "GET");
    state.metaCache = meta;
    const saved = loadSettings().country_code;
    const currentVal = select.value || saved;
    select.innerHTML = `<option value="">${i18nText("ui.country_none", "غير محدد")}</option>` +
      (meta.countries || []).map((c) =>
        `<option value="${c.code}"${c.code === currentVal ? " selected" : ""}>`
        + `${formatCountryName(c)} — ${c.emergency}</option>`
      ).join("");
    if (!currentVal && meta.default_country) select.value = meta.default_country;
    updateFixedEmergencyButton(select.value);
    select.onchange = () => updateFixedEmergencyButton(select.value);
  } catch {
    select.innerHTML = `<option value="">${i18nText("ui.country_unavailable", "غير متاح")}</option>`;
  }
}

function updateFixedEmergencyButton(countryCode) {
  const btn = document.getElementById("fixed-sos-btn");
  const numEl = document.getElementById("fixed-emergency-num");
  if (!btn || !numEl) return;
  const meta = state.metaCache || {};
  const countries = meta.countries || [];
  const code = (countryCode || loadSettings().country_code || meta.default_country || "EG").toUpperCase();
  const found = countries.find((c) => c.code === code);
  const num = (found && found.emergency) || meta.emergency_number || "123";
  numEl.textContent = num;
  btn.setAttribute("href", `tel:${num}`);
}

/* ---------------- الوضع ---------------- */

function updateInputPlaceholder() {
  const input = document.getElementById("input");
  if (!input) return;
  const key = state.mode === "summary" ? "ui.placeholders.summary" : "ui.placeholders.chat";
  const fallback = state.mode === "summary"
    ? i18nText("ui.placeholders.summary", "اكتبي ملاحظة للملخص أو اتركيه فارغًا…")
    : i18nText("ui.placeholders.chat", "اكتبي سؤالك هنا…");
  input.setAttribute("data-i18n", key);
  input.placeholder = fallback;
}

function setMode(mode) {
  state.mode = mode;
  const chatBtn = document.getElementById("mode-chat");
  const sumBtn = document.getElementById("mode-summary");
  if (chatBtn) {
    chatBtn.classList.toggle("active", mode === "chat");
    chatBtn.setAttribute("aria-selected", String(mode === "chat"));
  }
  if (sumBtn) {
    sumBtn.classList.toggle("active", mode === "summary");
    sumBtn.setAttribute("aria-selected", String(mode === "summary"));
  }
  updateInputPlaceholder();
}

function autoResizeInput(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 135)}px`;
}

/* ---------------- المحادثة ---------------- */

const chatEl = () => document.getElementById("chat");

function hideWelcome() {
  const w = document.getElementById("welcome");
  if (w) w.remove();
}

function resetChat() {
  state.messages = [];
  setMode("chat");
  const chat = chatEl();
  if (!chat) return;
  chat.innerHTML = state.welcomeHtml;
  if (window.i18n) {
    window.i18n.load(currentLocale()).then(renderSuggestions);
  }
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
  if (state.mode === "summary" && data.overview !== undefined) {
    container.classList.add("summary-card");
    const sections = [
      [i18nText("ui.summary_sections.overview", "نظرة عامة"), data.overview],
      [i18nText("ui.summary_sections.what_changed", "ما الذي تغيّر"), data.what_changed],
      [i18nText("ui.summary_sections.patterns", "الأنماط المرصودة"), data.patterns],
      [i18nText("ui.summary_sections.medical_alerts", "تنبيهات"), data.medical_alerts],
      [i18nText("ui.summary_sections.what_this_does_not_mean", "ما لا يعنيه هذا"), data.what_this_does_not_mean],
    ];
    sections.forEach(([title, text]) => {
      if (!text) return;
      const box = document.createElement("div");
      box.className = "summary-section";
      const h = document.createElement("h4");
      h.textContent = title;
      const p = document.createElement("div");
      p.textContent = text;
      box.append(h, p);
      container.appendChild(box);
    });
    if ((data.questions_for_doctor || []).length) {
      const box = document.createElement("div");
      box.className = "summary-section";
      const h = document.createElement("h4");
      h.textContent = i18nText("ui.summary_sections.questions_for_doctor", "أسئلة لطبيبتِك");
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

  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = data.answer || "";
  container.appendChild(body);
  appendMeta(container, data);
  appendCopyButton(container, data.answer || "");
}

function appendCopyButton(container, text) {
  if (!text || !navigator.clipboard) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copy-btn";
  btn.textContent = i18nText("ui.copy_reply", "نسخ");
  btn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = i18nText("ui.copied_reply", "تم النسخ");
      setTimeout(() => {
        btn.textContent = i18nText("ui.copy_reply", "نسخ");
      }, 1600);
    } catch {
      /* ignore clipboard error */
    }
  };
  container.appendChild(btn);
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
  const rules = data.rule_codes || [];
  const sources = data.sources_used || [];
  if (sources.length) {
    add("source", window.i18n
      ? window.i18n.tn("units.sources_count", sources.length)
      : String(sources.length));
  }
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
  const isSummaryPrompt = /\u0645\u0644\u062e\u0635|summar/i.test(text);
  if (isSummaryPrompt && state.mode !== "summary") {
    setMode("summary");
  } else if (!isSummaryPrompt && state.mode === "summary") {
    setMode("chat");
  }
}

async function api(path, method = "GET", body = null, _retried = false) {
  const opts = {
    method,
    headers: {
      "Accept-Language": currentLocale(),
    },
  };
  const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
  if (accessToken) {
    opts.headers["Authorization"] = `Bearer ${accessToken}`;
  }
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${API}${path}`, opts);
  if (resp.status === 401 && !_retried && !path.startsWith("/v1/auth/")) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      return api(path, method, body, true);
    }
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const detail = typeof err.detail === "string"
      ? err.detail
      : (err.detail && err.detail.message) || `HTTP ${resp.status}`;
    throw new Error(detail);
  }
  return resp.json();
}

async function tryRefreshToken() {
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) return false;
  try {
    const resp = await fetch(`${API}/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) {
      clearAuthTokens();
      return false;
    }
    const data = await resp.json();
    localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
    if (data.user) {
      state.authUser = data.user;
      localStorage.setItem(AUTH_USER_KEY, JSON.stringify(data.user));
    }
    return true;
  } catch {
    return false;
  }
}

async function send(text) {
  state.loading = true;
  document.getElementById("send-btn").disabled = true;
  document.getElementById("typing").hidden = false;
  scrollBottom();

  try {
    const settings = readSettingsForm();
    const historyPayload = (state.messages || []).slice(-6).map((m) => ({
      role: m.role === "user" ? "user" : "assistant",
      content: m.role === "user" ? (m.text || "") : ((m.data && (m.data.answer || m.data.overview)) || ""),
    })).filter((m) => m.content);
    const data = await api("/v1/chat", "POST", {
      message: text,
      mode: state.mode,
      user_key: deviceKey(),
      country_code: settings.country_code,
      user_context: settings,
      history: historyPayload,
    });
    state.messages.push({ role: "user", text }, { role: "bot", data });

    if (data.emergency) {
      showOverlay(data);
      addMessage("bot", data.answer);
    } else {
      addMessage("bot", data);
    }
  } catch (e) {
    const prefix = i18nText("ui.server_error", "تعذّر الاتصال بالخادم");
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
  let text = input.value.trim();
  if (!text && state.mode === "summary") {
    const prompts = window.i18n ? window.i18n.t("ui.suggestions") : null;
    text = (Array.isArray(prompts) && prompts[3]) || "Summary";
  }
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

/* ---------------- عناصر الإدخال السريع في التتبّع ---------------- */

function renderQuickSymptoms() {
  const box = document.getElementById("quick-symptoms");
  if (!box || !window.i18n) return;
  const list = window.i18n.t("ui.tracker.quick_symptoms");
  if (!Array.isArray(list)) return;
  box.innerHTML = "";
  const input = document.getElementById("s-name");
  list.forEach((label) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick-chip";
    btn.textContent = label;
    btn.onclick = () => {
      if (!input) return;
      input.value = label;
      box.querySelectorAll(".quick-chip").forEach((c) => c.classList.toggle("active", c === btn));
      input.focus();
    };
    box.appendChild(btn);
  });
}

function selectSeverity(level) {
  const input = document.getElementById("s-severity");
  if (!input) return;
  const nextVal = String(input.value) === String(level) ? "" : String(level);
  input.value = nextVal;
  document.querySelectorAll("#severity-pills .sev-pill").forEach((pill) => {
    const active = pill.getAttribute("data-level") === nextVal;
    pill.classList.toggle("active", active);
    pill.setAttribute("aria-checked", String(active));
  });
}

function renderSeverityPills() {
  const box = document.getElementById("severity-pills");
  if (!box || !window.i18n) return;
  const levels = window.i18n.t("ui.tracker.severity_levels");
  if (!Array.isArray(levels)) return;
  const current = document.getElementById("s-severity")?.value || "";
  box.innerHTML = "";
  levels.forEach((label, idx) => {
    const level = String(idx + 1);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `sev-pill${current === level ? " active" : ""}`;
    btn.setAttribute("data-level", level);
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", String(current === level));
    btn.textContent = label;
    btn.onclick = () => selectSeverity(level);
    box.appendChild(btn);
  });
}

/* ---------------- لوحة التتبّع ---------------- */

function toggleTracker() {
  toggleDrawer(document.getElementById("tracker"), refreshTracker);
}

function setTrackerTab(tab) {
  state.tab = tab;
  ["cycles", "symptoms", "insights"].forEach((t) => {
    const btn = document.getElementById(`tab-${t}`);
    const pane = document.getElementById(`pane-${t}`);
    if (btn) {
      btn.classList.toggle("active", t === tab);
      btn.setAttribute("aria-selected", String(t === tab));
    }
    if (pane) pane.hidden = t !== tab;
  });
  if (tab === "insights") refreshInsights();
}

async function refreshTracker() {
  await Promise.all([loadCycles(), loadSymptoms()]);
  if (state.tab === "insights") await refreshInsights();
}

function fmtDate(iso) {
  try {
    const loc = currentLocale().indexOf("en") === 0 ? "en-GB" : "ar-EG";
    return new Date(iso + "T00:00:00").toLocaleDateString(loc,
      { year: "numeric", month: "long", day: "numeric" });
  } catch {
    return iso;
  }
}

function updateCycleKpis(rows) {
  const countEl = document.getElementById("kpi-cycles-val");
  const avgEl = document.getElementById("kpi-avg-val");
  const lastEl = document.getElementById("kpi-last-val");
  const badgeEl = document.getElementById("tracker-badge");
  const ringCircle = document.getElementById("cycle-ring-progress");
  const ringNum = document.getElementById("ring-day-num");
  const ringCaption = document.getElementById("ring-day-caption");
  const chartCard = document.getElementById("cycle-chart-card");
  const chartBars = document.getElementById("cycle-bars");
  const noneText = i18nText("ui.tracker.kpi_none", "—");
  const circumference = 301.59;

  if (countEl) countEl.textContent = String(rows.length);
  if (badgeEl) {
    badgeEl.textContent = String(rows.length);
    badgeEl.hidden = rows.length === 0;
  }
  if (!rows.length) {
    if (avgEl) avgEl.textContent = noneText;
    if (lastEl) lastEl.textContent = noneText;
    if (ringCircle) ringCircle.style.strokeDashoffset = String(circumference);
    if (ringNum) ringNum.textContent = noneText;
    if (ringCaption) ringCaption.textContent = i18nText("ui.tracker.ring_empty_label", "سجّلي أول دورة");
    if (chartCard) chartCard.hidden = true;
    return;
  }

  const sorted = [...rows].sort((a, b) => a.start_date.localeCompare(b.start_date));
  const latest = sorted[sorted.length - 1];
  if (lastEl) lastEl.textContent = fmtDate(latest.start_date);

  const gaps = [];
  for (let i = 1; i < sorted.length; i++) {
    const d1 = new Date(sorted[i - 1].start_date + "T00:00:00");
    const d2 = new Date(sorted[i].start_date + "T00:00:00");
    const diff = Math.round((d2 - d1) / 86400000);
    if (diff > 0 && diff <= 400) {
      gaps.push({ days: diff, label: sorted[i].start_date.slice(5) });
    }
  }

  const avgDays = gaps.length
    ? Math.round(gaps.reduce((a, b) => a + b.days, 0) / gaps.length)
    : 28;

  if (avgEl) {
    if (gaps.length) {
      avgEl.textContent = window.i18n ? window.i18n.tn("units.days", avgDays) : `${avgDays}`;
    } else {
      avgEl.textContent = noneText;
    }
  }

  const today = new Date(todayIso() + "T00:00:00");
  const lastDate = new Date(latest.start_date + "T00:00:00");
  const elapsed = Math.max(1, Math.round((today - lastDate) / 86400000) + 1);
  const ratio = Math.min(1, elapsed / Math.max(21, avgDays));
  if (ringCircle) {
    ringCircle.style.strokeDashoffset = String(Math.round(circumference * (1 - ratio)));
  }
  if (ringNum) ringNum.textContent = String(elapsed);
  if (ringCaption) ringCaption.textContent = i18nText("ui.tracker.ring_day_label", "اليوم منذ آخر بداية");

  if (chartCard && chartBars) {
    if (!gaps.length) {
      chartCard.hidden = true;
    } else {
      chartCard.hidden = false;
      chartBars.innerHTML = "";
      const recentGaps = gaps.slice(-6);
      const maxVal = Math.max(35, ...recentGaps.map((g) => g.days));
      recentGaps.forEach((g) => {
        const col = document.createElement("div");
        col.className = "cycle-bar-col";
        const val = document.createElement("span");
        val.className = "cycle-bar-val ltr-isolate";
        val.textContent = String(g.days);
        const fill = document.createElement("div");
        fill.className = "cycle-bar-fill";
        fill.style.height = `${Math.max(18, Math.round((g.days / maxVal) * 56))}px`;
        const dt = document.createElement("span");
        dt.className = "cycle-bar-date ltr-isolate";
        dt.textContent = g.label;
        col.append(val, fill, dt);
        chartBars.appendChild(col);
      });
    }
  }
}

async function loadPrediction() {
  const insuffEl = document.getElementById("prediction-insufficient-msg");
  const detailsEl = document.getElementById("prediction-details");
  const badgeEl = document.getElementById("prediction-confidence-badge");
  const winEl = document.getElementById("prediction-window-val");
  const basisEl = document.getElementById("prediction-basis-val");
  if (!insuffEl || !detailsEl) return;
  try {
    const pred = await api(`/v1/predictions?user_key=${encodeURIComponent(deviceKey())}`);
    if ((pred.status !== "ok" && pred.status !== "predicted") || !pred.window_start || !pred.window_end) {
      insuffEl.hidden = false;
      detailsEl.hidden = true;
      if (badgeEl) badgeEl.hidden = true;
      return;
    }
    insuffEl.hidden = true;
    detailsEl.hidden = false;
    winEl.textContent = `${fmtDate(pred.window_start)} – ${fmtDate(pred.window_end)}`;
    if (badgeEl) {
      badgeEl.hidden = false;
      const confMap = {
        high: i18nText("ui.tracker.prediction_confidence_high", "درجة الثقة: عالية"),
        medium: i18nText("ui.tracker.prediction_confidence_medium", "درجة الثقة: متوسطة"),
        low: i18nText("ui.tracker.prediction_confidence_low", "درجة الثقة: منخفضة"),
      };
      badgeEl.textContent = confMap[pred.confidence] || confMap.low;
    }
    if (basisEl && pred.basis) {
      const b = pred.basis;
      const cycUnit = window.i18n ? window.i18n.tn("units.cycles", b.cycles_used) : `${b.cycles_used}`;
      const dayUnit = window.i18n ? window.i18n.tn("units.days", Math.round(b.weighted_mean_days)) : `${b.weighted_mean_days}`;
      basisEl.textContent = `${cycUnit} · ${dayUnit} (±${b.std_dev_days})`;
    }
  } catch (_) {
    // non-fatal
  }
}

async function correctCycleStartFromInput() {
  const start = document.getElementById("c-date").value;
  const len = document.getElementById("c-length").value;
  if (!start) {
    showToast(i18nText("ui.feedback.select_cycle_date", "اختاري تاريخ أول يوم نزيف."), "danger");
    return;
  }
  try {
    await api(`/v1/predictions/correct?user_key=${encodeURIComponent(deviceKey())}`, "POST", {
      actual_start_date: start,
      length_days: len === "" ? null : Number(len),
    });
    await loadCycles();
    showToast(i18nText("ui.feedback.cycle_added", "تمت إضافة الدورة بنجاح."), "ok");
  } catch (e) {
    showToast(e.message, "danger");
  }
}

async function copyYesterdayLog() {
  try {
    await api(`/v1/daily-logs/copy-yesterday?user_key=${encodeURIComponent(deviceKey())}`, "POST", {});
    await loadSymptoms();
    showToast(i18nText("ui.tracker.copied_yesterday", "تم نسخ سجل أمس وأعراضه إلى اليوم."), "ok");
  } catch (e) {
    showToast(e.message, "danger");
  }
}

async function exportFullJson() {
  try {
    const data = await api(`/v1/export/full.json?user_key=${encodeURIComponent(deviceKey())}`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cyclecare-backup-${todayIso()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast(i18nText("ui.feedback.export_success", "تم تجهيز ملف التصدير."), "ok");
  } catch (e) {
    showToast(e.message, "danger");
  }
}

async function loadCycles() {
  const list = document.getElementById("cycles-list");
  try {
    const rows = await api(`/v1/cycles?user_key=${encodeURIComponent(deviceKey())}`);
    updateCycleKpis(rows);
    loadPrediction();
    document.getElementById("cycles-count").textContent =
      rows.length && window.i18n ? window.i18n.tn("units.cycles", rows.length) : "";
    if (!rows.length) {
      list.innerHTML = `<li class="muted">${i18nText("ui.tracker.empty_records", "لا يوجد تسجيل بعد.")}</li>`;
      return;
    }
    list.innerHTML = "";
    const bleedSuffix = i18nText("ui.tracker.bleeding_days_suffix", "يوم نزيف");
    const delText = i18nText("ui.tracker.delete_btn", "حذف");
    rows.forEach((c) => {
      const li = document.createElement("li");
      const label = c.length_days
        ? `${fmtDate(c.start_date)} · ${c.length_days} ${bleedSuffix}`
        : fmtDate(c.start_date);
      const span = document.createElement("span");
      span.textContent = label;
      const btn = document.createElement("button");
      btn.className = "link-danger";
      btn.textContent = delText;
      btn.onclick = () => removeCycle(c);
      li.append(span, btn);
      list.appendChild(li);
    });
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.load_error", "تعذّر تحميل البيانات");
    list.innerHTML = `<li class="muted">${errPrefix}: ${e.message}</li>`;
  }
}

async function addCycle() {
  const start = document.getElementById("c-date").value;
  const len = document.getElementById("c-length").value;
  if (!start) {
    showToast(i18nText("ui.feedback.select_cycle_date", "اختاري تاريخ أول يوم نزيف."), "danger");
    return;
  }
  try {
    await api(`/v1/cycles?user_key=${encodeURIComponent(deviceKey())}`, "POST", {
      start_date: start,
      length_days: len === "" ? null : Number(len),
    });
    document.getElementById("c-date").value = todayIso();
    document.getElementById("c-length").value = "";
    await loadCycles();
    showToast(i18nText("ui.feedback.cycle_added", "تمت إضافة الدورة بنجاح."), "ok");
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

async function removeCycle(cycleOrId) {
  const id = typeof cycleOrId === "object" ? cycleOrId.id : cycleOrId;
  const backup = typeof cycleOrId === "object" ? cycleOrId : null;
  if (!backup) {
    const ok = await askConfirm(i18nText("ui.feedback.confirm_delete_cycle", "هل تريدين حذف هذه الدورة؟"));
    if (!ok) return;
  }
  try {
    await api(`/v1/cycles/${id}?user_key=${encodeURIComponent(deviceKey())}`, "DELETE");
    await loadCycles();
    showToast(
      i18nText("ui.feedback.cycle_deleted", "تم حذف الدورة."),
      "ok",
      backup
        ? async () => {
            await api(`/v1/cycles?user_key=${encodeURIComponent(deviceKey())}`, "POST", {
              start_date: backup.start_date,
              length_days: backup.length_days ?? null,
            });
            await loadCycles();
            showToast(i18nText("ui.tracker.undo_restored", "تمت استعادة السجل بنجاح."), "ok");
          }
        : null
    );
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

async function loadSymptoms() {
  const list = document.getElementById("symptoms-list");
  try {
    const rows = await api(`/v1/symptoms?user_key=${encodeURIComponent(deviceKey())}`);
    document.getElementById("symptoms-count").textContent =
      rows.length ? `${rows.length}` : "";
    if (!rows.length) {
      list.innerHTML = `<li class="muted">${i18nText("ui.tracker.empty_records", "لا يوجد تسجيل بعد.")}</li>`;
      return;
    }
    list.innerHTML = "";
    const sevTag = i18nText("ui.tracker.severity_tag", "شدّة");
    const delText = i18nText("ui.tracker.delete_btn", "حذف");
    rows.forEach((s) => {
      const li = document.createElement("li");
      const sev = s.severity ? ` · ${sevTag} ${s.severity}/5` : "";
      const note = s.note ? ` · ${s.note}` : "";
      const span = document.createElement("span");
      span.textContent = `${fmtDate(s.log_date)} — ${s.symptom}${sev}${note}`;
      const btn = document.createElement("button");
      btn.className = "link-danger";
      btn.textContent = delText;
      btn.onclick = () => removeSymptom(s);
      li.append(span, btn);
      list.appendChild(li);
    });
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.load_error", "تعذّر تحميل البيانات");
    list.innerHTML = `<li class="muted">${errPrefix}: ${e.message}</li>`;
  }
}

async function addSymptom() {
  const day = document.getElementById("s-date").value;
  const name = document.getElementById("s-name").value.trim();
  const sev = document.getElementById("s-severity").value;
  const note = document.getElementById("s-note").value.trim();
  if (!day) {
    showToast(i18nText("ui.feedback.select_symptom_date", "اختاري التاريخ."), "danger");
    return;
  }
  if (!name) {
    showToast(i18nText("ui.feedback.enter_symptom_name", "اكتبي اسم العرض."), "danger");
    return;
  }
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
    document.querySelectorAll("#quick-symptoms .quick-chip").forEach((c) => c.classList.remove("active"));
    renderSeverityPills();
    await loadSymptoms();
    showToast(i18nText("ui.feedback.symptom_added", "تمت إضافة العرض بنجاح."), "ok");
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

async function removeSymptom(symOrId) {
  const id = typeof symOrId === "object" ? symOrId.id : symOrId;
  const backup = typeof symOrId === "object" ? symOrId : null;
  if (!backup) {
    const ok = await askConfirm(i18nText("ui.feedback.confirm_delete_symptom", "هل تريدين حذف هذا السجل؟"));
    if (!ok) return;
  }
  try {
    await api(`/v1/symptoms/${id}?user_key=${encodeURIComponent(deviceKey())}`, "DELETE");
    await loadSymptoms();
    showToast(
      i18nText("ui.feedback.symptom_deleted", "تم حذف السجل."),
      "ok",
      backup
        ? async () => {
            await api(`/v1/symptoms?user_key=${encodeURIComponent(deviceKey())}`, "POST", {
              log_date: backup.log_date,
              symptom: backup.symptom,
              severity: backup.severity ?? null,
              note: backup.note ?? null,
            });
            await loadSymptoms();
            showToast(i18nText("ui.tracker.undo_restored", "تمت استعادة السجل بنجاح."), "ok");
          }
        : null
    );
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

async function refreshInsights() {
  const box = document.getElementById("insights-body");
  box.innerHTML = `<p class="muted">${i18nText("ui.tracker.insights_loading", "جارٍ التحليل…")}</p>`;
  try {
    const data = await api(`/v1/insights?user_key=${encodeURIComponent(deviceKey())}`);
    box.innerHTML = "";

    const summary = document.createElement("p");
    summary.className = "muted";
    if (data.cycles_recorded) {
      const cycText = window.i18n
        ? window.i18n.tn("units.cycles", data.cycles_recorded)
        : String(data.cycles_recorded);
      const avgText = data.avg_cycle_days && window.i18n
        ? ` · ${i18nText("ui.tracker.kpi_avg", "متوسط الطول")}: ${window.i18n.tn("units.days", data.avg_cycle_days)}`
        : "";
      summary.textContent = `${cycText}${avgText}`;
    } else {
      summary.textContent = i18nText("ui.tracker.insights_no_cycles", "لا توجد دورات مسجّلة بعد؛ سجّلي دورة على الأقل ليبدأ التحليل.");
    }
    box.appendChild(summary);

    if (data.needs_doctor) {
      const note = document.createElement("div");
      note.className = "insight-alert";
      note.textContent = i18nText("ui.tracker.insights_doctor_alert", "في بياناتكِ ما يستحق مراجعة طبية. هذا ليس تشخيصًا.");
      box.appendChild(note);
    }

    const evPrefix = i18nText("ui.tracker.insights_evidence_prefix", "الأرقام:");
    (data.findings || []).forEach((f) => {
      const card = document.createElement("div");
      card.className = "insight-card";
      const h = document.createElement("h4");
      h.textContent = `${f.title} — ${f.rule_code}`;
      const p = document.createElement("p");
      p.textContent = data.glossary?.[f.rule_code] || "";
      const ev = document.createElement("small");
      ev.textContent = `${evPrefix} ${(f.evidence || []).join(" · ")}`;
      card.append(h, p, ev);
      box.appendChild(card);
    });

    if (!(data.findings || []).length) {
      box.innerHTML += `<p class="muted">${i18nText("ui.tracker.insights_empty", "لا نتائج بعد.")}</p>`;
    }
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.load_error", "تعذّر تحميل البيانات");
    box.innerHTML = `<p class="muted">${errPrefix}: ${e.message}</p>`;
  }
}

function showInsights() {
  const el = document.getElementById("tracker");
  el.hidden = false;
  setTrackerTab("insights");
}

async function exportCsvData() {
  try {
    const [cycles, symptoms] = await Promise.all([
      api(`/v1/cycles?user_key=${encodeURIComponent(deviceKey())}`),
      api(`/v1/symptoms?user_key=${encodeURIComponent(deviceKey())}`),
    ]);
    const hStart = i18nText("csv.cycle_start", "بداية الدورة");
    const hBleed = i18nText("csv.bleeding_days", "أيام النزيف");
    const hDate = i18nText("csv.log_date", "التاريخ");
    const hSym = i18nText("csv.symptom", "العرض");
    const hSev = i18nText("csv.severity", "الشدة");
    const hNote = i18nText("csv.note", "ملاحظة");

    const lines = [
      `${hStart},${hBleed}`,
      ...cycles.map((c) => `${c.start_date},${c.length_days ?? ""}`),
      "",
      `${hDate},${hSym},${hSev},${hNote}`,
      ...symptoms.map((s) => `${s.log_date},"${(s.symptom || "").replace(/"/g, '""')}",${s.severity ?? ""},"${(s.note || "").replace(/"/g, '""')}"`),
    ];
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `cyclecare-${todayIso()}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast(i18nText("ui.feedback.csv_exported", "تم تصدير السجل بنجاح."), "ok");
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

async function exportPdfReport() {
  try {
    const loc = encodeURIComponent(currentLocale());
    const ukey = encodeURIComponent(deviceKey());
    const resp = await fetch(`${API}/v1/export/report.pdf?user_key=${ukey}&locale=${loc}`, {
      headers: { "Accept-Language": currentLocale() },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `cyclecare-report-${todayIso()}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast(i18nText("ui.feedback.csv_exported", "تم تصدير السجل بنجاح."), "ok");
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

async function deleteAllData() {
  const ok = await askConfirm(i18nText("ui.feedback.confirm_delete_all", "سيُحذف كل ما سجّلتِه من دورات وأعراض. هل أنتِ متأكدة؟"));
  if (!ok) return;
  try {
    await api(`/v1/data?user_key=${encodeURIComponent(deviceKey())}`, "DELETE");
    await refreshTracker();
    showToast(i18nText("ui.feedback.all_deleted", "تم حذف كل بياناتك."), "ok");
  } catch (e) {
    const errPrefix = i18nText("ui.feedback.action_error", "تعذّر تنفيذ الإجراء");
    showToast(`${errPrefix}: ${e.message}`, "danger");
  }
}

/* ---------------- التعريب والتهيئة ---------------- */

function renderAllLocalizedWidgets() {
  renderSuggestions();
  renderQuickSymptoms();
  renderSeverityPills();
  updateInputPlaceholder();
  loadCountries();
  const tracker = document.getElementById("tracker");
  if (tracker && !tracker.hidden) {
    refreshTracker();
  }
}

async function initI18n() {
  try {
    const meta = await api("/v1/meta", "GET");
    state.metaCache = meta;
    window.__LOCALE_CONFIG__ = meta.locale || {};
  } catch {
    window.__LOCALE_CONFIG__ = { default: "ar", supported: ["ar", "en"] };
  }
  await window.i18n.load();
  renderAllLocalizedWidgets();
}

function toggleLanguage() {
  const current = window.i18n.state.locale;
  const supported = (window.__LOCALE_CONFIG__ && window.__LOCALE_CONFIG__.supported) || ["ar", "en"];
  const index = supported.indexOf(current);
  const next = supported[(index + 1) % supported.length];
  window.i18n.setLocale(next).then(renderAllLocalizedWidgets);
}

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

/* ---------------- المرحلة 1: مصادقة الحسابات، حفظ البيانات، قفل PIN، التمويه، والتنقل السفلي ---------------- */

function toggleAuthPanel() {
  toggleDrawer(document.getElementById("auth-panel"), renderAuthState);
}

function setAuthTab(mode) {
  state.authTab = mode === "register" ? "register" : "login";
  const loginBtn = document.getElementById("auth-tab-login");
  const regBtn = document.getElementById("auth-tab-register");
  const dnameWrap = document.getElementById("auth-display-name-wrap");
  const submitBtn = document.getElementById("auth-submit-btn");

  if (loginBtn) loginBtn.classList.toggle("active", state.authTab === "login");
  if (regBtn) regBtn.classList.toggle("active", state.authTab === "register");
  if (dnameWrap) dnameWrap.hidden = state.authTab !== "register";
  if (submitBtn) {
    const key = state.authTab === "register" ? "ui.auth.register_btn" : "ui.auth.login_btn";
    submitBtn.setAttribute("data-i18n", key);
    submitBtn.textContent = state.authTab === "register"
      ? i18nText("ui.auth.register_btn", "إنشاء الحساب وحفظ بياناتي")
      : i18nText("ui.auth.login_btn", "دخول إلى حسابي");
  }
}

function clearAuthTokens() {
  state.authUser = null;
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
  renderAuthState();
}

function applyProfileToLocal(profile) {
  if (!profile) return;
  const merged = {
    age: profile.age ?? null,
    country_code: profile.country_code || loadSettings().country_code || "EG",
    pregnancy_status: profile.pregnancy_status ?? null,
    contraception: profile.contraception ?? null,
    conditions: Array.isArray(profile.conditions) ? profile.conditions : [],
  };
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged));
  fillSettingsForm(merged);
  updateFixedEmergencyButton(merged.country_code);
}

function renderAuthState() {
  const btn = document.getElementById("auth-btn");
  const label = document.getElementById("auth-btn-label");
  const guestView = document.getElementById("auth-guest-view");
  const userView = document.getElementById("auth-user-view");
  const userDisplay = document.getElementById("auth-user-display");
  const userEmail = document.getElementById("auth-user-email");
  const userAvatar = document.getElementById("auth-user-avatar");

  if (state.authUser && state.authUser.username) {
    if (btn) btn.classList.add("authenticated");
    if (label) {
      label.removeAttribute("data-i18n");
      label.textContent = state.authUser.display_name || state.authUser.username;
    }
    if (guestView) guestView.hidden = true;
    if (userView) userView.hidden = false;
    if (userDisplay) userDisplay.textContent = state.authUser.display_name || state.authUser.username;
    if (userEmail) userEmail.textContent = state.authUser.username;
    if (userAvatar) {
      userAvatar.textContent = (state.authUser.display_name || state.authUser.username || "U")
        .trim()
        .charAt(0)
        .toUpperCase();
    }
  } else {
    if (btn) btn.classList.remove("authenticated");
    if (label) {
      label.setAttribute("data-i18n", "ui.auth.btn_guest");
      label.textContent = i18nText("ui.auth.btn_guest", "حسابي / دخول");
    }
    if (guestView) guestView.hidden = false;
    if (userView) userView.hidden = true;
  }
}

async function submitAuthForm(event) {
  if (event) event.preventDefault();
  const username = (document.getElementById("auth-username")?.value || "").trim();
  const password = document.getElementById("auth-password")?.value || "";
  const displayName = (document.getElementById("auth-display-name")?.value || "").trim();
  const migrate = Boolean(document.getElementById("auth-migrate-check")?.checked);

  if (username.length < 3 || password.length < 6) {
    showToast(i18nText("ui.auth.auth_error", "تحققي من اسم المستخدمة وكلمة المرور (6 أحرف فأكثر)."), "danger");
    return false;
  }

  const endpoint = state.authTab === "register" ? "/v1/auth/register" : "/v1/auth/login";
  const payload = {
    username,
    password,
    migrate_user_key: migrate ? anonymousDeviceKey() : null,
  };
  if (state.authTab === "register" && displayName) {
    payload.display_name = displayName;
  }

  try {
    const res = await api(endpoint, "POST", payload);
    localStorage.setItem(ACCESS_TOKEN_KEY, res.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, res.refresh_token);
    state.authUser = res.user;
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(res.user));

    if (res.profile) {
      applyProfileToLocal(res.profile);
    }
    if (res.user && typeof res.user.disguise_mode === "boolean") {
      applyDisguiseMode(res.user.disguise_mode);
    }

    renderAuthState();
    await refreshTracker();

    const toastText = state.authTab === "register"
      ? i18nText("ui.auth.register_success", "تم إنشاء حسابكِ وحفظ بياناتكِ بنجاح.")
      : i18nText("ui.auth.login_success", "تم تسجيل الدخول واستعادة بياناتكِ المحفوظة بنجاح.");
    showToast(toastText, "ok");
    document.getElementById("auth-password").value = "";
  } catch (e) {
    const errPrefix = i18nText("ui.auth.auth_error", "تحققي من اسم المستخدمة وكلمة المرور (6 أحرف فأكثر).");
    showToast(`${errPrefix} (${e.message})`, "danger");
  }
  return false;
}

async function logoutAccount() {
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  try {
    if (refreshToken) {
      await api("/v1/auth/logout", "POST", { refresh_token: refreshToken });
    }
  } catch {
    /* تجاهل الخطأ عند الخروج */
  }
  clearAuthTokens();
  await refreshTracker();
  showToast(i18nText("ui.auth.logout_success", "تم تسجيل الخروج. أنتِ الآن في الوضع المحلي."), "ok");
}

async function restoreAuthSession() {
  try {
    const cachedUser = localStorage.getItem(AUTH_USER_KEY);
    if (cachedUser) {
      state.authUser = JSON.parse(cachedUser);
      renderAuthState();
    }
  } catch {
    state.authUser = null;
  }

  const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
  if (accessToken) {
    try {
      const me = await api("/v1/auth/me", "GET");
      if (me && me.user) {
        state.authUser = me.user;
        localStorage.setItem(AUTH_USER_KEY, JSON.stringify(me.user));
        if (me.profile) applyProfileToLocal(me.profile);
        if (typeof me.user.disguise_mode === "boolean") {
          applyDisguiseMode(me.user.disguise_mode);
        }
        renderAuthState();
      }
    } catch {
      clearAuthTokens();
    }
  } else {
    try {
      const prof = await api(`/v1/profile?user_key=${encodeURIComponent(deviceKey())}`, "GET");
      if (prof && (prof.age || prof.contraception || prof.pregnancy_status)) {
        applyProfileToLocal(prof);
      }
    } catch {
      /* وضع محلي بحت */
    }
  }
}

async function savePinSettings() {
  const pinInput = document.getElementById("pin-input");
  const pin = (pinInput?.value || "").trim();
  if (!/^\d{4,6}$/.test(pin)) {
    showToast(i18nText("ui.auth.pin_invalid", "رمز PIN غير صحيح."), "danger");
    return;
  }
  localStorage.setItem(LOCAL_PIN_KEY, pin);
  if (state.authUser) {
    try {
      const res = await api("/v1/auth/pin", "POST", { pin });
      if (res.user) state.authUser = res.user;
    } catch {
      /* محفوظ محليًا أيضًا */
    }
  }
  if (pinInput) pinInput.value = "";
  showToast(i18nText("ui.auth.pin_saved", "تم تحديث إعدادات القفل والتمويه."), "ok");
}

async function clearPinSettings() {
  localStorage.removeItem(LOCAL_PIN_KEY);
  if (state.authUser) {
    try {
      const res = await api("/v1/auth/pin", "POST", { clear_pin: true });
      if (res.user) state.authUser = res.user;
    } catch {
      /* تجاهل */
    }
  }
  const overlay = document.getElementById("pin-lock-overlay");
  if (overlay) overlay.hidden = true;
  showToast(i18nText("ui.auth.pin_saved", "تم تحديث إعدادات القفل والتمويه."), "ok");
}

function lockAppNow() {
  const overlay = document.getElementById("pin-lock-overlay");
  if (overlay) {
    overlay.hidden = false;
    const input = document.getElementById("unlock-pin-input");
    if (input) {
      input.value = "";
      setTimeout(() => input.focus(), 80);
    }
  }
}

async function unlockAppWithPin(event) {
  if (event) event.preventDefault();
  const input = document.getElementById("unlock-pin-input");
  const candidate = (input?.value || "").trim();
  const localPin = localStorage.getItem(LOCAL_PIN_KEY);

  let valid = false;
  if (localPin && candidate === localPin) {
    valid = true;
  } else if (state.authUser) {
    try {
      const res = await api("/v1/auth/verify-pin", "POST", { pin: candidate });
      valid = Boolean(res && res.verified);
    } catch {
      valid = false;
    }
  }

  if (valid) {
    const overlay = document.getElementById("pin-lock-overlay");
    if (overlay) overlay.hidden = true;
    if (input) input.value = "";
  } else {
    showToast(i18nText("ui.auth.pin_invalid", "رمز PIN غير صحيح."), "danger");
  }
  return false;
}

function applyDisguiseMode(enabled) {
  const active = Boolean(enabled);
  localStorage.setItem(DISGUISE_KEY, active ? "1" : "0");
  const cb = document.getElementById("disguise-checkbox");
  if (cb) cb.checked = active;

  const brandLogo = document.getElementById("brand-logo");
  const brandTitle = document.getElementById("brand-title");
  const disguiseTitle = i18nText("ui.auth.disguise_name", "مفكرتي اليومية");

  if (active) {
    document.title = `${disguiseTitle} 📝`;
    if (brandLogo) brandLogo.textContent = "📝";
    if (brandTitle) brandTitle.textContent = disguiseTitle;
  } else {
    document.title = `CycleCare — ${i18nText("app.tagline", "تتبّع الدورة والتثقيف الصحي — بلا تشخيص")}`;
    if (brandLogo) brandLogo.textContent = "C";
    if (brandTitle) brandTitle.textContent = "CycleCare";
  }
}

async function toggleDisguiseMode(enabled) {
  applyDisguiseMode(enabled);
  if (state.authUser) {
    try {
      await api("/v1/auth/pin", "POST", { disguise_mode: Boolean(enabled) });
    } catch {
      /* محفوظ محليًا */
    }
  }
  showToast(i18nText("ui.auth.pin_saved", "تم تحديث إعدادات القفل والتمويه."), "ok");
}

async function toggleAiConsent(granted) {
  state.aiConsentGranted = Boolean(granted);
  localStorage.setItem(AI_CONSENT_KEY, state.aiConsentGranted ? "1" : "0");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send-btn");
  if (input) input.disabled = !state.aiConsentGranted;
  if (sendBtn) sendBtn.disabled = !state.aiConsentGranted;
  try {
    await api(`/v1/consents?user_key=${encodeURIComponent(deviceKey())}`, "PUT", {
      consent_type: "ai_assistant",
      granted: state.aiConsentGranted,
    });
  } catch {
    /* محفوظ محليًا */
  }
}

function switchBottomTab(tab) {
  ["today", "calendar", "insights", "assistant", "me"].forEach((t) => {
    const btn = document.getElementById(`btab-${t}`);
    if (btn) btn.classList.toggle("active", t === tab);
  });

  if (tab === "today" || tab === "calendar") {
    openTrackerTab("cycles");
  } else if (tab === "insights") {
    openTrackerTab("insights");
  } else if (tab === "assistant") {
    const tracker = document.getElementById("tracker");
    const authPanel = document.getElementById("auth-panel");
    if (tracker) tracker.hidden = true;
    if (authPanel) authPanel.hidden = true;
    enterWorkspace();
  } else if (tab === "me") {
    toggleAuthPanel();
  }
}

function phoneHtml(number, verified) {
  const cls = verified ? "phone-number" : "phone-number unverified";
  return `<span class="${cls}" data-ltr>${number}</span>`;
}

const todayIso = () => new Date().toISOString().slice(0, 10);

initTheme();
state.welcomeHtml = chatEl() ? chatEl().innerHTML : "";
fillSettingsForm(loadSettings());
if (localStorage.getItem(DISGUISE_KEY) === "1") {
  applyDisguiseMode(true);
}
if (localStorage.getItem(LOCAL_PIN_KEY)) {
  lockAppNow();
}
initI18n().then(async () => {
  renderAuthState();
  await restoreAuthSession();
  await loadCycles();
});

document.getElementById("c-date").value = todayIso();
document.getElementById("s-date").value = todayIso();

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const confirmModal = document.getElementById("confirm-modal");
    const tracker = document.getElementById("tracker");
    const settings = document.getElementById("settings");
    const authPanel = document.getElementById("auth-panel");
    if (confirmModal && !confirmModal.hidden) resolveConfirm(false);
    else if (authPanel && !authPanel.hidden) authPanel.hidden = true;
    else if (!tracker.hidden) tracker.hidden = true;
    else if (!settings.hidden) settings.hidden = true;
    else closeOverlay();
  }
});

scrollBottom();
