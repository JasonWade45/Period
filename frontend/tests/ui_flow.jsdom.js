/* تحقق واجهة بلا متصفح (jsdom) على خادم يعمل على 127.0.0.1:8000.
 * التشغيل: npm i jsdom && node frontend/tests/ui_flow.jsdom.js
 * يغطي: لوحة واحدة، الاسم، المحادثة/الملخص على /api/v1/ai/*، الشارات، الطوارئ. */
const { JSDOM } = require("jsdom");
(async () => {
  const base = "http://127.0.0.1:8000";
  const dom = await JSDOM.fromURL(base + "/", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, base).href, o);
      w.alert = (m) => console.log("ALERT:", m);
      w.confirm = () => true;
      w.HTMLElement.prototype.scrollIntoView = () => {};
      if (!w.crypto || !w.crypto.randomUUID) Object.defineProperty(w, "crypto", { value: { randomUUID: () => "jsdom-device-" + Math.random() } });
    },
  });
  const w = dom.window, d = w.document;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  await wait(1500);
  const ok = (c, m) => console.log((c ? "PASS " : "FAIL ") + m);
  ok(!d.getElementById("tracker") && !d.getElementById("tracker-btn"), "no separate tracker/settings panels");
  ok(d.querySelectorAll("aside.settings").length === 1, "exactly one side panel");
  // open panel -> profile tab -> save name
  d.getElementById("settings-btn").click();
  ok(!d.getElementById("panel").hidden, "panel opens");
  w.setPanelTab("profile");
  ok(!d.getElementById("pane-profile").hidden && d.getElementById("pane-cycles").hidden, "tabs switch");
  d.getElementById("f-name").value = "  منى\n";
  await w.saveSettings(); await wait(500);
  ok(w.eval("state.name") === "منى", "name saved via server: " + w.eval("state.name"));
  ok(d.querySelector("#welcome h2").textContent.includes("منى"), "welcome greets by name: " + d.querySelector("#welcome h2").textContent);
  ok(d.getElementById("panel").hidden, "panel closes after save");
  // chat
  await w.ask("ليه الدورة بتتأخر؟"); await wait(1500);
  const bots = [...d.querySelectorAll(".msg.bot")];
  const last = bots[bots.length - 1];
  console.log("BOT:", last.firstChild.textContent.slice(0, 80).replace(/\n/g, " "));
  ok(last.textContent.startsWith("منى،"), "chat reply addresses her by name");
  ok(!!last.querySelector(".badge.unreviewed"), "unreviewed badge: " + (last.querySelector(".badge.unreviewed")||{}).textContent);
  ok(!!last.querySelector(".badge.simple"), "simple-mode badge present");
  // guard
  await w.ask("اخد كام حبة ايبوبروفين"); await wait(1200);
  const g = [...d.querySelectorAll(".msg.bot")].pop();
  ok(g.textContent.includes("طبيبتكِ") && !g.querySelector(".badge.unreviewed"), "dosing question -> fixed guard reply, no sources");
  // summary uses the same endpoint family + same message card
  w.setMode("summary");
  await w.ask("اعملي ملخص"); await wait(1500);
  const s = [...d.querySelectorAll(".msg.bot")].pop();
  ok(s.classList.contains("summary-card") && s.querySelectorAll(".summary-section").length >= 3, "summary card rendered, sections=" + s.querySelectorAll(".summary-section").length);
  ok(s.textContent.includes("منى"), "summary addresses her by name");
  ok(!!s.querySelector(".badge.simple"), "summary shows simple-mode badge");
  // emergency
  w.setMode("chat");
  await w.ask("بنزف كل ساعة وبرمي جلطات كبيرة"); await wait(1200);
  ok(!d.getElementById("overlay").hidden, "emergency overlay shows");
  w.closeOverlay();
  // insights shortcut
  w.showInsights(); await wait(800);
  ok(!d.getElementById("pane-insights").hidden && !d.getElementById("panel").hidden, "insights chip opens unified panel");
  // language toggle keeps name
  w.toggleLanguage(); await wait(800);
  console.log("EN tab label:", d.getElementById("tab-profile").textContent, "| html dir:", d.documentElement.dir);
  process.exit(0);
})();
