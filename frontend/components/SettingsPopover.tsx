"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { X, Building2, Gauge, LogOut, UserCircle } from "lucide-react";
import { login, me, register, setToken, type Tier, type UserProfile } from "@/lib/api";

interface SettingsPopoverProps {
  token: string | null;
  onTokenChange: (token: string | null) => void;
  onClose: () => void;
}

type AuthMode = "login" | "register";

const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  gratuit: { label: "Gratuit", className: "border-slate-500/40 bg-slate-500/10 text-slate-300" },
  pro: { label: "Pro", className: "border-law-cyan/40 bg-law-cyan/10 text-law-cyan" },
  cabinet: { label: "Cabinet", className: "border-law-purple/40 bg-law-purple/10 text-law-purple" },
};

export default function SettingsPopover({ token, onTokenChange, onClose }: SettingsPopoverProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await login(username, password);
      setToken(res.access_token);
      onTokenChange(res.access_token);
      setPassword("");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de connexion");
    } finally {
      setBusy(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await register(email, password, name.trim() || undefined);
      setToken(res.access_token);
      onTokenChange(res.access_token);
      setPassword("");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de l'inscription");
    } finally {
      setBusy(false);
    }
  }

  function handleLogout() {
    setToken(null);
    onTokenChange(null);
    setUsername("");
    setEmail("");
    setName("");
    setProfile(null);
  }

  function switchMode(next: AuthMode) {
    setMode(next);
    setError(null);
  }

  const tierStyle = profile ? TIER_STYLES[profile.tier] : null;

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-2 w-80 rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-4 shadow-2xl backdrop-blur-xl"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Compte</h3>
        <button
          type="button"
          onClick={onClose}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-white"
          aria-label="Fermer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {token ? (
        <div className="space-y-4">
          {profile ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 text-xs">
                <span className="flex min-w-0 items-center gap-2 text-emerald-300">
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
                <p className="flex items-center gap-2 px-1 text-xs text-slate-400">
                  <Building2 className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{profile.workspace_name}</span>
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 text-xs text-emerald-300">
              <UserCircle className="h-4 w-4" />
              Un jeton est enregistré et joint à chaque requête.
            </div>
          )}
          <Link
            href="/compte"
            onClick={onClose}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-600/40 bg-slate-800/50 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-law-cyan/40 hover:bg-slate-700/50"
          >
            <Gauge className="h-4 w-4 text-law-cyan" />
            Mon usage
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300 transition-colors hover:bg-rose-500/20"
          >
            <LogOut className="h-4 w-4" />
            Se déconnecter
          </button>
        </div>
      ) : (
        <>
          <p className="mb-4 text-xs text-slate-400">
            En développement, l&apos;API accepte les appels anonymes. Un jeton JWT est facultatif.
          </p>
          <div className="mb-3 flex rounded-lg border border-slate-600/60 bg-slate-900/60 p-1">
            {(
              [
                { id: "login", label: "Se connecter" },
                { id: "register", label: "Créer un compte" },
              ] as { id: AuthMode; label: string }[]
            ).map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => switchMode(m.id)}
                className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
                  mode === m.id
                    ? "bg-slate-700/70 text-white"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          {mode === "login" ? (
            <form onSubmit={handleLogin} className="space-y-3">
              <div>
                <label htmlFor="settings-username" className="mb-1 block text-xs font-medium text-slate-300">
                  Nom d&apos;utilisateur
                </label>
                <input
                  id="settings-username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  className="w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none"
                  required
                />
              </div>
              <div>
                <label htmlFor="settings-password" className="mb-1 block text-xs font-medium text-slate-300">
                  Mot de passe
                </label>
                <input
                  id="settings-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  className="w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none"
                  required
                />
              </div>
              {error && <p className="text-xs text-rose-300">{error}</p>}
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-gradient-to-r from-law-cyan to-law-blue px-3 py-2 text-sm font-medium text-white shadow-glow-sm transition-all hover:shadow-glow disabled:opacity-50"
              >
                {busy ? "Connexion…" : "Obtenir un jeton"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-3">
              <div>
                <label htmlFor="settings-email" className="mb-1 block text-xs font-medium text-slate-300">
                  Adresse e-mail
                </label>
                <input
                  id="settings-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  className="w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none"
                  required
                />
              </div>
              <div>
                <label htmlFor="settings-name" className="mb-1 block text-xs font-medium text-slate-300">
                  Nom <span className="text-slate-500">(facultatif)</span>
                </label>
                <input
                  id="settings-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  className="w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none"
                />
              </div>
              <div>
                <label htmlFor="settings-new-password" className="mb-1 block text-xs font-medium text-slate-300">
                  Mot de passe
                </label>
                <input
                  id="settings-new-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  className="w-full rounded-lg border border-slate-600/60 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-law-cyan/60 focus:outline-none"
                  required
                />
              </div>
              {error && <p className="text-xs text-rose-300">{error}</p>}
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-gradient-to-r from-law-cyan to-law-blue px-3 py-2 text-sm font-medium text-white shadow-glow-sm transition-all hover:shadow-glow disabled:opacity-50"
              >
                {busy ? "Création…" : "Créer mon compte"}
              </button>
            </form>
          )}
        </>
      )}
    </div>
  );
}
