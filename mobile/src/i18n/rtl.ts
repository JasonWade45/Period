/**
 * تهيئة RTL الأصيلة والتعريب في تطبيق الجوال (React Native + Expo).
 * يعتمد على نفس ملفات الموارد locales/ar.json و locales/en.json كمصدر وحيد.
 */
import { I18nManager } from "react-native";
import ar from "../../../locales/ar.json";
import en from "../../../locales/en.json";

export type SupportedLocale = "ar" | "en";
export type DigitsStyle = "western" | "arabic_indic";

const RESOURCES: Record<SupportedLocale, Record<string, unknown>> = {
  ar: ar as Record<string, unknown>,
  en: en as Record<string, unknown>,
};

const ARABIC_INDIC_MAP: Record<string, string> = {
  "0": "٠",
  "1": "١",
  "2": "٢",
  "3": "٣",
  "4": "٤",
  "5": "٥",
  "6": "٦",
  "7": "٧",
  "8": "٨",
  "9": "٩",
};

export function configureNativeRTL(locale: SupportedLocale = "ar"): boolean {
  const shouldBeRTL = locale === "ar";
  I18nManager.allowRTL(true);
  if (I18nManager.isRTL !== shouldBeRTL) {
    I18nManager.forceRTL(shouldBeRTL);
    return true; // يشير إلى الحاجة لإعادة تحميل الشاشة عند تبديل الاتجاه
  }
  return false;
}

export function arabicPluralCategory(count: number): string {
  const n = Math.abs(Math.trunc(count));
  if (n === 0) return "zero";
  if (n === 1) return "one";
  if (n === 2) return "two";
  const mod100 = n % 100;
  if (mod100 >= 3 && mod100 <= 10) return "few";
  if (mod100 >= 11 && mod100 <= 99) return "many";
  return "other";
}

export function toDigits(text: string | number, style: DigitsStyle = "western"): string {
  const str = String(text);
  if (style !== "arabic_indic") return str;
  return str.replace(/[0-9]/g, (d) => ARABIC_INDIC_MAP[d] ?? d);
}

export function t(
  key: string,
  locale: SupportedLocale = "ar",
  params?: Record<string, string | number>,
  digitsStyle: DigitsStyle = "western"
): string {
  const parts = key.split(".");
  let node: unknown = RESOURCES[locale] ?? RESOURCES.ar;
  for (const part of parts) {
    if (node && typeof node === "object" && part in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[part];
    } else {
      return key;
    }
  }
  if (typeof node !== "string") return key;
  const interpolated = node.replace(/\{(\w+)\}/g, (match, name) =>
    params && name in params ? String(params[name]) : match
  );
  return toDigits(interpolated, digitsStyle);
}

/**
 * عزل اتجاهي لأرقام الهواتف داخل نص عربي (U+2066 LRI ... U+2069 PDI)
 * حتى لا يقلبها محرك BiDi في شاشات الطوارئ.
 */
export function isolatePhoneLTR(phoneNumber: string): string {
  return `\u2066${phoneNumber}\u2069`;
}
