"use client";

import { useState } from "react";
import { login, register, setToken } from "@/lib/api";

type AuthMode = "login" | "register";

interface AuthCardProps {
  /** Called after a successful login or registration. */
  onSuccess?: () => void;
  /** Prefix for input ids so several cards can coexist on a page. */
  idPrefix?: string;
}

/** Login/register form shared by the full-screen auth gate and the account popover. */
export default function AuthCard({ onSuccess, idPrefix = "auth" }: AuthCardProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await login(username, password);
      setToken(res.access_token);
      setPassword("");
      onSuccess?.();
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
      setPassword("");
      onSuccess?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de l'inscription");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: AuthMode) {
    setMode(next);
    setError(null);
  }

  const inputClass =
    "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:outline-none";
  const labelClass = "mb-1 block text-xs font-medium text-gray-600";

  return (
    <div>
      <div className="mb-3 flex rounded-lg border border-gray-300 bg-white p-1">
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
              mode === m.id ? "bg-gray-200 text-gray-900" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      {mode === "login" ? (
        <form onSubmit={handleLogin} className="space-y-3">
          <div>
            <label htmlFor={`${idPrefix}-username`} className={labelClass}>
              E-mail ou nom d&apos;utilisateur
            </label>
            <input
              id={`${idPrefix}-username`}
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              className={inputClass}
              required
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-password`} className={labelClass}>
              Mot de passe
            </label>
            <input
              id={`${idPrefix}-password`}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className={inputClass}
              required
            />
          </div>
          {error && <p className="text-xs text-red-700">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
          >
            {busy ? "Connexion…" : "Se connecter"}
          </button>
        </form>
      ) : (
        <form onSubmit={handleRegister} className="space-y-3">
          <div>
            <label htmlFor={`${idPrefix}-email`} className={labelClass}>
              Adresse e-mail
            </label>
            <input
              id={`${idPrefix}-email`}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              className={inputClass}
              required
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-name`} className={labelClass}>
              Nom <span className="text-gray-500">(facultatif)</span>
            </label>
            <input
              id={`${idPrefix}-name`}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-new-password`} className={labelClass}>
              Mot de passe
            </label>
            <input
              id={`${idPrefix}-new-password`}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className={inputClass}
              required
            />
          </div>
          {error && <p className="text-xs text-red-700">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
          >
            {busy ? "Création…" : "Créer mon compte"}
          </button>
        </form>
      )}
    </div>
  );
}
