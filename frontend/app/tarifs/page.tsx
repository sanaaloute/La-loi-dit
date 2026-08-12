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
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
        >
          Commencer gratuitement
        </Link>
      ) : (
        <Link
          href="/"
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
        >
          Se connecter pour souscrire
        </Link>
      );
    }
    const currentRank = TIER_RANK[profile?.tier ?? "gratuit"] ?? 0;
    const rank = TIER_RANK[card.id] ?? 0;
    if (rank === currentRank) {
      return (
        <span className="flex w-full cursor-default items-center justify-center gap-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2.5 text-sm font-medium text-accent">
          <Check className="h-4 w-4" />
          Votre offre
        </span>
      );
    }
    if (rank < currentRank) {
      return (
        <span className="flex w-full items-center justify-center rounded-lg px-3 py-2.5 text-xs text-gray-500">
          Inclus dans votre offre actuelle
        </span>
      );
    }
    if (config && !config.enabled) {
      return (
        <span className="flex w-full cursor-default items-center justify-center rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs font-medium text-gray-500">
          Bientôt disponible
        </span>
      );
    }
    return (
      <button
        type="button"
        onClick={() => void handleUpgrade(card.id as PaidTier)}
        disabled={checkoutBusy !== null}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
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
          <h1 className="mb-2 text-2xl font-semibold text-gray-900 sm:text-3xl">
            Des offres adaptées à <span className="text-accent">votre pratique</span>
          </h1>
          <p className="mx-auto max-w-xl text-sm text-gray-500">
            Recherche juridique fondée sur des sources vérifiées pour l&apos;Afrique de
            l&apos;Ouest (OHADA et droits nationaux). Changez d&apos;offre à tout moment.
          </p>
        </div>

        {banner === "success" && (
          <div className="mx-auto mb-6 flex max-w-2xl items-start justify-between gap-3 rounded-xl border border-accent/30 bg-accent/10 p-4">
            <p className="flex items-center gap-2 text-sm text-accent">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />
              Paiement confirmé — votre offre sera activée sous quelques instants.
            </p>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="rounded p-1 text-accent/70 hover:text-accent"
              aria-label="Fermer"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {banner === "canceled" && (
          <div className="mx-auto mb-6 flex max-w-2xl items-start justify-between gap-3 rounded-xl border border-warn-border/60 bg-warn-bg p-4">
            <p className="flex items-center gap-2 text-sm text-warn-text">
              <Info className="h-4 w-4 shrink-0 text-warn-text" />
              Paiement annulé — aucun montant n&apos;a été débité.
            </p>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="rounded p-1 text-warn-text/70 hover:text-warn-text"
              aria-label="Fermer"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {checkoutError && (
          <div className="mx-auto mb-6 max-w-2xl rounded-xl border border-red-700/30 bg-red-700/10 p-4 text-center">
            <p className="text-sm text-red-700">{checkoutError}</p>
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          {TIER_CARDS.map((card) => {
            const highlight = card.id === "pro";
            return (
              <div
                key={card.id}
                className={`relative flex flex-col rounded-xl border bg-white p-6 shadow-2xl backdrop-blur-xl ${
                  highlight
                    ? "border-accent/50"
                    : card.id === "cabinet"
                      ? "border-ink/40"
                      : "border-gray-200"
                }`}
              >
                {highlight && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full border border-accent/40 bg-white px-3 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
                    Populaire
                  </span>
                )}
                <h2 className="text-lg font-semibold text-gray-900">{card.label}</h2>
                <p className="mt-0.5 text-xs text-gray-500">{card.tagline}</p>
                <p className="mt-4 text-2xl font-semibold text-gray-900">{TIER_PRICES[card.id]}</p>
                <ul className="mb-6 mt-5 flex-1 space-y-2">
                  {card.features.map((feature) => (
                    <li key={feature} className="flex items-start gap-2 text-sm text-gray-600">
                      <Check
                        className={`mt-0.5 h-4 w-4 shrink-0 ${
                          card.id === "cabinet" ? "text-ink" : "text-accent"
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
