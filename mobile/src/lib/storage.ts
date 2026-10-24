// Persistent client state: JWT and active chat session id.
// SecureStore is async; values are mirrored in memory at startup so the API
// layer can read them synchronously (like the web app's localStorage).
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "yawoto.token";
const REFRESH_TOKEN_KEY = "yawoto.refresh_token";
const SESSION_KEY = "yawoto.session_id";

// Allow a small amount of client clock skew so a token is not discarded the
// instant its server-issued `exp` is reached.
const CLOCK_SKEW_LEEWAY_MS = 60 * 1000;

// ---------------------------------------------------------------------------
// JWT helpers (no atob in Hermes: manual base64url decoding)
// ---------------------------------------------------------------------------

const B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function decodeBase64Url(input: string): string {
  const normalized = input.replace(/-/g, "+").replace(/_/g, "/");
  const bytes: number[] = [];
  let buffer = 0;
  let bits = 0;
  for (const c of normalized) {
    if (c === "=") break;
    const idx = B64_CHARS.indexOf(c);
    if (idx === -1) throw new Error("invalid base64");
    buffer = (buffer << 6) | idx;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >> bits) & 0xff);
    }
  }
  if (typeof TextDecoder !== "undefined") {
    return new TextDecoder().decode(new Uint8Array(bytes));
  }
  return bytes.map((b) => String.fromCharCode(b)).join("");
}

export function parseJwtPayload(token: string): { exp?: number } | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    return JSON.parse(decodeBase64Url(payload)) as { exp?: number };
  } catch {
    return null;
  }
}

export function isTokenExpired(token: string): boolean {
  const payload = parseJwtPayload(token);
  if (!payload?.exp) return false;
  return payload.exp * 1000 < Date.now() + CLOCK_SKEW_LEEWAY_MS;
}

export function tokenSecondsLeft(token: string): number | null {
  const payload = parseJwtPayload(token);
  if (!payload?.exp) return null;
  return payload.exp - Date.now() / 1000;
}

// ---------------------------------------------------------------------------
// Auth change notifications (the auth provider subscribes to these)
// ---------------------------------------------------------------------------

type AuthListener = (token: string | null) => void;
const authListeners = new Set<AuthListener>();

export function onAuthChange(listener: AuthListener): () => void {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

function notifyAuthChange(): void {
  const token = getToken();
  for (const listener of authListeners) listener(token);
}

// ---------------------------------------------------------------------------
// In-memory cache backed by SecureStore
// ---------------------------------------------------------------------------

let tokenCache: string | null = null;
let refreshTokenCache: string | null = null;
let sessionCache: string | null = null;

async function safeGet(key: string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null): void {
  void (value === null
    ? SecureStore.deleteItemAsync(key)
    : SecureStore.setItemAsync(key, value)
  ).catch(() => {
    // Persistence failure: the in-memory cache still covers this app run.
  });
}

/** Load persisted values into memory. Call once at app startup. */
export async function loadStoredAuth(): Promise<string | null> {
  const [token, refreshToken, sessionId] = await Promise.all([
    safeGet(TOKEN_KEY),
    safeGet(REFRESH_TOKEN_KEY),
    safeGet(SESSION_KEY),
  ]);
  tokenCache = token && !isTokenExpired(token) ? token : null;
  if (token && !tokenCache) safeSet(TOKEN_KEY, null);
  refreshTokenCache = refreshToken || null;
  sessionCache = sessionId;
  return tokenCache;
}

export function getToken(): string | null {
  if (!tokenCache) return null;
  if (isTokenExpired(tokenCache)) {
    tokenCache = null;
    safeSet(TOKEN_KEY, null);
    notifyAuthChange();
    return null;
  }
  return tokenCache;
}

/** Raw token without the expiry check (refresh needs the expired value). */
export function getTokenUnchecked(): string | null {
  return tokenCache;
}

export function setToken(token: string | null): void {
  tokenCache = token;
  safeSet(TOKEN_KEY, token);
  notifyAuthChange();
}

export function clearToken(): void {
  setToken(null);
}

export function getRefreshToken(): string | null {
  return refreshTokenCache;
}

export function setRefreshToken(token: string | null): void {
  refreshTokenCache = token;
  safeSet(REFRESH_TOKEN_KEY, token);
}

export function clearRefreshToken(): void {
  setRefreshToken(null);
}

export function getSessionId(): string | null {
  return sessionCache;
}

export function setSessionId(sessionId: string | null): void {
  sessionCache = sessionId;
  safeSet(SESSION_KEY, sessionId);
}

/** Wipe all persisted auth state (logout / account deletion). */
export function clearAll(): void {
  setToken(null);
  setRefreshToken(null);
  setSessionId(null);
}
