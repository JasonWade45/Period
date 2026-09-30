/* CycleCare frontend */
"use strict";

const API = ""; // نفس الأصل (يُخدم من FastAPI)

const state = {
  mode: "chat",
  loading: false,
  messages: [], // {role, text?, data?}
};

/* ---------------- settings (localStorage) ---------------- */

const SETTINGS_KEY = "cyclecare_context_v1";

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
  } catch {
    return {};
  }
}

function fillSettingsForm(s) {
  document.getElementById("f-age").value = s.age ?? "";
  document.getElementById("f-cycles").value = s.cycles_recorded ?? 0;
  document.getElementById("f-avg").value = s.avg_cycle_days ?? "";
  document.getElementById("f-pregnancy").value = s.pregnancy_status ?? "";
  document.getElementById("f-contraception").value = s.contraception ?? "";
  document.getElementById("f-conditions").value =
    (s.conditions || []).map((c) => `${c.name} | ${c.status}`).join("\n");

  const rows = document.querySelectorAll("#f-last .cycle-row");
  (s.last_cycles || []).slice(0, rows.length).forEach((c, i) => {
    rows[i].querySelector(".c-date").value = c.start_date || "";
    rows[i].querySelector(".c-len").value = c.length_days || "";
  });
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

  const last_cycles = [];
  document.querySelectorAll("#f-last .cycle-row").forEach((row) => {
    const d = row.querySelector(".c-date").value;
    const l = parseInt(row.querySelector(".c-len").value, 10);
    if (d) last_cycles.push({ start_date: d, length_days: Number.isFinite(l) ? l : null });
  });

  const num = (id) => {
    const v = document.getElementById(id).value;
    return v === "" ? null : Number(v);
  };

  return {
    age: num("f-age"),
    cycles_recorded: num("f-cycles") ?? 0,
    avg_cycle_days: num("f-avg"),
    pregnancy_status: document.getElementById("f-pregnancy").value || null,
    contraception: document.getElementById("f-contraception").value || null,
    conditions,
    last_cycles,
  };
}

function saveSettings() {
  const s = readSettingsForm();
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  toggleSettings();
}

function clearSettings() {
  localStorage.removeItem(SETTINGS_KEY);
  fillSettingsForm({});
}

function toggleSettings() {
  const el = document.getElementById("settings");
  el.hidden = !el.hidden;
}

/* ---------------- mode ---------------- */

function setMode(mode) {
  state.mode = mode;
  document.getElementById("mode-chat").classList.toggle("active", mode === "chat");
  document.getElementById("mode-summary").classList.toggle("active", mode === "summary");
  document.getElementById("input").placeholder =
    mode === "summary" ? "اطلب ملخصًا لدوراتي…" : "اكتبي سؤالك هنا…";
}

/* ---------------- chat rendering ---------------- */

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
  if (state.mode === "summary" && data.overview !== undefined) {
    container.classList.add("summary-card");
    const sections = [
      ["نظرة عامة", data.overview],
      ["ما الذي تغيّر", data.what_changed],
      ["الأنماط المرصودة", data.patterns],
      ["تنبيهات", data.medical_alerts],
      ["ما لا يعنيه هذا", data.what_this_does_not_mean],
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
      h.textContent = "أسئلة لطبيبتِك";
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

  if (data.needs_doctor) add("doctor", "يستحق مراجعة طبية");
  (data.rule_codes || []).forEach((r) => add("rule", r));
  (data.sources_used || []).forEach((s) => add("source", s));
  (data.missing_info || []).forEach((m) => add("missing", `ناقص: ${m}`));

  if (meta.children.length) container.appendChild(meta);
}

function addError(text) {
  const div = document.createElement("div");
  div.className = "msg msg-error";
  div.textContent = text;
  chatEl().appendChild(div);
}

/* ---------------- emergency overlay ---------------- */

function showOverlay(data) {
  const overlay = document.getElementById("overlay");
  const crisis = !!data.crisis;
  overlay.classList.toggle("crisis", crisis);
  document.getElementById("overlay-title").textContent = crisis
    ? "دواعي فورية لطلب المساعدة"
    : "رعاية طبية عاجلة";
  document.getElementById("overlay-text").textContent = data.answer || "";
  overlay.hidden = false;
}

function closeOverlay() {
  document.getElementById("overlay").hidden = true;
}

/* ---------------- send ---------------- */

async function ask(text) {
  if (!text || state.loading) return;
  setModeFromShortcut(text);
  addMessage("user", text);
  await send(text);
}

function setModeFromShortcut(text) {
  if (text.includes("ملخص") && state.mode !== "summary") setMode("summary");
}

async function send(text) {
  state.loading = true;
  document.getElementById("send-btn").disabled = true;
  document.getElementById("typing").hidden = false;
  scrollBottom();

  try {
    const resp = await fetch(`${API}/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        mode: state.mode,
        user_context: loadSettings(),
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const data = await resp.json();
    state.messages.push({ role: "user", text }, { role: "bot", data });

    if (data.emergency) {
      showOverlay(data);
      addMessage("bot", data.answer);
    } else {
      addMessage("bot", data);
    }
  } catch (e) {
    addError(`تعذّر الاتصال بالخادم: ${e.message}`);
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

/* ---------------- init ---------------- */

fillSettingsForm(loadSettings());

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const settings = document.getElementById("settings");
    if (!settings.hidden) settings.hidden = true;
    else closeOverlay();
  }
});

scrollBottom();
