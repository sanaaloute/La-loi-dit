import * as SecureStore from "expo-secure-store";
import { uuid } from "./uuid";

const DEVICE_ID_KEY = "yawoto.device_id";

let cached: string | null = null;
let inFlight: Promise<string> | null = null;

/**
 * Stable device identifier sent as `X-Device-Id` on every API request. The
 * backend binds mobile sessions to this id instead of the client IP (phones
 * roam between networks). Generated once, persisted in the secure store.
 */
export function getDeviceId(): Promise<string> {
  if (cached) return Promise.resolve(cached);
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const existing = await SecureStore.getItemAsync(DEVICE_ID_KEY);
      if (existing) {
        cached = existing;
        return existing;
      }
    } catch {
      // Secure store unreadable: fall through and generate a session-scoped id.
    }
    const id = uuid();
    try {
      await SecureStore.setItemAsync(DEVICE_ID_KEY, id);
    } catch {
      // Persistence failed: the in-memory id still keeps this run consistent.
    }
    cached = id;
    return id;
  })().finally(() => {
    inFlight = null;
  });
  return inFlight;
}
