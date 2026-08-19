"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import { adminApi, type PromptSource, type UserPromptRecord, type UserPromptsResponse } from "@/lib/api";
import { EmptyState, INPUT_CLASS, PRIMARY_BUTTON_CLASS, SectionCard, TableShell, Td, Th, THead, formatDateTime } from "./ui";

const SOURCE_OPTIONS: { value: "" | PromptSource; label: string }[] = [
  { value: "", label: "Toutes les sources" },
  { value: "search", label: "Recherche" },
  { value: "chat", label: "Chat (POST)" },
  { value: "chat_stream", label: "Chat (SSE)" },
  { value: "ws_chat", label: "Chat (WebSocket)" },
];

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

interface Filters {
  q: string;
  source: "" | PromptSource;
  from: string;
  to: string;
}

export default function PromptsTab() {
  const [data, setData] = useState<UserPromptsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [filters, setFilters] = useState<Filters>({ q: "", source: "", from: "", to: "" });
  const [appliedFilters, setAppliedFilters] = useState<Filters>(filters);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await adminApi.prompts({
        q: appliedFilters.q || undefined,
        source: appliedFilters.source || undefined,
        from: appliedFilters.from || undefined,
        to: appliedFilters.to || undefined,
        page,
        page_size: pageSize,
      });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appliedFilters, page, pageSize]);

  function applyFilters(e: React.FormEvent) {
    e.preventDefault();
    setAppliedFilters(filters);
    setPage(1);
  }

  function resetFilters() {
    const empty: Filters = { q: "", source: "", from: "", to: "" };
    setFilters(empty);
    setAppliedFilters(empty);
    setPage(1);
  }

  const totalPages = useMemo(() => {
    if (!data) return 1;
    return Math.max(1, Math.ceil(data.total / data.page_size));
  }, [data]);

  return (
    <div className="space-y-5">
      <SectionCard title="Filtres">
        <form onSubmit={applyFilters} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="sm:col-span-2 lg:col-span-2">
            <label htmlFor="prompts-search" className="mb-1 block text-xs font-medium text-gray-600">
              Rechercher dans le texte ou l&apos;email
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                id="prompts-search"
                type="text"
                value={filters.q}
                onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
                placeholder="ex. code du travail"
                className={`${INPUT_CLASS} pl-9`}
              />
            </div>
          </div>
          <div>
            <label htmlFor="prompts-source" className="mb-1 block text-xs font-medium text-gray-600">
              Source
            </label>
            <select
              id="prompts-source"
              value={filters.source}
              onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value as PromptSource | "" }))}
              className={INPUT_CLASS}
            >
              {SOURCE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="prompts-from" className="mb-1 block text-xs font-medium text-gray-600">
              Du
            </label>
            <input
              id="prompts-from"
              type="date"
              value={filters.from}
              onChange={(e) => setFilters((f) => ({ ...f, from: e.target.value }))}
              className={INPUT_CLASS}
            />
          </div>
          <div>
            <label htmlFor="prompts-to" className="mb-1 block text-xs font-medium text-gray-600">
              Au
            </label>
            <input
              id="prompts-to"
              type="date"
              value={filters.to}
              onChange={(e) => setFilters((f) => ({ ...f, to: e.target.value }))}
              className={INPUT_CLASS}
            />
          </div>
          <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-5">
            <button type="submit" className={PRIMARY_BUTTON_CLASS}>
              <Search className="h-4 w-4" />
              Appliquer
            </button>
            <button type="button" onClick={resetFilters} className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50">
              Réinitialiser
            </button>
          </div>
        </form>
      </SectionCard>

      {error && (
        <ErrorCard message={error}>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
          >
            <RefreshCw className="h-4 w-4" />
            Réessayer
          </button>
        </ErrorCard>
      )}

      <SectionCard
        title={`Prompts utilisateurs${data ? ` (${data.total.toLocaleString("fr-FR")})` : ""}`}
        actions={
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-600 transition-colors hover:text-gray-900 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Actualiser
          </button>
        }
      >
        {loading && !data ? (
          <LoadingState label="Chargement des prompts…" />
        ) : !data || data.prompts.length === 0 ? (
          <EmptyState message="Aucun prompt enregistré pour le moment." />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-col items-center justify-between gap-3 text-xs text-gray-600 sm:flex-row">
              <span>
                Page {data.page} / {totalPages} — {data.prompts.length} ligne{data.prompts.length > 1 ? "s" : ""}
              </span>
              <div className="flex items-center gap-2">
                <span>Lignes par page</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setPage(1);
                  }}
                  className={INPUT_CLASS}
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <TableShell>
              <THead>
                <tr>
                  <Th>Date / Heure</Th>
                  <Th>Utilisateur</Th>
                  <Th>Source</Th>
                  <Th>Prompt</Th>
                </tr>
              </THead>
              <tbody>
                {data.prompts.map((p) => (
                  <PromptRow key={p.id} prompt={p} />
                ))}
              </tbody>
            </TableShell>

            <div className="flex items-center justify-between gap-2 text-xs text-gray-600">
              <button
                type="button"
                onClick={() => setPage((n) => Math.max(1, n - 1))}
                disabled={page <= 1 || loading}
                className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                  page <= 1 || loading
                    ? "border-gray-200 bg-gray-100 text-gray-400"
                    : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
                }`}
              >
                Précédent
              </button>
              <span>
                Page {data.page} / {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage((n) => Math.min(totalPages, n + 1))}
                disabled={page >= totalPages || loading}
                className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                  page >= totalPages || loading
                    ? "border-gray-200 bg-gray-100 text-gray-400"
                    : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
                }`}
              >
                Suivant
              </button>
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function PromptRow({ prompt }: { prompt: UserPromptRecord }) {
  const [expanded, setExpanded] = useState(false);
  const long = prompt.prompt.length > 160;
  const display = expanded || !long ? prompt.prompt : prompt.prompt.slice(0, 160) + "…";
  return (
    <tr className="align-top">
      <Td className="whitespace-nowrap text-xs">{formatDateTime(prompt.created_at)}</Td>
      <Td className="max-w-[200px] truncate" title={prompt.user_id}>
        <div className="truncate text-xs font-medium text-gray-900">{prompt.email || prompt.user_id}</div>
        {prompt.email && prompt.email !== prompt.user_id && (
          <div className="truncate text-[11px] text-gray-400">{prompt.user_id}</div>
        )}
      </Td>
      <Td>
        <SourceBadge source={prompt.source} />
      </Td>
      <Td className="max-w-md">
        <div className="whitespace-pre-wrap text-sm text-gray-700">{display}</div>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-1 text-[11px] font-medium text-accent hover:underline"
          >
            {expanded ? "Réduire" : "Voir plus"}
          </button>
        )}
      </Td>
    </tr>
  );
}

function SourceBadge({ source }: { source: string }) {
  const labels: Record<string, string> = {
    search: "Recherche",
    chat: "Chat",
    chat_stream: "Chat SSE",
    ws_chat: "Chat WS",
  };
  return (
    <span className="inline-flex items-center rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
      {labels[source] ?? source}
    </span>
  );
}
