"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Building2, UserCircle } from "lucide-react";
import AppHeader from "@/components/AppHeader";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import {
  getSubscription,
  getToken,
  me,
  usageMe,
  type SubscriptionInfo,
  type SubscriptionStatus,
  type Tier,
  type UsageResponse,
  type UserProfile,
} from "@/lib/api";

const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  gratuit: { label: "Gratuit", className: "border-slate-500/40 bg-slate-500/10 text-slate-300" },
  pro: { label: "Pro", className: "border-law-cyan/40 bg-law-cyan/10 text-law-cyan" },
  cabinet: { label: "Cabinet", className: "border-law-purple/40 bg-law-purple/10 text-law-purple" },
};

const STATUS_STYLES: Record<SubscriptionStatus, { label: string; className: string }> = {
  active: { label: "Actif", className: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300" },
  past_due: { label: "En retard de paiement", className: "border-amber-400/30 bg-amber-400/10 text-amber-300" },
  canceled: { label: "Résilié", className: "border-rose-400/30 bg-rose-400/10 text-rose-300" },
  none: { label: "Aucun", className: "border-slate-500/40 bg-slate-500/10 text-slate-300" },
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
  const [token, setToken] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [subscription, setSubscription] = useState<SubscriptionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setToken(getToken());
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
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
      ? "bg-rose-500"
      : pct > 80
        ? "bg-amber-400"
        : "bg-gradient-to-r from-law-cyan to-law-blue";

  // History comes most-recent-first; display ascending. Bar height = total tokens.
  const days = useMemo(() => [...(usage?.history ?? [])].reverse(), [usage]);
  const maxTokens = useMemo(
    () => Math.max(1, ...days.map((d) => d.tokens_in + d.tokens_out)),
    [days],
  );
  const labelEvery = Math.max(1, Math.ceil(days.length / 6));

  return (
    <PageShell
      header={<AppHeader token={token} onTokenChange={setToken} />}
      disclaimer="Les compteurs sont remis à zéro chaque jour à minuit (UTC)."
    >
      {!token ? (
        <GatePanel body="Connectez-vous depuis l'icône de compte en haut à droite pour consulter votre profil et votre usage." />
      ) : error ? (
        <ErrorCard message={error}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-600/60 bg-slate-800/60 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-700/60"
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
              <section className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <UserCircle className="h-9 w-9 shrink-0 text-slate-400" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-white">
                        {profile.name || profile.email}
                      </p>
                      {profile.name && (
                        <p className="truncate text-xs text-slate-400">{profile.email}</p>
                      )}
                      {profile.workspace_name && (
                        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-400">
                          <Building2 className="h-3 w-3 shrink-0" />
                          <span className="truncate">{profile.workspace_name}</span>
                        </p>
                      )}
                    </div>
                  </div>
                  {tierStyle && (
                    <span
                      className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${tierStyle.className}`}
                    >
                      {tierStyle.label}
                    </span>
                  )}
                </div>
              </section>
            )}

            {/* Subscription card */}
            {subscription && (
              <section className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
                <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-law-cyan" />
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
                      <p className="text-xs text-slate-400">
                        {subscription.cancel_at_period_end
                          ? `Résiliation en fin de période — accès jusqu'au ${formatDate(subscription.current_period_end)}`
                          : `Renouvellement le ${formatDate(subscription.current_period_end)}`}
                      </p>
                    )}
                  </div>
                  <Link
                    href="/tarifs"
                    className="flex items-center gap-1.5 rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-law-cyan/40 hover:bg-slate-700/60"
                  >
                    Voir les offres
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                </div>
              </section>
            )}

            {/* Today's usage */}
            <section className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                <span className="h-1.5 w-1.5 rounded-full bg-law-cyan" />
                Consommation du jour
              </h2>
              <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm text-slate-200">
                  <span className="text-lg font-semibold text-white">{formatNumber(consumed)}</span>
                  {" / "}
                  {formatNumber(budget)} tokens
                </p>
                <p className="text-xs text-slate-400">
                  {formatNumber(usage.remaining_tokens)} restants — {formatNumber(usage.today.requests)}{" "}
                  requête{usage.today.requests > 1 ? "s" : ""} aujourd&apos;hui
                </p>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
                <div
                  className={`h-full rounded-full transition-all ${barClass}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {pct >= 100 && (
                <p className="mt-2 text-xs text-rose-300">
                  Quota journalier atteint — passez à l&apos;offre supérieure pour continuer.
                </p>
              )}
            </section>

            {/* 30-day history */}
            <section className="rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                <span className="h-1.5 w-1.5 rounded-full bg-law-cyan" />
                Historique (30 jours)
              </h2>
              {days.length === 0 ? (
                <div className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-4 text-center">
                  <p className="text-xs text-slate-400">
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
                          className="min-w-1 flex-1 rounded-t bg-gradient-to-t from-law-cyan/60 to-law-blue/80 transition-colors hover:from-law-cyan hover:to-law-blue"
                          style={{ height: `${Math.max(2, (total / maxTokens) * 100)}%` }}
                        />
                      );
                    })}
                  </div>
                  <div className="mt-1 flex gap-1">
                    {days.map((d, i) => (
                      <div key={d.day} className="min-w-1 flex-1 text-center text-[9px] text-slate-500">
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
