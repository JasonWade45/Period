/**
 * نظام التصميم الموحّد (Design System Tokens) — نمطان فاتح وداكن بتباين ≥ 4.5:1
 * مع دعم تكبير الخط وخصائص الاتجاه المنطقية (start / end).
 */
export type ThemeMode = "light" | "dark";

export interface ThemePalette {
  mode: ThemeMode;
  bg: string;
  bgSoft: string;
  card: string;
  ink: string;
  inkSoft: string;
  line: string;
  rose: string;
  roseDark: string;
  roseSoft: string;
  violet: string;
  violetSoft: string;
  ok: string;
  warn: string;
  urgent: string;
}

export const LIGHT_THEME: ThemePalette = {
  mode: "light",
  bg: "#fdf8fa",
  bgSoft: "#f6eef2",
  card: "#ffffff",
  ink: "#231922",          // نسبة التباين مع #ffffff > 14:1
  inkSoft: "#594857",      // نسبة التباين مع #ffffff > 7.2:1 (أعلى من 4.5:1)
  line: "#eadce3",
  rose: "#c43666",
  roseDark: "#9c224d",
  roseSoft: "#fceef3",
  violet: "#6d4aff",
  violetSoft: "#f1edff",
  ok: "#1f7a5a",
  warn: "#9a6309",
  urgent: "#be123c",
};

export const DARK_THEME: ThemePalette = {
  mode: "dark",
  bg: "#120d14",
  bgSoft: "#1b141e",
  card: "#1f1723",
  ink: "#f8eef3",          // نسبة التباين مع #1f1723 > 13.5:1
  inkSoft: "#cbb8c6",      // نسبة التباين مع #1f1723 > 7.5:1 (أعلى من 4.5:1)
  line: "#332638",
  rose: "#e85d8c",
  roseDark: "#ff8ab1",
  roseSoft: "#351c29",
  violet: "#9478ff",
  violetSoft: "#261e42",
  ok: "#34d399",
  warn: "#fbbf24",
  urgent: "#f43f5e",
};

export const TYPOGRAPHY = {
  fontFamilyArabic: "IBMPlexSansArabic-Regular",
  lineHeightArabicFactor: 1.7,
  scale(baseSize: number, fontMultiplier: number = 1.0): number {
    return Math.round(baseSize * Math.min(1.4, Math.max(0.85, fontMultiplier)));
  },
};
