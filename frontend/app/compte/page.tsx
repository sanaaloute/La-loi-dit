"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Building2, LogOut, UserCircle } from "lucide-react";
import AppHeader from "@/components/AppHeader";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import { useAuthToken } from "@/lib/useAuth";
import {
  getSubscription,
  me,
  usageMe,
  type SubscriptionInfo,
  type SubscriptionStatus,
  type Tier,
  type UsageResponse,
  type UserProfile,
} from "@/lib/api";

const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  gratuit: { label: "Gratuit", className: "border-gray-300 bg-gray-100 text-gray-600" },
  pro: { label: "Pro", className: "border-accent/40 bg-accent/10 text-accent" },
  cabinet: { label: "Cabinet", className: "border-ink/40 bg-ink/10 text-ink" },
};

const STATUS_STYLES: Record<SubscriptionStatus, { label: string; className: string }> = {
  active: { label: "Actif", className: "border-accent/30 bg-accent/10 text-accent" },
  past_due: { label: "En retard de paiement", className: "border-warn-border/60 bg-warn-bg text-warn-text" },
  canceled: { label: "Résilié", className: "border-red-700/30 bg-red-700/10 text-red-700" },
  none: { label: "Aucun", className: "border-gray-300 bg-gray-100 text-gray-600" },
};

function formatNumber(n: number): string {
  return n.toLocaleString("fr-FR");
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}

export default function ComptePage() {
  const [token, setToken] = useAuthToken();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [subscription, setSubscription] = useState<SubscriptionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    getSubscription(token)
      .then((s) => {
        if (!cancelled) setSubscription(s);
      })
      .catch(() => {
        // Abonnement indisponible : la carte correspondante est simplement masquée.
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const tierStyle = profile ? TIER_STYLES[profile.tier] : null;
  const subTierStyle = subscription
    ? TIER_STYLES[subscription.tier as Tier] ?? null
    : null;
  const subStatusStyle = subscription ? STATUS_STYLES[subscription.status] : null;

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
                    {tierStyle && (
                      <span
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${tierStyle.className}`}
                      >
                        {tierStyle.label}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => setToken(null)}
                      className="flex items-center gap-1.5 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-700/20"
                    >
                      <LogOut className="h-3.5 w-3.5" />
                      Se déconnecter
                    </button>
                  </div>
                </div>
              </section>
            )}

            {/* Subscription card */}
            {subscription && (
              <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
                <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                  Abonnement
                </h2>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="space-y-2">
                    <p className="flex flex-wrap items-center gap-2">
                      {subTierStyle && (
                        <span
                          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${subTierStyle.className}`}
                        >
                          {subTierStyle.label}
                        </span>
                      )}
                      {subStatusStyle && (
                        <span
                          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${subStatusStyle.className}`}
                        >
                          {subStatusStyle.label}
                        </span>
                      )}
                    </p>
                    {subscription.current_period_end && (
                      <p className="text-xs text-gray-500">
                        {subscription.cancel_at_period_end
                          ? `Résiliation en fin de période — accès jusqu'au ${formatDate(subscription.current_period_end)}`
                          : `Renouvellement le ${formatDate(subscription.current_period_end)}`}
                      </p>
                    )}
                  </div>
                  <Link
                    href="/tarifs"
                    className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:border-accent/40 hover:bg-gray-100"
                  >
                    Voir les offres
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
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
                  {" / "}
                  {formatNumber(budget)} tokens
                </p>
                <p className="text-xs text-gray-500">
                  {formatNumber(usage.remaining_tokens)} restants — {formatNumber(usage.today.requests)}{" "}
                  requête{usage.today.requests > 1 ? "s" : ""} aujourd&apos;hui
                </p>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-gray-100">
                <div
                  className={`h-full rounded-full transition-all ${barClass}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {pct >= 100 && (
                <p className="mt-2 text-xs text-red-700">
                  Quota journalier atteint — passez à l&apos;offre supérieure pour continuer.
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
          </div>
        )}
    </PageShell>
  );
}
