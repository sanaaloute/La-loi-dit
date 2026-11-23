"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  BarChart3,
  Bell,
  ChevronRight,
  Clock,
  ExternalLink,
  FileText,
  FolderOpen,
  Gavel,
  Landmark,
  Lightbulb,
  Play,
  Scale,
  Search,
  Sparkles,
  Zap,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import FlagBf from "@/components/FlagBf";
import {
  listFreshnessEvents,
  listSessions,
  type ChatSessionSummary,
  type FreshnessEvent,
} from "@/lib/api";
import { relativeDate } from "@/lib/dates";
import { useAuthToken } from "@/lib/useAuth";
import { isLegalTextEvent } from "@/lib/veille";

const DOMAIN_CHIPS = [
  {
    label: "Droit du travail",
    icon: Scale,
    suggestion:
      "Quels sont les droits du travailleur en cas de licenciement au Burkina Faso ?",
  },
  {
    label: "Droit administratif",
    icon: Landmark,
    suggestion: "Quelle est la procédure pour contester une décision administrative ?",
  },
  {
    label: "Droit pénal",
    icon: Gavel,
    suggestion: "Quelles sont les peines encourues pour un vol au Burkina Faso ?",
  },
  {
    label: "Finances publiques",
    icon: BarChart3,
    suggestion: "Quelles sont les obligations fiscales d'une entreprise au Burkina Faso ?",
  },
];

const QUICK_ACTIONS = [
  { href: "/recherche", label: "Rechercher une loi", icon: Scale, chip: "bg-accent-light text-accent" },
  { href: "/explorer", label: "Consulter le Journal officiel", icon: FileText, chip: "bg-emerald-500/10 text-emerald-600" },
  { href: "/veille", label: "Suivre les nouvelles lois", icon: Bell, chip: "bg-violet-500/10 text-violet-600" },
  { href: "/dossiers", label: "Mes documents", icon: FolderOpen, chip: "bg-amber-500/10 text-amber-600" },
  { href: "/veille", label: "Tableau de bord de la veille", icon: BarChart3, chip: "bg-sky-500/10 text-sky-600" },
];

const OFFICIAL_SOURCES = [
  { name: "Journal Officiel du Burkina Faso", host: "jobf.gov.bf", url: "https://jobf.gov.bf", initials: "JO", tile: "bg-accent-light text-accent" },
  { name: "LégiBurkina", host: "legiburkina.gov.bf", url: "https://legiburkina.gov.bf", initials: "LB", tile: "bg-gold-light text-gold" },
  { name: "Assemblée législative du peuple", host: "assembleenationale.bf", url: "https://www.assembleenationale.bf", initials: "AL", tile: "bg-emerald-500/10 text-emerald-600" },
];

const TIP_QUERIES = [
  "Quelle est la procédure de création d'une entreprise au Burkina Faso ?",
  "Quelles sont les conditions de rupture du contrat de travail ?",
];

/** "19 nov. 2026, 14:32" — compact French date-time for the recents list. */
function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const date = d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return `${date}, ${time}`;
}

