"use client";

import { useCallback, useEffect, useState } from "react";
import { AUTH_CHANGE_EVENT, getToken, setToken as apiSetToken } from "./api";

/**
 * Keeps a React state in sync with the persisted JWT.
 *
 * - Reads the token from localStorage on mount (and filters out expired ones).
 * - Listens for `AUTH_CHANGE_EVENT` so that 401 responses or logins in other
 *   components update this hook automatically.
 * - The returned setter persists the token and broadcasts the change.
 */
export function useAuthToken(): [string | null, (token: string | null) => void] {
  const [token, setTokenState] = useState<string | null>(null);

  useEffect(() => {
    setTokenState(getToken());
  }, []);

  useEffect(() => {
    function handleChange() {
      setTokenState(getToken());
    }
    window.addEventListener(AUTH_CHANGE_EVENT, handleChange);
    return () => window.removeEventListener(AUTH_CHANGE_EVENT, handleChange);
  }, []);

  const setToken = useCallback((next: string | null) => {
    apiSetToken(next);
  }, []);

  return [token, setToken];
}
