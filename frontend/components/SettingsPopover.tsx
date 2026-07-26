"use client";

import { useEffect, useRef, useState } from "react";
import { X, LogOut, UserCircle } from "lucide-react";
import { login, setToken } from "@/lib/api";

interface SettingsPopoverProps {
  token: string | null;
  onTokenChange: (token: string | null) => void;
  onClose: () => void;
}

export default function SettingsPopover({ token, onTokenChange, onClose }: SettingsPopoverProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  function handleLogout() {
    setToken(null);
    onTokenChange(null);
    setUsername("");
  }

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-2 w-80 rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-4 shadow-2xl backdrop-blur-xl"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Connexion</h3>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-slate-400 hover:bg-white/5 hover:text-white"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <p className="mb-4 text-xs text-slate-400">
        En développement, l&apos;API accepte les appels anonymes. Un jeton JWT est facultatif.
      </p>
      {token ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 text-xs text-emerald-300">
            <UserCircle className="h-4 w-4" />
            Un jeton est enregistré et joint à chaque requête.
          </div>
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
      )}
    </div>
  );
}
