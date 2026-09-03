// Auth state: loads the persisted token at startup, exposes sign-in/out,
// and follows token invalidation (401 anywhere → back to the login screen).
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { deleteAccount as apiDeleteAccount, logout as apiLogout, me, type TokenResponse, type UserProfile } from "./api";
import { chatEngine } from "./chat";
import { unregisterPushToken } from "./push";
import { clearAll, loadStoredAuth, onAuthChange, setToken } from "./storage";

type AuthStatus = "loading" | "signedIn" | "signedOut";

interface AuthContextValue {
  status: AuthStatus;
  token: string | null;
  profile: UserProfile | null;
  /** Store the token of a fresh login/registration. */
  signIn: (response: TokenResponse) => Promise<void>;
  /** POST /auth/logout (best effort) + wipe local state. */
  signOut: () => Promise<void>;
  /** DELETE /auth/me + wipe local state. Throws on failure (e.g. 403). */
  deleteAccount: () => Promise<void>;
  /** Reload the profile from /auth/me. */
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [token, setTokenState] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadStoredAuth().then((stored) => {
      if (cancelled) return;
      setTokenState(stored);
      setStatus(stored ? "signedIn" : "signedOut");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // A 401 on any API call clears the stored token — mirror that here so the
  // router sends the user back to the login screen.
  useEffect(
    () =>
      onAuthChange((next) => {
        setTokenState(next);
        setStatus((prev) => {
          if (prev === "loading") return prev;
          return next ? "signedIn" : "signedOut";
        });
        if (!next) {
          setProfile(null);
          chatEngine.reset();
        }
      }),
    [],
  );

  const refreshProfile = useCallback(async () => {
    if (!token) return;
    try {
      setProfile(await me());
    } catch {
      // Profile unreadable (offline…): keep the previous one.
    }
  }, [token]);

  useEffect(() => {
    if (token) void refreshProfile();
  }, [token, refreshProfile]);

  const signIn = useCallback(async (response: TokenResponse) => {
    setToken(response.access_token);
  }, []);

  const signOut = useCallback(async () => {
    // Unregister first: the DELETE still needs the Bearer token.
    await unregisterPushToken();
    await apiLogout();
    clearAll();
    chatEngine.reset();
    setProfile(null);
  }, []);

  const deleteAccount = useCallback(async () => {
    await unregisterPushToken();
    await apiDeleteAccount();
    clearAll();
    chatEngine.reset();
    setProfile(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, token, profile, signIn, signOut, deleteAccount, refreshProfile }),
    [status, token, profile, signIn, signOut, deleteAccount, refreshProfile],
  );

  return React.createElement(AuthContext.Provider, { value }, children);
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth doit être utilisé dans un AuthProvider");
  return ctx;
}
