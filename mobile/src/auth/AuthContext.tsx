/**
 * سياق المصادقة وقفل التطبيق (Auth + App Lock PIN/Biometric + Disguise Mode).
 * يدعم:
 * - وضع بلا حساب (Anonymous Local-First): كل شيء يعمل على الجهاز.
 * - حساب حقيقي (اسم مستخدم/بريد + كلمة مرور + تجديد توكن Refresh Token).
 * - قفل التطبيق بـ PIN أو بصمة/وجه (expo-local-authentication).
 * - وضع التمويه (تغيير الاسم الظاهر إلى «مفكرتي اليومية»).
 */
import React, { createContext, useContext, useState } from "react";
import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";

const PIN_STORE_KEY = "cyclecare.security.pin";
const ACCESS_STORE_KEY = "cyclecare.auth.access_token";
const REFRESH_STORE_KEY = "cyclecare.auth.refresh_token";

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  user_key: string;
  has_pin: boolean;
  disguise_mode: boolean;
}

export interface AuthContextValue {
  user: AuthUser | null;
  isLocked: boolean;
  disguiseMode: boolean;
  signIn: (apiBase: string, username: string, password: string) => Promise<void>;
  register: (apiBase: string, username: string, password: string, displayName?: string) => Promise<void>;
  signOut: (apiBase: string) => Promise<void>;
  unlockWithPin: (pin: string) => Promise<boolean>;
  unlockWithBiometrics: () => Promise<boolean>;
  setPin: (pin: string | null) => Promise<void>;
  setDisguiseMode: (enabled: boolean) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLocked, setIsLocked] = useState<boolean>(false);
  const [disguiseMode, setDisguiseMode] = useState<boolean>(false);

  const signIn = async (apiBase: string, username: string, password: string) => {
    const resp = await fetch(`${apiBase}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) throw new Error("invalid_credentials");
    const data = await resp.json();
    await SecureStore.setItemAsync(ACCESS_STORE_KEY, data.access_token);
    await SecureStore.setItemAsync(REFRESH_STORE_KEY, data.refresh_token);
    setUser(data.user);
    setDisguiseMode(Boolean(data.user?.disguise_mode));
  };

  const register = async (apiBase: string, username: string, password: string, displayName?: string) => {
    const resp = await fetch(`${apiBase}/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, display_name: displayName }),
    });
    if (!resp.ok) throw new Error("registration_failed");
    const data = await resp.json();
    await SecureStore.setItemAsync(ACCESS_STORE_KEY, data.access_token);
    await SecureStore.setItemAsync(REFRESH_STORE_KEY, data.refresh_token);
    setUser(data.user);
  };

  const signOut = async (apiBase: string) => {
    const refreshToken = await SecureStore.getItemAsync(REFRESH_STORE_KEY);
    if (refreshToken) {
      await fetch(`${apiBase}/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      }).catch(() => undefined);
    }
    await SecureStore.deleteItemAsync(ACCESS_STORE_KEY);
    await SecureStore.deleteItemAsync(REFRESH_STORE_KEY);
    setUser(null);
  };

  const setPin = async (pin: string | null) => {
    if (!pin) {
      await SecureStore.deleteItemAsync(PIN_STORE_KEY);
      setIsLocked(false);
      return;
    }
    await SecureStore.setItemAsync(PIN_STORE_KEY, pin);
  };

  const unlockWithPin = async (candidate: string): Promise<boolean> => {
    const stored = await SecureStore.getItemAsync(PIN_STORE_KEY);
    if (stored && stored === candidate.trim()) {
      setIsLocked(false);
      return true;
    }
    return false;
  };

  const unlockWithBiometrics = async (): Promise<boolean> => {
    const hasHardware = await LocalAuthentication.hasHardwareAsync();
    const isEnrolled = await LocalAuthentication.isEnrolledAsync();
    if (!hasHardware || !isEnrolled) return false;
    const res = await LocalAuthentication.authenticateAsync({
      promptMessage: "افتحي قفل التطبيق",
      fallbackLabel: "استخدمي رمز PIN",
    });
    if (res.success) {
      setIsLocked(false);
      return true;
    }
    return false;
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isLocked,
        disguiseMode,
        signIn,
        register,
        signOut,
        unlockWithPin,
        unlockWithBiometrics,
        setPin,
        setDisguiseMode,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
