"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, CheckCircle2, Info, Loader2, X } from "lucide-react";
import AppHeader from "@/components/AppHeader";
import PageShell from "@/components/ui/PageShell";
import {
  ApiError,
  billingConfig,
  createCheckout,
  getSubscription,
  getToken,
  me,
  type BillingConfig,
  type SubscriptionInfo,
  type Tier,
  type UserProfile,
} from "@/lib/api";

type PaidTier = "pro" | "cabinet";

// Montants affichés à titre indicatif : les prix réels sont configurés dans Paddle.
const TIER_PRICES: Record<Tier, string> = {
  gratuit: "0 FCFA",
  pro: "9 900 FCFA/mois",
  cabinet: "29 000 FCFA/mois",
};

interface TierCard {
  id: Tier;
  label: string;
  tagline: string;
  features: string[];
}

const TIER_CARDS: TierCard[] = [
  {
    id: "gratuit",
    label: "Gratuit",
    tagline: "Pour découvrir",
    features: [
      "Assistant juridique (questions-réponses)",
      "Modèles locaux",
      "20 000 tokens / jour",
      "Export Markdown",
    ],
  },
  {
    id: "pro",
    label: "Pro",
    tagline: "Populaire",
    features: [
      "Tout Gratuit, plus :",
      "Modèles cloud (OpenRouter)",
      "Rédaction de documents juridiques",
      "Export PDF / Word",
      "200 000 tokens / jour",
      "120 requêtes / min",
    ],
  },
  {
    id: "cabinet",
    label: "Cabinet",
    tagline: "Pour les cabinets",
    features: [
      "Tout Pro, plus :",
      "Meilleurs modèles (GPT-4o, Claude)",
      "Traitement prioritaire",
      "2 000 000 tokens / jour",
      "600 requêtes / min",
    ],
  },
];

const TIER_RANK: Record<string, number> = { gratuit: 0, pro: 1, cabinet: 2 };

