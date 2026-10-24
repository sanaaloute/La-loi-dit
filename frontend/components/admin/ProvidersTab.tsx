"use client";

import { useEffect, useState } from "react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import { adminApi, type ProvidersResponse } from "@/lib/api";
import { EmptyState, SectionCard, StatusBadge, TableShell, Td, Th, THead, formatCheckName, formatCheckValue } from "./ui";

const MODEL_ROLES = [
  {
    model: "Qwen 2.5 7B",
    roles: "Classification, planification et tâches utilitaires structurées",
  },
  {
    model: "gpt-oss 20B",
    roles: "Analyse, synthèse, rédaction et mémoire",
  },
];

export default function ProvidersTab() {
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setProviders(await adminApi.providers());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (loading) return <LoadingState label="Chargement de l'infrastructure…" />;
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
      {/* Fixed role models */}
      <SectionCard title="Modèles fixes">
        <p className="mb-4 text-sm text-gray-500">
          La génération s'exécute sur notre fournisseur LLM privé Afrikia. Les attributions de rôles sont fixes.
        </p>
        <TableShell>
          <THead>
            <tr>
              <Th>Modèle</Th>
              <Th>Rôles</Th>
              <Th>Moteur</Th>
              <Th>Clé API</Th>
            </tr>
          </THead>
          <tbody>
            {MODEL_ROLES.map((entry) => (
              <tr key={entry.model}>
                <Td className="font-medium text-gray-700">{entry.model}</Td>
                <Td>{entry.roles}</Td>
                <Td>Afrikia (privé)</Td>
                <Td>Côté serveur</Td>
              </tr>
            ))}
          </tbody>
        </TableShell>
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
    </div>
  );
}
