// Store release info and outbound links: WhatsApp contact, terms page, store
// listings, and the "is my build outdated" check backing both the launch
// dialog and the À propos update button.

import Constants from "expo-constants";
import { Linking, Platform } from "react-native";
import { apiUrl } from "./api";

export const APP_VERSION = Constants.expoConfig?.version ?? "";

export const WHATSAPP_URL = "https://wa.me/8613141392484";
export const TERMS_URL = "https://yawoto.neobytech.net/confidentialite";
export const STORE_URL_IOS = "https://apps.apple.com/app/id6826883491";
export const STORE_URL_ANDROID =
  "https://play.google.com/store/apps/details?id=net.neobytech.yawoto";

export interface LatestRelease {
  latest_version: string;
  release_notes: string;
  store_url_ios: string;
  store_url_android: string;
}

// Session cache shared by the launch dialog and the account screen so both
// read one fetch; a focus refresh repeats it at most every few minutes.
const CACHE_TTL_MS = 5 * 60 * 1000;
let cached: { at: number; release: LatestRelease } | null = null;

export async function fetchLatestRelease(force = false): Promise<LatestRelease | null> {
  if (!force && cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.release;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);
  try {
    const res = await fetch(apiUrl("/app/version"), { signal: controller.signal });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<LatestRelease>;
    if (typeof body.latest_version !== "string") return null;
    const release: LatestRelease = {
      latest_version: body.latest_version,
      release_notes: typeof body.release_notes === "string" ? body.release_notes : "",
      store_url_ios: body.store_url_ios || STORE_URL_IOS,
      store_url_android: body.store_url_android || STORE_URL_ANDROID,
    };
    cached = { at: Date.now(), release };
    return release;
  } catch {
    // Offline or tunnel down: no update prompt, no noise.
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function segmentToNumber(part: string): number {
  const n = parseInt(part.replace(/\D.*$/, ""), 10);
  return Number.isFinite(n) ? n : 0;
}

/** Numeric dotted compare: "1.0.10" > "1.0.9"; empty/unknown → false. */
export function isUpdateAvailable(current: string, latest: string): boolean {
  if (!current || !latest) return false;
  const a = current.split(".").map(segmentToNumber);
  const b = latest.split(".").map(segmentToNumber);
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (y > x) return true;
    if (y < x) return false;
  }
  return false;
}

export function openStoreListing(release?: LatestRelease | null): void {
  const url =
    Platform.OS === "ios"
      ? release?.store_url_ios || STORE_URL_IOS
      : release?.store_url_android || STORE_URL_ANDROID;
  void Linking.openURL(url).catch(() => {});
}

export function openWhatsApp(): void {
  void Linking.openURL(WHATSAPP_URL).catch(() => {});
}

export function openTerms(): void {
  void Linking.openURL(TERMS_URL).catch(() => {});
}
