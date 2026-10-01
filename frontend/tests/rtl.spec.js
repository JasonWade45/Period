/**
 * اختبار لقطات RTL — يُشغَّل بمتصفح حقيقي:
 *
 *   npm i -D @playwright/test && npx playwright install chromium
 *   BASE_URL=http://127.0.0.1:8115 npx playwright test frontend/tests/rtl.spec.js
 *
 * لماذا غير مشغَّل في بيئة التطوير هنا: تنزيل متصفح Chromium يحتاج شبكة إلى
 * CDN الخاص بـPlaywright، وهي محجوبة في هذه البيئة (مثل huggingface وapi.groq).
 * لذلك كُتب الاختبار جاهزًا للتشغيل في CI، وأُضيف له فحص ساكن
 * (`tools/check_rtl.py`) يغطي ما يمكن التحقق منه بلا متصفح.
 *
 * يغطي: صفحة الهبوط، شاشة الحساب (إنشاء/دخول)، استمارة البروفايل (6 أقسام)،
 * الداشبورد، دخول التطبيق، اتجاه الصفحة، موضع الفقاعات، عزل رقم الطوارئ،
 * وعدم انعكاس الأيقونات غير الاتجاهية.
 *
 * ملاحظة: تغيّر محتوى الصفحة يستدعي تحديث اللقطات القاعدية:
 *   npx playwright test frontend/tests/rtl.spec.js --update-snapshots
 */
const { test, expect } = require("@playwright/test");

const BASE = process.env.BASE_URL || "http://127.0.0.1:8113";

test.beforeEach(async ({ page }) => {
  await page.goto(BASE, { waitUntil: "networkidle" });
});

test("العربية افتراضيًا واتجاه الصفحة RTL", async ({ page }) => {
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", /^ar/);
});

test("صفحة الهبوط مرئية والتطبيق مخفي", async ({ page }) => {
  await expect(page.locator("#landing")).toBeVisible();
  await expect(page.locator("#app")).toBeHidden();
  await expect(page).toHaveScreenshot("landing-ar.png", { fullPage: true });
});

test("زر اللغة في الهبوط يبدّل الاتجاه", async ({ page }) => {
  await page.click("#lang-btn-landing");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("#cta-start")).toHaveText("Sign up now");
  await expect(page).toHaveScreenshot("landing-en.png", { fullPage: true });
});

test("زر الدخول يفتح شاشة الحساب في وضع تسجيل الدخول", async ({ page }) => {
  await page.click(".landing-login");
  await expect(page.locator("#auth")).toBeVisible();
  await expect(page.locator("#landing")).toBeHidden();
  await expect(page.locator("#a-submit")).toHaveText("دخول");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});

test("التسجيل: حساب + استمارة البروفايل (6 أقسام) + داشبورد ثم التطبيق", async ({ page }) => {
  await page.click("#cta-start");
  await expect(page.locator("#auth")).toBeVisible();
  await expect(page.locator("#a-submit")).toHaveText("إنشاء الحساب");

  const email = `rtl-${Date.now()}@example.com`;
  await page.fill("#a-email", email);
  await page.fill("#a-password", "secret-pass-123");
  await page.click("#a-submit");
  await page.waitForURL("**/profile.html");

  await expect(page.locator("#stage h1")).toHaveText("أساسيات");
  await expect(page.locator("#cnt")).toContainText("القسم 1 من 6");

  // إجابة واحدة: سنة الميلاد (يُحسب العمر منها عند الإنهاء)
  await page.selectOption("#stage select", "2000");

  for (let i = 0; i < 5; i++) {
    await page.click("#foot .go:not(.ghost)");   // «التالي» عبر الأقسام
  }
  await expect(page.locator("#stage h1")).toHaveText("أعراضك والتنبيهات");
  await page.click("#foot .go:not(.ghost)");     // «خلّصت»
  await expect(page.locator(".done")).toBeVisible();

  // الحفظ محلي: التسجيل + العمر المحسوب
  const saved = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("cyclecare_context_v1") || "{}"));
  expect(saved.registered).toBe(true);
  expect(saved.age).toBe(new Date().getFullYear() - 2000);

  // جلسة الحساب سارية على الخادم
  const me = await page.evaluate(() => fetch("/v1/auth/me").then((r) => r.json()));
  expect(me.authenticated).toBe(true);
  expect(me.email).toBe(email);

  await page.click("#foot .go:not(.ghost)");     // «افتحي التطبيق»
  await expect(page.locator("#dashboard")).toBeVisible();   // الداشبورد أولًا
  await expect(page.locator("#dash-cycles-list")).toBeVisible();

  await page.click('[data-i18n="dash.to_chat"]');           // ثم المحادثة
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#dashboard")).toBeHidden();
  await expect(page.locator("#dash-btn")).toBeVisible();     // زر الداشبورد في الهيدر

  // إعادة التحميل تذهب للتطبيق (والعلامة أُزيلت — لا داشبورد تلقائيًا)
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#dashboard")).toBeHidden();
});

test("لقطة كاملة لواجهة المحادثة العربية", async ({ page }) => {
  await page.evaluate(() => enterApp());
  await expect(page).toHaveScreenshot("home-ar.png", { fullPage: true });
});

test("تبديل اللغة يغيّر الاتجاه والنصوص", async ({ page }) => {
  await page.evaluate(() => enterApp());
  await page.click("#lang-btn");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.locator("#mode-chat")).toHaveText("Chat");
  await expect(page).toHaveScreenshot("home-en.png", { fullPage: true });
});

test("لقطة رسالة طوارئ: الرقم لا ينعكس", async ({ page }) => {
  await page.evaluate(() => enterApp());
  await page.fill("#input", "عندي نزيف غزير وبغير فوطه كل ساعه");
  await page.click("#send-btn");
  await expect(page.locator("#overlay")).toBeVisible();

  const phone = page.locator("#overlay .phone-number").first();
  await expect(phone).toBeVisible();
  // العزل الاتجاهي هو ما يمنع قلب الرقم داخل جملة عربية
  const bidi = await phone.evaluate((el) => getComputedStyle(el).direction);
  expect(bidi).toBe("ltr");
  await expect(page).toHaveScreenshot("emergency-ar.png");
});

test("الأيقونات غير الاتجاهية لا تُقلب", async ({ page }) => {
  const transform = await page.locator("#settings-btn").evaluate(
    (el) => getComputedStyle(el).transform);
  expect(transform === "none" || transform === "matrix(1, 0, 0, 1, 0, 0)").toBeTruthy();
});

test("ارتفاع السطر للنص العربي لا يقل عن 1.6", async ({ page }) => {
  const ratio = await page.locator("body").evaluate((el) => {
    const style = getComputedStyle(el);
    return parseFloat(style.lineHeight) / parseFloat(style.fontSize);
  });
  expect(ratio).toBeGreaterThanOrEqual(1.6);
});

test("لا تباعد أحرف في النص العربي", async ({ page }) => {
  const spacing = await page.locator("body").evaluate(
    (el) => getComputedStyle(el).letterSpacing);
  expect(["normal", "0px"]).toContain(spacing);
});
