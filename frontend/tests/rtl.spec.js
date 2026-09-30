/**
 * اختبار لقطات RTL — يُشغَّل بمتصفح حقيقي:
 *
 *   npm i -D @playwright/test && npx playwright install chromium
 *   npx playwright test frontend/tests/rtl.spec.js
 *
 * لماذا غير مشغَّل في بيئة التطوير هنا: تنزيل متصفح Chromium يحتاج شبكة إلى
 * CDN الخاص بـPlaywright، وهي محجوبة في هذه البيئة (مثل huggingface وapi.groq).
 * لذلك كُتب الاختبار جاهزًا للتشغيل في CI، وأُضيف له فحص ساكن
 * (`tools/check_rtl.py`) يغطي ما يمكن التحقق منه بلا متصفح.
 *
 * يغطي: اتجاه الصفحة، موضع الفقاعات، عزل رقم الطوارئ، عدم انعكاس الأيقونات
 * غير الاتجاهية، والتباعد العمودي للنص العربي.
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

test("لقطة كاملة للواجهة العربية", async ({ page }) => {
  await expect(page).toHaveScreenshot("home-ar.png", { fullPage: true });
});

test("تبديل اللغة يغيّر الاتجاه والنصوص", async ({ page }) => {
  await page.click("#lang-btn");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.locator("#mode-chat")).toHaveText("Chat");
  await expect(page).toHaveScreenshot("home-en.png", { fullPage: true });
});

test("لقطة رسالة طوارئ: الرقم لا ينعكس", async ({ page }) => {
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
