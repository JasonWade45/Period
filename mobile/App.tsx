/**
 * نقطة الدخول الرئيسية لتطبيق الجوال (React Native + Expo).
 * يحقق معيار المرحلة 1:
 * - RTL أصيل (I18nManager).
 * - نظام تصميم موحّد (فاتح/داكن).
 * - تخزين محلي مشفّر يعمل بالكامل على الجهاز بلا مساعد ذكي.
 * - مصادقة حقيقية + وضع محلي بلا حساب + قفل PIN/بصمة + وضع تمويه.
 * - تنقل سفلي بـ 5 تبويبات + زر طوارئ ثابت.
 */
import React, { useEffect, useState } from "react";
import { SafeAreaView, StyleSheet, Text, View } from "react-native";
import { AuthProvider, useAuth } from "./src/auth/AuthContext";
import { configureNativeRTL, SupportedLocale, t } from "./src/i18n/rtl";
import { BottomTabs, TabKey } from "./src/navigation/BottomTabs";
import { localDb, LocalCycleRow } from "./src/storage/encryptedDb";
import { DARK_THEME, LIGHT_THEME, ThemeMode } from "./src/theme/tokens";

const MainShell: React.FC = () => {
  const [locale] = useState<SupportedLocale>("ar");
  const [themeMode] = useState<ThemeMode>("light");
  const [activeTab, setActiveTab] = useState<TabKey>("today");
  const [cycles, setCycles] = useState<LocalCycleRow[]>([]);
  const { disguiseMode } = useAuth();

  const theme = themeMode === "dark" ? DARK_THEME : LIGHT_THEME;

  useEffect(() => {
    configureNativeRTL(locale);
    localDb.init().then(() => localDb.listCycles()).then(setCycles);
  }, [locale]);

  const headerTitle = disguiseMode
    ? t("ui.auth.disguise_name", locale)
    : t("app.name", locale);

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: theme.bg }]}>
      <View style={[styles.header, { borderBottomColor: theme.line }]}>
        <Text style={[styles.title, { color: theme.ink }]}>{headerTitle}</Text>
        <Text style={[styles.subtitle, { color: theme.inkSoft }]}>
          {t("app.not_a_diagnosis", locale)}
        </Text>
      </View>

      <View style={styles.content}>
        <View style={[styles.ringCard, { backgroundColor: theme.card, borderColor: theme.line }]}>
          <Text style={[styles.ringValue, { color: theme.roseDark }]}>
            {cycles.length > 0 ? cycles[0].start_date : t("ui.tracker.ring_empty_label", locale)}
          </Text>
          <Text style={[styles.ringCaption, { color: theme.inkSoft }]}>
            {t("ui.tracker.insights_intro", locale)}
          </Text>
        </View>
      </View>

      <BottomTabs
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        locale={locale}
        theme={theme}
        emergencyNumber="123"
      />
    </SafeAreaView>
  );
};

export default function App() {
  return (
    <AuthProvider>
      <MainShell />
    </AuthProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  header: {
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderBottomWidth: 1,
  },
  title: {
    fontSize: 20,
    fontWeight: "700",
    textAlign: "auto",
  },
  subtitle: {
    fontSize: 12,
    marginTop: 4,
    textAlign: "auto",
  },
  content: {
    flex: 1,
    padding: 18,
    justifyContent: "center",
  },
  ringCard: {
    borderWidth: 1,
    borderRadius: 24,
    padding: 24,
    alignItems: "center",
  },
  ringValue: {
    fontSize: 22,
    fontWeight: "700",
  },
  ringCaption: {
    fontSize: 13,
    marginTop: 8,
    textAlign: "center",
  },
});
