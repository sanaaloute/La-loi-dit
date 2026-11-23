"use client";

import { useEffect, useMemo, useState } from "react";
import { Bell, ExternalLink, FileText, Landmark } from "lucide-react";
import AppShell from "@/components/AppShell";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import { listFreshnessEvents, type FreshnessEvent } from "@/lib/api";
import { formatLongDate, relativeDate } from "@/lib/dates";
import { useAuthToken } from "@/lib/useAuth";
import { isLegalTextEvent } from "@/lib/veille";

const EVENTS_LIMIT = 100;

/** Groups events by calendar day, newest first; day keys are French labels. */
function groupByDay(events: FreshnessEvent[]): { label: string; items: FreshnessEvent[] }[] {
  const groups = new Map<string, FreshnessEvent[]>();
  for (const ev of events) {
    const d = new Date(ev.detected_at);
    const dayKey = Number.isNaN(d.getTime()) ? "Date inconnue" : d.toISOString().slice(0, 10);
    const list = groups.get(dayKey) ?? [];
    list.push(ev);
    groups.set(dayKey, list);
  }
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 10);
  return [...groups.entries()].map(([day, items]) => ({
    label:
      day === today
        ? "Aujourd'hui"
        : day === yesterday
          ? "Hier"
          : day === "Date inconnue"
            ? day
            : formatLongDate(`${day}T12:00:00Z`),
    items,
  }));
}

export default function VeillePage() {
  const [token] = useAuthToken();
  const [events, setEvents] = useState<FreshnessEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listFreshnessEvents(EVENTS_LIMIT, token)
      .then((rows) => {
        if (!cancelled) setEvents(rows.filter(isLegalTextEvent));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Une erreur est survenue.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const stats = useMemo(() => {
    const now = Date.now();
    const all = events ?? [];
    return {
      week: all.filter((e) => now - Date.parse(e.detected_at) < 7 * 24 * 3600 * 1000).length,
      month: all.filter((e) => now - Date.parse(e.detected_at) < 30 * 24 * 3600 * 1000).length,
      sources: new Set(all.map((e) => e.source_name)).size,
    };
  }, [events]);

  const groups = useMemo(() => groupByDay(events ?? []), [events]);

  return (
    <AppShell disclaimer="Veille basée sur les publications détectées sur les sources officielles — seule la version au Journal officiel fait foi.">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="flex items-center gap-2.5 font-serif text-2xl font-bold text-gray-900 sm:text-3xl">
            <Bell className="h-7 w-7 text-gold" />
            Veille juridique
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Les dernières publications détectées sur les sources officielles du Burkina Faso.
          </p>
        </header>

        {/* Stats */}
        <div className="mb-6 grid grid-cols-3 gap-3">
          {[
            { value: stats.week, label: "Nouveaux textes (7 j)" },
            { value: stats.month, label: "Ce mois-ci" },
            { value: stats.sources, label: "Sources détectées" },
          ].map((stat) => (
            <div
              key={stat.label}
              className="rounded-2xl border border-gray-200 bg-white p-4 text-center shadow-panel"
            >
              <p className="text-2xl font-bold text-accent">{stat.value}</p>
              <p className="mt-1 text-[11px] leading-tight text-gray-500">{stat.label}</p>
            </div>
          ))}
        </div>

        {error ? (
          <ErrorCard message={error}>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
            >
              Réessayer
            </button>
          </ErrorCard>
        ) : events === null ? (
          <LoadingState label="Chargement de la veille…" />
        ) : events.length === 0 ? (
          <div className="rounded-2xl border border-gray-200 bg-white p-10 text-center shadow-panel">
            <Landmark className="mx-auto h-8 w-8 text-gray-300" />
            <p className="mt-3 text-sm font-medium text-gray-700">Aucune publication détectée</p>
            <p className="mt-1 text-xs text-gray-500">
              Dès qu'une nouvelle publication est repérée sur le JOBF, LégiBurkina ou l'Assemblée,
              elle apparaît ici.
            </p>
          </div>
        ) : (
          groups.map((group) => (
            <section key={group.label} className="mb-6">
              <h2 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                {group.label}
              </h2>
              <ul className="space-y-2">
                {group.items.map((ev, i) => (
                  <li key={`${ev.source_name}-${ev.detected_at}-${i}`}>
                    <a
                      href={ev.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 transition-all hover:border-accent/40 hover:shadow-panel"
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-light text-accent">
                        <FileText className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-gray-900">
                          {ev.source_name}
                        </span>
                        {ev.detail && (
                          <span className="mt-0.5 block text-xs leading-relaxed text-gray-500">
                            {ev.detail}
                          </span>
                        )}
                        <span className="mt-1 block text-[11px] text-gray-400">
                          {relativeDate(ev.detected_at)}
                        </span>
                      </span>
                      <ExternalLink className="mt-1 h-3.5 w-3.5 shrink-0 text-gray-300 transition-colors group-hover:text-accent" />
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </AppShell>
  );
}
