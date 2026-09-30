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
 * يغطي: صفحة الهبوط، استمارة التسجيل (7 خطوات)، دخول التطبيق، اتجاه الصفحة،
 * موضع الفقاعات، عزل رقم الطوارئ، وعدم انعكاس الأيقونات غير الاتجاهية.
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
  await expect(page.locator("#signup")).toBeHidden();
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

test("التسجيل: 7 خطوات ثم دخول التطبيق", async ({ page }) => {
  await page.click("#cta-start");
  await expect(page.locator("#signup")).toBeVisible();
  await expect(page.locator("#signup-progress-text")).toContainText("1");
  await expect(page.locator('#signup .step[data-step="0"]')).toBeVisible();

  // خطوة 1: عمر غير صالح يمنع المتابعة
  await page.fill("#s-age", "5");
  await page.on("dialog", (d) => d.accept());
  await page.click("#signup-next");
  await expect(page.locator('#signup .step[data-step="0"]')).toBeVisible();

  // تعبئة صحيحة والمرور على كل الخطوات
  await page.fill("#s-age", "27");
  await page.click("#signup-next");
  await page.fill("#s-cycles", "12");
  await page.click("#signup-next");
  await page.fill("#s-avg", "28");
  await page.click("#signup-next");
  await page.click("#signup-next");                     // تاريخ آخر دورة اختياري
  await page.waitForTimeout(300);                       // بلدة البلد من /v1/meta
  await page.click("#signup-next");                     // بلد + احتمال حمل
  await page.click("#signup-next");                     // خطوة اختيارية
  await expect(page.locator('#signup .step[data-step="6"]')).toBeVisible();

  await page.click("#signup-next");                     // «يلا نبدأ»
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#signup")).toBeHidden();

  // الحفظ محلي: التسجيل وإجابات العمر
  const saved = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("cyclecare_context_v1") || "{}"));
  expect(saved.registered).toBe(true);
  expect(saved.age).toBe(27);
  expect(saved.cycles_recorded).toBe(12);
  expect(saved.avg_cycle_days).toBe(28);

  // إعادة التحميل تذهب مباشرة للتطبيق
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#landing")).toBeHidden();
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