export default function TarifsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [, setSubscription] = useState<SubscriptionInfo | null>(null);
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [checkoutBusy, setCheckoutBusy] = useState<PaidTier | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [banner, setBanner] = useState<"success" | "canceled" | null>(null);

  useEffect(() => {
    setToken(getToken());
    // Query params lus côté client (pas de useSearchParams : la page reste statique).
    const params = new URLSearchParams(window.location.search);
    if (params.get("success") === "1") setBanner("success");
    else if (params.get("canceled") === "1") setBanner("canceled");
  }, []);

  useEffect(() => {
    let cancelled = false;
    billingConfig()
      .then((c) => {
        if (!cancelled) setConfig(c);
      })
      .catch(() => {
        // Configuration indisponible : on laisse les boutons d'upgrade actifs.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    me(token)
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch(() => {});
    getSubscription(token)
      .then((s) => {
        if (!cancelled) setSubscription(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Après un paiement réussi, le webhook met à jour l'offre : on recharge le profil.
  useEffect(() => {
    if (banner !== "success" || !token) return;
    const timer = setTimeout(() => {
      me(token)
        .then((p) => setProfile(p))
        .catch(() => {});
    }, 3000);
    return () => clearTimeout(timer);
  }, [banner, token]);

  async function handleUpgrade(tier: PaidTier) {
    if (checkoutBusy) return;
    setCheckoutBusy(tier);
    setCheckoutError(null);
    try {
      const res = await createCheckout(tier, token);
      window.location.href = res.checkout_url;
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setCheckoutError("Le paiement en ligne n'est pas encore configuré. Réessayez plus tard.");
      } else {
        setCheckoutError(err instanceof Error ? err.message : "Échec de la création du paiement.");
      }
      setCheckoutBusy(null);
    }
  }

  function renderCta(card: TierCard) {
    if (!token) {
      return card.id === "gratuit" ? (
        <Link
          href="/"
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-2.5 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-700/60"
        >
          Commencer gratuitement
        </Link>
      ) : (
        <Link
          href="/"
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-law-cyan to-law-blue px-3 py-2.5 text-sm font-medium text-white shadow-glow-sm transition-all hover:shadow-glow"
        >
          Se connecter pour souscrire
        </Link>
      );
    }
    const currentRank = TIER_RANK[profile?.tier ?? "gratuit"] ?? 0;
    const rank = TIER_RANK[card.id] ?? 0;
    if (rank === currentRank) {
      return (
        <span className="flex w-full cursor-default items-center justify-center gap-2 rounded-lg border border-law-cyan/40 bg-law-cyan/10 px-3 py-2.5 text-sm font-medium text-law-cyan">
          <Check className="h-4 w-4" />
          Votre offre
        </span>
      );
    }
    if (rank < currentRank) {
      return (
        <span className="flex w-full items-center justify-center rounded-lg px-3 py-2.5 text-xs text-slate-500">
          Inclus dans votre offre actuelle
        </span>
      );
    }
    if (config && !config.enabled) {
      return (
        <span className="flex w-full cursor-default items-center justify-center rounded-lg border border-slate-600/40 bg-slate-800/40 px-3 py-2.5 text-xs font-medium text-slate-400">
          Bientôt disponible
        </span>
      );
    }
    return (
      <button
        type="button"
        onClick={() => void handleUpgrade(card.id as PaidTier)}
        disabled={checkoutBusy !== null}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-law-cyan to-law-blue px-3 py-2.5 text-sm font-medium text-white shadow-glow-sm transition-all hover:shadow-glow disabled:opacity-50"
      >
        {checkoutBusy === card.id ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          `Passer à ${card.label}`
        )}
      </button>
    );
  }

  return (
    <PageShell
      header={<AppHeader token={token} onTokenChange={setToken} />}
      disclaimer="Paiement sécurisé par Paddle — résiliable à tout moment."
    >
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 text-center">
          <h1 className="mb-2 text-2xl font-semibold text-white sm:text-3xl">
            Des offres adaptées à <span className="gradient-text">votre pratique</span>
          </h1>
          <p className="mx-auto max-w-xl text-sm text-slate-400">
            Recherche juridique fondée sur des sources vérifiées pour l&apos;Afrique de
            l&apos;Ouest (OHADA et droits nationaux). Changez d&apos;offre à tout moment.
          </p>
        </div>

        {banner === "success" && (
          <div className="mx-auto mb-6 flex max-w-2xl items-start justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
            <p className="flex items-center gap-2 text-sm text-emerald-200">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-300" />
              Paiement confirmé — votre offre sera activée sous quelques instants.
            </p>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="rounded p-1 text-emerald-300/70 hover:text-emerald-200"
              aria-label="Fermer"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {banner === "canceled" && (
          <div className="mx-auto mb-6 flex max-w-2xl items-start justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
            <p className="flex items-center gap-2 text-sm text-amber-200">
              <Info className="h-4 w-4 shrink-0 text-amber-300" />
              Paiement annulé — aucun montant n&apos;a été débité.
            </p>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="rounded p-1 text-amber-300/70 hover:text-amber-200"
              aria-label="Fermer"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {checkoutError && (
          <div className="mx-auto mb-6 max-w-2xl rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-center">
            <p className="text-sm text-rose-300">{checkoutError}</p>
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          {TIER_CARDS.map((card) => {
            const highlight = card.id === "pro";
            return (
              <div
                key={card.id}
                className={`relative flex flex-col rounded-xl border bg-[#0f172a]/95 p-6 shadow-2xl backdrop-blur-xl ${
                  highlight
                    ? "border-law-cyan/50 shadow-glow-sm"
                    : card.id === "cabinet"
                      ? "border-law-purple/40"
                      : "border-slate-600/40"
                }`}
              >
                {highlight && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full border border-law-cyan/40 bg-[#0f172a] px-3 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-law-cyan">
                    Populaire
                  </span>
                )}
                <h2 className="text-lg font-semibold text-white">{card.label}</h2>
                <p className="mt-0.5 text-xs text-slate-400">{card.tagline}</p>
                <p className="mt-4 text-2xl font-semibold text-white">{TIER_PRICES[card.id]}</p>
                <ul className="mb-6 mt-5 flex-1 space-y-2">
                  {card.features.map((feature) => (
                    <li key={feature} className="flex items-start gap-2 text-sm text-slate-300">
                      <Check
                        className={`mt-0.5 h-4 w-4 shrink-0 ${
                          card.id === "cabinet" ? "text-law-purple" : "text-law-cyan"
                        }`}
                      />
                      {feature}
                    </li>
                  ))}
                </ul>
                {renderCta(card)}
              </div>
            );
          })}
        </div>
      </div>
    </PageShell>
  );
}
