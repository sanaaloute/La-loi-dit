"use client";

import { useEffect, useState } from "react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import { adminApi, type Tier, type TierBudgetsPatch, type TierBudgetsResponse } from "@/lib/api";
import { INPUT_CLASS, PRIMARY_BUTTON_CLASS, SectionCard, formatNumber } from "./ui";

const TIER_OPTIONS: { value: Tier; label: string }[] = [
  { value: "gratuit", label: "Gratuit" },
  { value: "pro", label: "Pro" },
  { value: "cabinet", label: "Cabinet" },
];

const BUDGET_FIELDS = [
  { key: "daily_token_budget", label: "Tokens par jour" },
  { key: "daily_request_budget", label: "Requêtes par jour" },
] as const;

type BudgetField = (typeof BUDGET_FIELDS)[number]["key"];
type Draft = Record<Tier, Record<BudgetField, string>>;

function toDraft(data: TierBudgetsResponse): Draft {
  const draft = {} as Draft;
  for (const { value: tier } of TIER_OPTIONS) {
    draft[tier] = {
      daily_token_budget: String(data.effective[tier].daily_token_budget),
      daily_request_budget: String(data.effective[tier].daily_request_budget),
    };
  }
  return draft;
}

export default function QuotasTab() {
  const [budgets, setBudgets] = useState<TierBudgetsResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.tierBudgets();
      setBudgets(data);
      setDraft(toDraft(data));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function save() {
    if (!budgets || !draft) return;
    setSaving(true);
    setActionError(null);
    setSaved(false);
    try {
      // Only send the fields that actually changed.
      const patch: TierBudgetsPatch = {};
      for (const { value: tier } of TIER_OPTIONS) {
        for (const { key } of BUDGET_FIELDS) {
          const raw = draft[tier][key].replace(/[\s ]/g, "");
          if (raw === String(budgets.effective[tier][key])) continue;
          const parsed = Number(raw);
          if (!Number.isInteger(parsed) || parsed <= 0) {
            throw new Error("Les quotas doivent être des nombres entiers positifs.");
          }
          patch[tier] = { ...patch[tier], [key]: parsed };
        }
      }
      const updated = Object.keys(patch).length > 0 ? await adminApi.patchTierBudgets(patch) : budgets;
      setBudgets(updated);
      setDraft(toDraft(updated));
      setSaved(true);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de l'enregistrement des quotas.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <LoadingState label="Chargement des quotas…" />;
  if (error || !budgets || !draft) {
    return (
      <ErrorCard message={error ?? "Une erreur est survenue."}>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
        >
          Réessayer
        </button>
      </ErrorCard>
    );
  }

  return (
    <div className="space-y-5">
      {actionError && <ErrorCard message={actionError} />}

      <SectionCard
        title="Quotas journaliers par offre"
        actions={
          <button type="button" onClick={() => void save()} disabled={saving} className={PRIMARY_BUTTON_CLASS}>
            {saving ? "Enregistrement…" : "Enregistrer"}
          </button>
        }
      >
        <div className="space-y-4">
          {TIER_OPTIONS.map(({ value: tier, label }) => (
            <div
              key={tier}
              className="grid grid-cols-1 gap-3 rounded-xl border border-gray-200 bg-gray-50 p-4 sm:grid-cols-3 sm:items-end"
            >
              <div>
                <p className="text-sm font-semibold text-gray-900">{label}</p>
                <p className="mt-0.5 text-[11px] text-gray-500">
                  Par défaut : {formatNumber(budgets.defaults[tier].daily_token_budget)} tokens,{" "}
                  {formatNumber(budgets.defaults[tier].daily_request_budget)} requêtes
                </p>
              </div>
              {BUDGET_FIELDS.map(({ key, label: fieldLabel }) => (
                <label key={key} className="block">
                  <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">
                    {fieldLabel}
                  </span>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={draft[tier][key]}
                    onChange={(e) =>
                      setDraft((prev) =>
                        prev ? { ...prev, [tier]: { ...prev[tier], [key]: e.target.value } } : prev,
                      )
                    }
                    disabled={saving}
                    aria-label={`${fieldLabel} — offre ${label}`}
                    className={INPUT_CLASS}
                  />
                </label>
              ))}
            </div>
          ))}
          <p className="text-[11px] text-gray-500">
            {saved
              ? "Quotas enregistrés — appliqués immédiatement à tous les utilisateurs."
              : "Les modifications sont appliquées immédiatement à tous les utilisateurs."}
          </p>
        </div>
      </SectionCard>
    </div>
  );
}
