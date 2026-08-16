"use client";

import { useEffect, useState } from "react";
import { Scale } from "lucide-react";
import AuthCard from "@/components/AuthCard";
import { useAuthToken } from "@/lib/useAuth";
import { ensureFreshToken } from "@/lib/api";

// How often an open session checks (and renews) its token.
const REFRESH_CHECK_MS = 60 * 1000;

/**
 * Blocks the whole application behind a centered login/register card until
 * the user is authenticated. While the app is open, the token is renewed
 * periodically so an active session does not expire.
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [token] = useAuthToken();
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setHydrated(true);
  }, []);

  // Keep the session alive: renew the token before it expires while the app
  // is open (e.g. idle tab), so the user is not kicked out mid-work.
  useEffect(() => {
    if (!token) return;
    const timer = window.setInterval(() => {
      void ensureFreshToken();
    }, REFRESH_CHECK_MS);
    return () => window.clearInterval(timer);
  }, [token]);

  // First paint matches the server render; resolve the stored token after
  // mount to avoid flashing the login card to authenticated users.
  if (!hydrated) {
    return (
      <div className="flex h-dvh items-center justify-center bg-gray-100">
        <Scale className="h-8 w-8 animate-pulse text-accent" />
      </div>
    );
  }

  if (!token) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-gray-100 px-4 py-10">
        <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-6 shadow-2xl backdrop-blur-xl sm:p-8">
          <div className="mb-6 flex flex-col items-center gap-3 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent">
              <Scale className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-gray-900">Yawoto</h1>
              <p className="text-sm text-gray-500">
                Connectez-vous ou créez un compte pour accéder à l&apos;assistant juridique.
              </p>
            </div>
          </div>
          <AuthCard idPrefix="gate" />
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
