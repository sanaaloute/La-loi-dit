"use client";

import { Fragment, useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import { adminApi, listModels, type ModelInfo, type ProvidersResponse, type Tier } from "@/lib/api";
import { EmptyState, SectionCard, StatusBadge, TableShell, Td, Th, THead, formatCheckName, formatCheckValue } from "./ui";

const TIER_STYLES: Record<Tier, { label: string; className: string }> = {
  gratuit: { label: "Gratuit", className: "border-gray-300 bg-gray-100 text-gray-600" },
  pro: { label: "Pro", className: "border-accent/40 bg-accent/10 text-accent" },
  cabinet: { label: "Cabinet", className: "border-ink/40 bg-ink/10 text-ink" },
};

const TIER_ORDER: Tier[] = ["gratuit", "pro", "cabinet"];

export default function ProvidersTab() {
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(new Set());

  function toggleProvider(provider: string) {
    setExpandedProviders((prev) => {
      const next = new Set(prev);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [p, m] = await Promise.all([adminApi.providers(), listModels()]);
      setProviders(p);
      setModels(m.models);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (loading) return <LoadingState label="Chargement des fournisseurs…" />;
  if (error) {
    return (
      <ErrorCard message={error}>
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

  const infra = providers ? Object.entries(providers.infra) : [];

  return (
    <div className="space-y-5">
      {/* Providers */}
      <SectionCard title="Fournisseurs LLM et embeddings">
        {!providers || providers.providers.length === 0 ? (
          <EmptyState message="Aucun fournisseur configuré." />
        ) : (
          <TableShell>
            <THead>
              <tr>
                <Th>Fournisseur</Th>
                <Th>Statut</Th>
                <Th>Base API</Th>
                <Th>Clé</Th>
                <Th>Modèle</Th>
                <Th>Modèles</Th>
              </tr>
            </THead>
            <tbody>
              {providers.providers.map((p) => {
                const expanded = expandedProviders.has(p.provider);
                return (
                  <Fragment key={p.provider}>
                    <tr>
                      <Td className="font-medium text-gray-700">{p.provider}</Td>
                      <Td>
                        <span
                          className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                            p.configured
                              ? "border-accent/30 bg-accent/10 text-accent"
                              : "border-warn-border/60 bg-warn-bg text-warn-text"
                          }`}
                        >
                          {p.configured ? "Configuré" : "Non configuré"}
                        </span>
                      </Td>
                      <Td className="max-w-56 truncate font-mono text-xs" title={p.api_base}>
                        {p.api_base || "—"}
                      </Td>
                      <Td className="font-mono text-xs">{p.key_suffix ?? "—"}</Td>
                      <Td className="max-w-56 truncate font-mono text-xs" title={p.model}>
                        {p.model || "—"}
                      </Td>
                      <Td>
                        {p.models.length === 0 ? (
                          <span className="text-xs text-gray-500">—</span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => toggleProvider(p.provider)}
                            aria-expanded={expanded}
                            className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-gray-50 px-2 py-1 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100"
                          >
                            {p.models.length} modèle{p.models.length > 1 ? "s" : ""}
                            <ChevronDown
                              className={`h-3.5 w-3.5 text-gray-500 transition-transform ${expanded ? "rotate-180" : ""}`}
                            />
                          </button>
                        )}
                      </Td>
                    </tr>
                    {expanded && (
                      <tr>
                        <Td colSpan={6} className="bg-gray-50">
                          <div className="flex flex-wrap gap-1.5">
                            {p.models.map((m) => {
                              const badge = m.tier_required !== "gratuit" ? TIER_STYLES[m.tier_required] : null;
                              return (
                                <span
                                  key={m.id}
                                  title={m.id}
                                  className="inline-flex items-center gap-1.5 rounded-full border border-gray-300 bg-gray-50 px-2.5 py-1 text-xs text-gray-700"
                                >
                                  {m.label}
                                  {badge && (
                                    <span
                                      className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}
                                    >
                                      {badge.label}
                                    </span>
                                  )}
                                </span>
                              );
                            })}
                          </div>
                        </Td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </TableShell>
        )}
      </SectionCard>

      {/* Tier defaults */}
      <SectionCard title="Modèles par défaut par offre">
        {!providers || Object.keys(providers.defaults).length === 0 ? (
          <EmptyState message="Aucun modèle par défaut défini." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            {TIER_ORDER.map((tier) => {
              const style = TIER_STYLES[tier];
              const modelId = providers.defaults[tier];
              const model = models?.find((m) => m.id === modelId);
              return (
                <div
                  key={tier}
                  className="rounded-xl border border-gray-200 bg-gray-50 p-4"
                >
                  <span
                    className={`inline-block rounded-full border px-2.5 py-1 text-xs font-medium ${style.className}`}
                  >
                    {style.label}
                  </span>
                  <p className="mt-2 truncate text-sm font-medium text-gray-900" title={modelId}>
                    {model?.label ?? modelId ?? "—"}
                  </p>
                  {model && <p className="mt-0.5 truncate font-mono text-[11px] text-gray-500">{model.id}</p>}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* Infra checks */}
      <SectionCard title="Vérifications d'infrastructure">
        {infra.length === 0 ? (
          <EmptyState message="Aucune vérification d'infrastructure disponible." />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {infra.map(([name, value]) => {
              const text = typeof value === "string" ? value : JSON.stringify(value);
              return (
                <div
                  key={name}
                  className="flex items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5"
                >
                  <span className="min-w-0 truncate text-xs text-gray-600" title={name}>
                    {formatCheckName(name)}
                  </span>
                  <span title={text}>
                    <StatusBadge value={formatCheckValue(text)} />
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* Model catalog */}
      <SectionCard title={`Catalogue des modèles${models ? ` (${models.length})` : ""}`}>
        {!models || models.length === 0 ? (
          <EmptyState message="Aucun modèle dans le catalogue." />
        ) : (
          <TableShell>
            <THead>
              <tr>
                <Th>Libellé</Th>
                <Th>Identifiant</Th>
                <Th>Fournisseur</Th>
                <Th>Offre requise</Th>
              </tr>
            </THead>
            <tbody>
              {models.map((m) => {
                const style = TIER_STYLES[m.tier_required];
                return (
                  <tr key={m.id}>
                    <Td className="text-gray-700">{m.label}</Td>
                    <Td className="font-mono text-xs">{m.id}</Td>
                    <Td className="text-xs">{m.provider}</Td>
                    <Td>
                      <span
                        className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${style.className}`}
                      >
                        {style.label}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </TableShell>
        )}
      </SectionCard>
    </div>
  );
}
