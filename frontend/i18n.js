/* التعريب في الواجهة: تحميل ملفات locales + RTL + أرقام ثنائية الصيغة.
 *
 * ملاحظات تصميمية:
 * - المصدر الوحيد للنص هو locales/<lang>.json، تمامًا كما في الباك-إند. لا نص
 *   مكتوب في JS يظهر للمستخدمة.
 * - الاتجاه (dir) يأتي مع اللغة، ويُطبَّق على <html> — بديل خفيف لمفهوم
 *   I18nManager في React Native: نفس الفكرة (فرض الاتجاه من اللغة لا من CSS).
 * - الأرقام: الغربية افتراضيًا؛ التحويل للعرض فقط، والقيم المُرسلة للخادم
 *   تبقى غربية (والباك-إند يطبّع العربية-الهندية على أي حال).
 * - أرقام الهواتف تُعرض داخل elements بـ class="ltr-isolate" حتى لا يقلبها
 *   محرك BiDi في RTL: "123" قد يظهر "321" داخل جملة عربية بلا عزل.
 */
(function (global) {
  "use strict";

  var ARABIC_INDIC = { 0: "٠", 1: "١", 2: "٢", 3: "٣", 4: "٤", 5: "٥", 6: "٦", 7: "٧", 8: "٨", 9: "٩" };
  var PLURAL_CATEGORIES = ["zero", "one", "two", "few", "many", "other"];

  var state = {
    locale: "ar",
    fallback: "ar",
    dir: "rtl",
    digitsStyle: "western",
    weekStartIndex: 5,
    translations: {},
  };

  function lookup(node, key) {
    var parts = key.split(".");
    for (var i = 0; i < parts.length; i++) {
      if (node == null || typeof node !== "object") return undefined;
      node = node[parts[i]];
    }
    return node;
  }

  function interpolate(text, params) {
    return String(text).replace(/\{(\w+)\}/g, function (match, name) {
      return Object.prototype.hasOwnProperty.call(params || {}, name) ? params[name] : match;
    });
  }

  /* قواعد CLDR للعربية — منقولة إلى JS لأن الواجهة تحتاجها قبل الرسم */
  function arabicCategory(count) {
    var n = Math.abs(Number(count));
    if (n === 0) return "zero";
    if (n === 1) return "one";
    if (n === 2) return "two";
    var rest = n % 100;
    if (rest >= 3 && rest <= 10) return "few";
    if (rest >= 11 && rest <= 99) return "many";
    return "other";
  }

  function englishCategory(count) {
    return Math.abs(Number(count)) === 1 ? "one" : "other";
  }

  function category(count) {
    return state.locale.indexOf("en") === 0 ? englishCategory(count) : arabicCategory(count);
  }

  function toDigits(value) {
    var text = String(value);
    if (state.digitsStyle !== "arabic_indic") return text;
    return text.replace(/[0-9]/g, function (d) { return ARABIC_INDIC[d]; });
  }

  function formatNumber(value) {
    var text = Number(value).toLocaleString("en-US");
    if (state.digitsStyle === "arabic_indic") {
      return toDigits(text).replace(/,/g, "\u066c");
    }
    return text;
  }

  function t(key, params) {
    var node = lookup(state.translations, key);
    if (node === undefined && state.fallback !== state.locale) {
      node = lookup(state.fallbackTranslations || {}, key);
    }
    if (typeof node !== "string") return key;      /* مفتاح ناقص يظهر صراحةً */
    return interpolate(toDigits(node), params);
  }

  function tn(key, count, params) {
    var node = lookup(state.translations, key);
    if (node && typeof node === "object") {
      var chosen = node[category(count)] || node.other;
      return interpolate(toDigits(chosen || key), Object.assign({ count: count }, params));
    }
    return t(key, Object.assign({ count: count }, params));
  }

  function applyDirection(options) {
    var html = document.documentElement;
    html.lang = state.locale;
    html.dir = state.dir;
    /* بعض المتصفحات/WebViews تحتاج إعادة تخطيط صريحة بعد تغيير الاتجاه */
    if (options && options.restart) {
      document.body.style.direction = state.dir;
    }
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
      var key = element.getAttribute("data-i18n");
      var value = t(key);
      if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
        element.placeholder = value;
      } else if (element.hasAttribute("data-i18n-attr")) {
        element.setAttribute(element.getAttribute("data-i18n-attr"), value);
      } else {
        element.textContent = value;
      }
    });
  }

  async function load(locale) {
    var meta = global.__LOCALE_CONFIG__ || {};
    var supported = meta.supported || ["ar", "en"];
    var fallback = meta.default || "ar";
    var requested = locale || localStorage.getItem("cyclecare.locale") || fallback;
    if (supported.indexOf(requested) === -1) requested = fallback;

    state.locale = requested;
    state.fallback = fallback;
    state.dir = (meta.dir && meta.dir[requested]) ||
                (requested.indexOf("ar") === 0 ? "rtl" : "ltr");
    state.digitsStyle = localStorage.getItem("cyclecare.digits") ||
                        meta.digits_style || "western";
    state.weekStartIndex = meta.week_start_index != null ? meta.week_start_index : 5;

    var response = await fetch("/locales/" + requested + ".json", { cache: "no-store" });
    state.translations = await response.json();
    if (fallback !== requested) {
      var fb = await fetch("/locales/" + fallback + ".json", { cache: "no-store" });
      state.fallbackTranslations = await fb.json();
    }
    applyDirection({ restart: true });
    return state.locale;
  }

  async function setLocale(locale) {
    localStorage.setItem("cyclecare.locale", locale);
    return load(locale);
  }

  function setDigitsStyle(style) {
    state.digitsStyle = style === "arabic_indic" ? "arabic_indic" : "western";
    localStorage.setItem("cyclecare.digits", state.digitsStyle);
    applyDirection({ restart: true });
  }

  global.i18n = {
    load: load,
    setLocale: setLocale,
    setDigitsStyle: setDigitsStyle,
    t: t,
    tn: tn,
    formatNumber: formatNumber,
    toDigits: toDigits,
    category: category,
    state: state,
    PLURAL_CATEGORIES: PLURAL_CATEGORIES,
  };
})(window);
