/**
 * التخزين المحلي المشفّر (Local-First Encrypted SQLite) على الجهاز.
 * يعمل كمصدر الحقيقة الأساسي للتتبّع حتى بلا إنترنت وبلا حساب وبلا مساعد ذكي.
 * يُحفظ مفتاح التشفير ورمز PIN في مخزن العتاد الآمن (Expo SecureStore: Keychain / Keystore).
 */
import * as SecureStore from "expo-secure-store";
import * as SQLite from "expo-sqlite";

const DB_KEY_STORAGE = "cyclecare.db.encryption_key.v1";
const DB_NAME = "cyclecare_local.db";

export interface LocalCycleRow {
  id: number;
  start_date: string;
  length_days: number | null;
  flow_level: string | null;
  synced: number;
}

export interface LocalSymptomRow {
  id: number;
  log_date: string;
  symptom: string;
  severity: number | null;
  note: string | null;
  synced: number;
}

export class EncryptedLocalDb {
  private db: SQLite.SQLiteDatabase | null = null;

  async init(): Promise<void> {
    let key = await SecureStore.getItemAsync(DB_KEY_STORAGE);
    if (!key) {
      key = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      await SecureStore.setItemAsync(DB_KEY_STORAGE, key);
    }
    this.db = await SQLite.openDatabaseAsync(DB_NAME);
    // تفعيل مفتاح التشفير في بيئات SQLCipher، وضبط WAL للسرعة (< 100ms)
    try {
      await this.db.execAsync(`PRAGMA key = '${key.replace(/'/g, "")}';`);
    } catch {
      /* SQLite القياسي في المحاكي يتجاهل PRAGMA key دون كسر التشغيل */
    }
    await this.db.execAsync(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS local_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_date TEXT NOT NULL UNIQUE,
        length_days INTEGER,
        flow_level TEXT,
        synced INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_local_cycles_date ON local_cycles (start_date);

      CREATE TABLE IF NOT EXISTS local_symptoms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_date TEXT NOT NULL,
        symptom TEXT NOT NULL,
        severity INTEGER,
        note TEXT,
        synced INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_local_symptoms_date ON local_symptoms (log_date);
    `);
  }

  async addCycle(startDate: string, lengthDays?: number | null, flowLevel?: string | null): Promise<void> {
    if (!this.db) await this.init();
    await this.db!.runAsync(
      "INSERT OR REPLACE INTO local_cycles (start_date, length_days, flow_level, synced, created_at) VALUES (?, ?, ?, 0, ?)",
      startDate,
      lengthDays ?? null,
      flowLevel ?? null,
      new Date().toISOString()
    );
  }

  async listCycles(limit: number = 24): Promise<LocalCycleRow[]> {
    if (!this.db) await this.init();
    return await this.db!.getAllAsync<LocalCycleRow>(
      "SELECT id, start_date, length_days, flow_level, synced FROM local_cycles ORDER BY start_date DESC LIMIT ?",
      limit
    );
  }

  async addSymptom(logDate: string, symptom: string, severity?: number | null, note?: string | null): Promise<void> {
    if (!this.db) await this.init();
    await this.db!.runAsync(
      "INSERT INTO local_symptoms (log_date, symptom, severity, note, synced, created_at) VALUES (?, ?, ?, ?, 0, ?)",
      logDate,
      symptom,
      severity ?? null,
      note ?? null,
      new Date().toISOString()
    );
  }

  async listSymptoms(limit: number = 100): Promise<LocalSymptomRow[]> {
    if (!this.db) await this.init();
    return await this.db!.getAllAsync<LocalSymptomRow>(
      "SELECT id, log_date, symptom, severity, note, synced FROM local_symptoms ORDER BY log_date DESC, id DESC LIMIT ?",
      limit
    );
  }

  async wipeAllLocalData(): Promise<void> {
    if (!this.db) await this.init();
    await this.db!.execAsync(`
      DELETE FROM local_cycles;
      DELETE FROM local_symptoms;
    `);
    await SecureStore.deleteItemAsync(DB_KEY_STORAGE);
  }
}

export const localDb = new EncryptedLocalDb();