export default function HomePage() {
  const [token] = useAuthToken();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [events, setEvents] = useState<FreshnessEvent[]>([]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listSessions(token)
      .then((res) => {
        if (!cancelled) setSessions(res.sessions.slice(0, 3));
      })
      .catch(() => {});
    listFreshnessEvents(50, token)
      .then((rows) => {
        if (!cancelled) setEvents(rows.filter(isLegalTextEvent));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token]);

  const stats = useMemo(() => {
    const now = Date.now();
    const week = events.filter((e) => now - Date.parse(e.detected_at) < 7 * 24 * 3600 * 1000).length;
    const month = events.filter((e) => now - Date.parse(e.detected_at) < 30 * 24 * 3600 * 1000).length;
    return { week, month, sources: OFFICIAL_SOURCES.length };
  }, [events]);

  const latestEvent = events[0] ?? null;

  function submitSearch() {
    const q = query.trim();
    if (q) router.push(`/recherche?q=${encodeURIComponent(q)}`);
  }

  return (
    <AppShell disclaimer="Yawoto peut faire des erreurs — vérifiez toujours le texte officiel publié au Journal officiel.">
      <div className="mx-auto max-w-7xl">
        {/* Hero */}
        <section className="relative overflow-hidden rounded-3xl bg-sidebar px-6 py-10 text-paper sm:px-10 sm:py-12">
          {/* To use a photo backdrop (e.g. an official building), place it at
              frontend/public/hero.jpg and add an absolute inset-0 div with
              bg-[url('/hero.jpg')] bg-cover bg-center under this overlay. */}
          <div
            className="absolute inset-0 bg-gradient-to-r from-[#121C30] via-[#121C30]/90 to-[#1D3A5F]/60"
            aria-hidden="true"
          />
          <div
            className="absolute -right-16 -top-16 h-64 w-64 rounded-full bg-gold/10"
            aria-hidden="true"
          />
          <div
            className="absolute -bottom-20 right-40 h-48 w-48 rounded-full bg-paper/5"
            aria-hidden="true"
          />
          <FlagBf className="absolute right-6 top-6 h-8 w-12 rounded shadow-lg sm:right-10" />
          <div className="relative">
            <h1 className="font-serif text-4xl font-bold sm:text-5xl">Yawoto</h1>
            <p className="mt-2 font-serif text-xl font-semibold text-gold sm:text-2xl">
              Votre recherche juridique intelligente
            </p>
            <p className="mt-2 max-w-xl text-sm text-paper/80 sm:text-base">
              Accédez rapidement aux lois, décrets et textes officiels du Burkina Faso
            </p>

            {/* Search */}
            <div className="mx-auto mt-6 flex max-w-2xl items-center gap-2 rounded-2xl bg-white p-2 shadow-lg">
              <Search className="ml-2 h-5 w-5 shrink-0 text-gray-400" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitSearch();
                }}
                placeholder="Posez votre question en langage naturel..."
                aria-label="Posez votre question en langage naturel"
                className="ring-none-focus min-w-0 flex-1 bg-transparent text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none"
              />
              <button
                type="button"
                onClick={submitSearch}
                disabled={query.trim().length === 0}
                className="flex shrink-0 items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
              >
                <Zap className="h-4 w-4" />
                <span className="hidden sm:inline">Rechercher</span>
              </button>
            </div>

            {/* Domain chips */}
            <div className="mx-auto mt-4 flex max-w-2xl flex-wrap justify-center gap-2">
              {DOMAIN_CHIPS.map((chip) => (
                <Link
                  key={chip.label}
                  href={`/recherche?suggestion=${encodeURIComponent(chip.suggestion)}`}
                  className="flex items-center gap-2 rounded-full bg-paper/10 px-3.5 py-2 text-xs font-medium text-paper backdrop-blur transition-colors hover:bg-paper/20"
                >
                  <chip.icon className="h-3.5 w-3.5 text-gold" />
                  {chip.label}
                </Link>
              ))}
            </div>
          </div>
        </section>

        {/* Dashboard grid */}
        <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="space-y-6">
            {/* Quick actions */}
            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-panel">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                  <Sparkles className="h-4 w-4 text-gold" />
                  Actions rapides
                </h2>
                <Link
                  href="/recherche"
                  className="flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-hover"
                >
                  Plus d'options
                  <ChevronRight className="h-3.5 w-3.5" />
                </Link>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {QUICK_ACTIONS.map((action) => (
                  <Link
                    key={action.label}
                    href={action.href}
                    className="flex flex-col items-center gap-2.5 rounded-xl border border-gray-200 px-3 py-4 text-center transition-all hover:border-accent/40 hover:shadow-panel"
                  >
                    <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${action.chip}`}>
                      <action.icon className="h-5 w-5" />
                    </span>
                    <span className="text-xs font-medium leading-snug text-gray-700">{action.label}</span>
                  </Link>
                ))}
              </div>
            </section>

            {/* Recent searches */}
            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-panel">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                  <Clock className="h-4 w-4 text-gold" />
                  Recherches récentes
                </h2>
                <Link
                  href="/recherche?historique=1"
                  className="flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-hover"
                >
                  Voir tout
                  <ChevronRight className="h-3.5 w-3.5" />
                </Link>
              </div>
              {sessions.length === 0 ? (
                <p className="py-4 text-center text-xs text-gray-500">
                  Aucune recherche pour le moment — posez votre première question ci-dessus.
                </p>
              ) : (
                <ul className="divide-y divide-gray-100">
                  {sessions.map((session) => (
                    <li key={session.session_id}>
                      <Link
                        href={`/recherche?session=${encodeURIComponent(session.session_id)}`}
                        className="group flex items-center gap-3 py-3"
                      >
                        <Search className="h-4 w-4 shrink-0 text-gray-400" />
                        <span className="min-w-0 flex-1 truncate text-sm text-gray-800">
                          {session.title}
                        </span>
                        <span className="hidden shrink-0 text-xs text-gray-400 sm:block">
                          {formatDateTime(session.updated_at)}
                        </span>
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-gray-200 text-gray-400 transition-colors group-hover:border-accent group-hover:bg-accent group-hover:text-white">
                          <Play className="h-3 w-3" />
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <div className="space-y-6">
            {/* Veille juridique */}
            <section className="rounded-2xl bg-sidebar p-5 text-paper shadow-panel">
              <div className="mb-1 flex items-center justify-between gap-2">
                <h2 className="flex items-center gap-2 text-base font-semibold">
                  <Bell className="h-4 w-4 text-gold" />
                  Veille juridique
                </h2>
                <span className="rounded-full bg-gold px-2 py-0.5 text-[10px] font-semibold text-gray-900">
                  Nouveau
                </span>
              </div>
              <p className="text-xs text-sidebar-text">Restez informé des dernières publications</p>
              <div className="mt-4 grid grid-cols-3 divide-x divide-paper/10 text-center">
                <div>
                  <p className="text-2xl font-bold">{stats.week}</p>
                  <p className="mt-0.5 text-[10px] leading-tight text-sidebar-text">
                    Nouveaux textes (7 j)
                  </p>
                </div>
                <div>
                  <p className="text-2xl font-bold">{stats.month}</p>
                  <p className="mt-0.5 text-[10px] leading-tight text-sidebar-text">Ce mois-ci</p>
                </div>
                <div>
                  <p className="text-2xl font-bold">{stats.sources}</p>
                  <p className="mt-0.5 text-[10px] leading-tight text-sidebar-text">Sources suivies</p>
                </div>
              </div>
              {latestEvent ? (
                <Link
                  href="/veille"
                  className="mt-4 flex items-center gap-3 rounded-xl bg-gold-light p-3 text-gray-900 transition-colors hover:bg-gold-light/80"
                >
                  <FileText className="h-5 w-5 shrink-0 text-gold" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-semibold">
                      Nouvelle publication détectée
                    </span>
                    <span className="block truncate text-[11px] text-gray-600">
                      {latestEvent.source_name}
                      {latestEvent.detail ? ` — ${latestEvent.detail}` : ""}
                    </span>
                    <span className="block text-[10px] text-gray-500">
                      {relativeDate(latestEvent.detected_at)}
                    </span>
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-gray-500" />
                </Link>
              ) : (
                <p className="mt-4 rounded-xl bg-paper/5 p-3 text-center text-[11px] text-sidebar-text">
                  Aucune publication détectée récemment.
                </p>
              )}
            </section>

            {/* Official sources */}
            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-panel">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                  <Landmark className="h-4 w-4 text-gold" />
                  Sources officielles
                </h2>
                <span className="flex items-center gap-1.5 text-xs text-emerald-600">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  Actives
                </span>
              </div>
              <ul className="space-y-1">
                {OFFICIAL_SOURCES.map((source) => (
                  <li key={source.host}>
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group flex items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-gray-50"
                    >
                      <span
                        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold ${source.tile}`}
                      >
                        {source.initials}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-gray-900">
                          {source.name}
                        </span>
                        <span className="block text-xs text-gray-500">{source.host}</span>
                      </span>
                      <ExternalLink className="h-3.5 w-3.5 shrink-0 text-gray-300 transition-colors group-hover:text-accent" />
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>

        {/* Tip banner */}
        <section className="mt-6 flex flex-col items-start gap-3 rounded-2xl border border-accent/15 bg-accent-light p-4 sm:flex-row sm:items-center sm:p-5">
          <Lightbulb className="h-6 w-6 shrink-0 text-gold" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-gray-900">
              Astuce : interrogez Yawoto comme un expert juridique
            </p>
            <p className="mt-1 text-xs text-gray-600">
              {TIP_QUERIES.map((q, i) => (
                <span key={q}>
                  {i > 0 && <span className="mx-2 text-gray-400">|</span>}
                  <Link
                    href={`/recherche?q=${encodeURIComponent(q)}`}
                    className="text-accent hover:underline"
                  >
                    « {q} »
                  </Link>
                </span>
              ))}
            </p>
          </div>
          <Link
            href="/recherche"
            className="flex shrink-0 items-center gap-1.5 rounded-xl bg-accent px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-accent-hover"
          >
            Découvrir plus
            <ChevronRight className="h-3.5 w-3.5" />
          </Link>
        </section>
      </div>
    </AppShell>
  );
}
