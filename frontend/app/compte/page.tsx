"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Building2, Loader2, LogOut, Trash2, UserCircle } from "lucide-react";
import AppHeader from "@/components/AppHeader";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import { useAuthToken } from "@/lib/useAuth";
import {
  clearRefreshToken,
  deleteAccount,
  logout,
  me,
  usageMe,
  type UsageResponse,
  type UserProfile,
} from "@/lib/api";

function formatNumber(n: number): string {
  return n.toLocaleString("fr-FR");
}

export default function ComptePage() {
  const [token, setToken] = useAuthToken();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [deletingAccount, setDeletingAccount] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setProfile(null);
    setUsage(null);
    Promise.all([me(token), usageMe(token)])
      .then(([p, u]) => {
        if (cancelled) return;
        setProfile(p);
        setUsage(u);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Une erreur est survenue.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleDeleteAccount() {
    if (!token || deletingAccount) return;
    if (!window.confirm("Supprimer définitivement votre compte ? Vos conversations, marque-pages et préférences seront effacés.")) {
      return;
    }
    if (!window.confirm("Cette action est irréversible. Confirmez-vous la suppression de votre compte ?")) {
      return;
    }
    setDeletingAccount(true);
    try {
      await deleteAccount(token);
      // The account is gone server-side: drop the local token, the auth gate
      // then shows the login card.
      setToken(null);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Échec de la suppression du compte.");
      setDeletingAccount(false);
    }
  }

  const consumed = usage ? usage.today.tokens_in + usage.today.tokens_out : 0;
  const budget = usage?.daily_budget ?? 0;
  const pct = budget > 0 ? Math.min(100, (consumed / budget) * 100) : 0;
  const barClass =
    pct >= 100
      ? "bg-red-700"
      : pct > 80
        ? "bg-warn-border"
        : "bg-accent";

  // History comes most-recent-first; display ascending. Bar height = total tokens.
  const days = useMemo(() => [...(usage?.history ?? [])].reverse(), [usage]);
  const maxTokens = useMemo(
    () => Math.max(1, ...days.map((d) => d.tokens_in + d.tokens_out)),
    [days],
  );
  const labelEvery = Math.max(1, Math.ceil(days.length / 6));

  return (
    <PageShell
      header={<AppHeader token={token} />}
      disclaimer="Les compteurs sont remis à zéro chaque jour à minuit (UTC)."
    >
      {!token ? (
        <GatePanel body="Connectez-vous pour consulter votre profil et votre usage." />
      ) : error ? (
        <ErrorCard message={error}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
          >
            Réessayer
          </button>
        </ErrorCard>
      ) : loading || !usage ? (
        <LoadingState label="Chargement de votre compte…" />
      ) : (
          <div className="mx-auto max-w-3xl space-y-5">
            {/* Profile card */}
            {profile && (
              <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <UserCircle className="h-9 w-9 shrink-0 text-gray-500" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-gray-900">
                        {profile.name || profile.email || profile.phone}
                      </p>
                      {profile.name && (profile.email || profile.phone) && (
                        <p className="truncate text-xs text-gray-500">{profile.email || profile.phone}</p>
                      )}
                      {profile.workspace_name && (
                        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-gray-500">
                          <Building2 className="h-3 w-3 shrink-0" />
                          <span className="truncate">{profile.workspace_name}</span>
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        // Revoke the server-side session (best effort), then
                        // discard the local tokens: the gate shows the login.
                        void logout(token).catch(() => {});
                        setToken(null);
                        clearRefreshToken();
                      }}
                      className="flex items-center gap-1.5 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-700/20"
                    >
                      <LogOut className="h-3.5 w-3.5" />
                      Se déconnecter
                    </button>
                  </div>
                </div>
              </section>
            )}

            {/* Today's usage */}
            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                Consommation du jour
              </h2>
              <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm text-gray-700">
                  <span className="text-lg font-semibold text-gray-900">{formatNumber(consumed)}</span>
                  {budget > 0 ? ` / ${formatNumber(budget)} tokens` : " tokens (illimité)"}
                </p>
                <p className="text-xs text-gray-500">
                  {budget > 0 ? `${formatNumber(usage.remaining_tokens)} tokens restants — ` : ""}
                  {formatNumber(usage.today.requests)}
                  {usage.daily_request_budget > 0
                    ? ` / ${formatNumber(usage.daily_request_budget)} requêtes`
                    : " requêtes (illimitées)"}
                  {usage.daily_request_budget > 0
                    ? ` — ${formatNumber(usage.remaining_requests)} restantes`
                    : ""}
                </p>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-gray-100">
                <div
                  className={`h-full rounded-full transition-all ${barClass}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {(pct >= 100 ||
                (usage.daily_request_budget > 0 && usage.today.requests >= usage.daily_request_budget)) && (
                <p className="mt-2 text-xs text-red-700">
                  Quota journalier atteint — réessayez demain.
                </p>
              )}
            </section>

            {/* 30-day history */}
            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                Historique (30 jours)
              </h2>
              {days.length === 0 ? (
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-center">
                  <p className="text-xs text-gray-500">
                    Aucune activité enregistrée sur les 30 derniers jours.
                  </p>
                </div>
              ) : (
                <div>
                  <div className="flex h-32 items-end gap-1">
                    {days.map((d) => {
                      const total = d.tokens_in + d.tokens_out;
                      return (
                        <div
                          key={d.day}
                          title={`${d.day} : ${formatNumber(total)} tokens, ${formatNumber(d.requests)} requête${d.requests > 1 ? "s" : ""}`}
                          className="min-w-1 flex-1 rounded-t bg-accent/50 transition-colors hover:bg-accent"
                          style={{ height: `${Math.max(2, (total / maxTokens) * 100)}%` }}
                        />
                      );
                    })}
                  </div>
                  <div className="mt-1 flex gap-1">
                    {days.map((d, i) => (
                      <div key={d.day} className="min-w-1 flex-1 text-center text-[9px] text-gray-500">
                        {i % labelEvery === 0 ? `${d.day.slice(8, 10)}/${d.day.slice(5, 7)}` : ""}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>

            {/* Danger zone */}
            <section className="rounded-xl border border-red-700/30 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-red-700">
                <AlertTriangle className="h-3.5 w-3.5" />
                Zone sensible
              </h2>
              <p className="mb-3 text-xs text-gray-500">
                La suppression de votre compte est définitive : profil, conversations, marque-pages
                et mémoire de l&apos;assistant sont effacés.
              </p>
              <button
                type="button"
                onClick={() => void handleDeleteAccount()}
                disabled={deletingAccount}
                className="flex items-center gap-1.5 rounded-lg bg-red-700 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-red-800 disabled:opacity-50"
              >
                {deletingAccount ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                Supprimer mon compte
              </button>
            </section>
          </div>
        )}
    </PageShell>
  );
}
