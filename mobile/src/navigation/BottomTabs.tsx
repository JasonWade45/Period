/**
 * شريط التنقل السفلي الخماسي (اليوم، التقويم، الرؤى، المساعد، أنا)
 * مع زر الطوارئ الثابت المعزول اتجاهيًا (LTR isolation).
 */
import React from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { isolatePhoneLTR, SupportedLocale, t } from "../i18n/rtl";
import { ThemePalette } from "../theme/tokens";

export type TabKey = "today" | "calendar" | "insights" | "assistant" | "me";

interface BottomTabsProps {
  activeTab: TabKey;
  onSelectTab: (tab: TabKey) => void;
  locale: SupportedLocale;
  theme: ThemePalette;
  emergencyNumber: string;
}

const TAB_ITEMS: Array<{ key: TabKey; icon: string; labelKey: string }> = [
  { key: "today", icon: "🌸", labelKey: "ui.tabs.today" },
  { key: "calendar", icon: "📅", labelKey: "ui.tabs.calendar" },
  { key: "insights", icon: "📊", labelKey: "ui.tabs.insights" },
  { key: "assistant", icon: "💬", labelKey: "ui.tabs.assistant" },
  { key: "me", icon: "👤", labelKey: "ui.tabs.me" },
];

export const BottomTabs: React.FC<BottomTabsProps> = ({
  activeTab,
  onSelectTab,
  locale,
  theme,
  emergencyNumber,
}) => {
  return (
    <View style={[styles.bar, { backgroundColor: theme.card, borderColor: theme.line }]}>
      {TAB_ITEMS.map((item) => {
        const active = item.key === activeTab;
        return (
          <Pressable
            key={item.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            onPress={() => onSelectTab(item.key)}
            style={[
              styles.tabBtn,
              active && { backgroundColor: theme.roseSoft },
            ]}
          >
            <Text style={styles.icon}>{item.icon}</Text>
            <Text
              style={[
                styles.label,
                { color: active ? theme.roseDark : theme.inkSoft },
              ]}
            >
              {t(item.labelKey, locale)}
            </Text>
          </Pressable>
        );
      })}

      <Pressable
        accessibilityRole="button"
        onPress={() => Linking.openURL(`tel:${emergencyNumber}`)}
        style={[styles.sosBtn, { backgroundColor: theme.urgent }]}
      >
        <Text style={styles.sosText}>
          🚨 {t("ui.tabs.emergency_btn", locale)} {isolatePhoneLTR(emergencyNumber)}
        </Text>
      </Pressable>
    </View>
  );
};

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderTopWidth: 1,
  },
  tabBtn: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 6,
    borderRadius: 16,
  },
  icon: {
    fontSize: 16,
  },
  label: {
    fontSize: 11,
    fontWeight: "700",
    marginTop: 2,
  },
  sosBtn: {
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 999,
    marginStart: 6,
  },
  sosText: {
    color: "#ffffff",
    fontSize: 11,
    fontWeight: "700",
  },
});
