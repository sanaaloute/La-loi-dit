"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { X, Building2, Gauge, LogOut, UserCircle } from "lucide-react";
import AuthCard from "@/components/AuthCard";
import { me, setToken, type Tier, type UserProfile } from "@/lib/api";

interface SettingsPopoverProps {
  token: string | null;
  onTokenChange: (token: string | null) => void;
  onClose: () => void;
}

const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  gratuit: { label: "Gratuit", className: "border-gray-300 bg-gray-100 text-gray-600" },
  pro: { label: "Pro", className: "border-accent/40 bg-accent/10 text-accent" },
  cabinet: { label: "Cabinet", className: "border-ink/40 bg-ink/10 text-ink" },
};

export default function SettingsPopover({ token, onTokenChange, onClose }: SettingsPopoverProps) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onClose]);

  useEffect(() => {
    setProfile(null);
    if (!token) return;
    let cancelled = false;
    me(token)
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch(() => {
        // Profil indisponible : on garde l'indication générique du jeton.
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  function handleLogout() {
    setToken(null);
    onTokenChange(null);
    setProfile(null);
  }

  const tierStyle = profile ? TIER_STYLES[profile.tier] : null;

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-2 w-80 rounded-xl border border-gray-200 bg-white p-4 shadow-2xl backdrop-blur-xl"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Compte</h3>
        <button
          type="button"
          onClick={onClose}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900"
          aria-label="Fermer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {token ? (
        <div className="space-y-4">
          {profile ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2 rounded-lg border border-accent/20 bg-accent/10 p-3 text-xs">
                <span className="flex min-w-0 items-center gap-2 text-accent">
                  <UserCircle className="h-4 w-4 shrink-0" />
                  <span className="truncate">{profile.email}</span>
                </span>
                {tierStyle && (
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${tierStyle.className}`}
                  >
                    {tierStyle.label}
                  </span>
                )}
              </div>
              {profile.workspace_name && (
                <p className="flex items-center gap-2 px-1 text-xs text-gray-500">
                  <Building2 className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{profile.workspace_name}</span>
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-accent/20 bg-accent/10 p-3 text-xs text-accent">
              <UserCircle className="h-4 w-4" />
              Un jeton est enregistré et joint à chaque requête.
            </div>
          )}
          <Link
            href="/compte"
            onClick={onClose}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 transition-colors hover:border-accent/40 hover:bg-gray-100"
          >
            <Gauge className="h-4 w-4 text-accent" />
            Mon usage
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-sm text-red-700 transition-colors hover:bg-red-700/20"
          >
            <LogOut className="h-4 w-4" />
            Se déconnecter
          </button>
        </div>
      ) : (
        <AuthCard idPrefix="settings" onSuccess={onClose} />
      )}
    </div>
  );
}
