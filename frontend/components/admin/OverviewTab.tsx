"use client";

import { useEffect, useState } from "react";
import { Activity, ArrowDownUp, FileCheck2, Hash, Server } from "lucide-react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import {
  ApiError,
  adminApi,
  ready,
  type AdminUsageResponse,
  type EvaluationLatestResponse,
  type ReadyResponse,
  type RetrievalAnalyticsResponse,
} from "@/lib/api";
import { EmptyState, SectionCard, StatCard, StatusBadge, TableShell, Td, Th, THead, formatCheckName, formatCheckValue, formatDateTime, formatNumber } from "./ui";

type EvalState = "loading" | "ok" | "none" | "error";

export default function OverviewTab() {
  const [readyData, setReadyData] = useState<ReadyResponse | null>(null);
  const [usage, setUsage] = useState<AdminUsageResponse | null>(null);
  const [analytics, setAnalytics] = useState<RetrievalAnalyticsResponse | null>(null);
  const [evalReport, setEvalReport] = useState<EvaluationLatestResponse | null>(null);
  const [evalState, setEvalState] = useState<EvalState>("loading");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    setEvalState("loading");
    try {
      const [r, u, a] = await Promise.all([ready(), adminApi.usage(30), adminApi.retrievalAnalytics()]);
      setReadyData(r);
      setUsage(u);
      setAnalytics(a);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
    // The eval report lives in its own request: 404 simply means none exists.
    try {
      const report = await adminApi.evaluationLatest();
      setEvalReport(report);
      setEvalState("ok");
    } catch (err) {
      setEvalReport(null);
      setEvalState(err instanceof ApiError && err.status === 404 ? "none" : "error");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) return <LoadingState label="Chargement de l'aperçu…" />;
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

  const checks = readyData ? Object.entries(readyData.checks) : [];
  const totals = usage?.totals ?? {};

  return (
    <div className="space-y-5">
      {/* Health */}
      <SectionCard title="Santé des services" actions={readyData && <StatusBadge value={readyData.status} />}>
        {checks.length === 0 ? (
          <EmptyState message="Aucune vérification disponible." />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {checks.map(([name, value]) => {
              const text = typeof value === "string" ? value : JSON.stringify(value);
              return (
                <div
                  key={name}
                  className="flex items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5"
                >
                  <span className="flex min-w-0 items-center gap-2 text-xs text-gray-600">
                    <Server className="h-3.5 w-3.5 shrink-0 text-accent" />
                    <span className="truncate" title={name}>
                      {formatCheckName(name)}
                    </span>
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

      {/* Usage totals (30 days) */}
      <SectionCard title="Consommation globale (30 jours)">
        <div className="grid gap-3 sm:grid-cols-3">
          <StatCard
            label="Tokens en entrée"
            value={formatNumber(totals.tokens_in ?? 0)}
            icon={ArrowDownUp}
          />
          <StatCard
            label="Tokens en sortie"
            value={formatNumber(totals.tokens_out ?? 0)}
            icon={ArrowDownUp}
          />
          <StatCard label="Requêtes" value={formatNumber(totals.requests ?? 0)} icon={Hash} />
        </div>
      </SectionCard>

      {/* Retrieval analytics */}
      <SectionCard title="Analytique des requêtes">
        {!analytics || analytics.total_requests === 0 ? (
          <EmptyState message="Aucune requête enregistrée dans le journal d'audit en mémoire." />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <StatCard
                label="Requêtes totales"
                value={formatNumber(analytics.total_requests)}
                hint="Journal d'audit en mémoire (processus courant)"
                icon={Activity}
              />
            </div>
            <TableShell>
              <THead>
                <tr>
                  <Th>Chemin</Th>
                  <Th>Requêtes</Th>
                  <Th>Erreurs</Th>
                  <Th>Latence moy.</Th>
                </tr>
              </THead>
              <tbody>
                {analytics.by_path.map((p) => (
                  <tr key={p.path}>
                    <Td className="font-mono text-xs">{p.path}</Td>
                    <Td>{formatNumber(p.requests)}</Td>
                    <Td className={p.errors > 0 ? "text-red-700" : ""}>{formatNumber(p.errors)}</Td>
                    <Td>{p.avg_latency_ms.toLocaleString("fr-FR")} ms</Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
            {analytics.by_user.length > 0 && (
              <TableShell>
                <THead>
                  <tr>
                    <Th>Utilisateur</Th>
                    <Th>Requêtes</Th>
                  </tr>
                </THead>
                <tbody>
                  {analytics.by_user.map((u) => (
                    <tr key={u.user}>
                      <Td className="font-mono text-xs">{u.user}</Td>
                      <Td>{formatNumber(u.requests)}</Td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            )}
          </div>
        )}
      </SectionCard>

      {/* Latest evaluation */}
      <SectionCard title="Dernière évaluation hors ligne">
        {evalState === "loading" ? (
          <LoadingState label="Chargement du rapport…" />
        ) : evalState === "ok" && evalReport ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <StatCard
              label="Cas évalués"
              value={evalReport.total_cases != null ? formatNumber(evalReport.total_cases) : "—"}
              icon={FileCheck2}
            />
            <StatCard
              label="Taux de réussite"
              value={
                evalReport.pass_rate != null
                  ? `${(evalReport.pass_rate * 100).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`
                  : "—"
              }
              icon={Activity}
            />
            <StatCard
              label="Généré le"
              value={formatDateTime(evalReport.generated_at)}
              hint={evalReport.dataset ?? undefined}
              icon={Hash}
            />
          </div>
        ) : (
          <EmptyState
            message={
              evalState === "none"
                ? "Aucun rapport d'évaluation disponible."
                : "Le rapport d'évaluation n'a pas pu être chargé."
            }
          />
        )}
      </SectionCard>
    </div>
  );
}
