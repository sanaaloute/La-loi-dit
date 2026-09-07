"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ExternalLink,
  FileText,
  Loader2,
  Newspaper,
  Search,
} from "lucide-react";
import AppHeader from "@/components/AppHeader";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import { useAuthToken } from "@/lib/useAuth";
import {
  ApiError,
  getArticle,
  listFreshnessEvents,
  listSourceArticles,
  listSources,
  searchCorpus,
  type ArticleIndexEntry,
  type ArticleLookupResponse,
  type EvidenceChunk,
  type FreshnessEvent,
  type SourceListItem,
} from "@/lib/api";
import { formatLongDate, relativeDate } from "@/lib/dates";

// ---------------------------------------------------------------------------
// Display vocabularies (same French labels as the admin ingestion form and
// the evidence viewer)
// ---------------------------------------------------------------------------

const FOLDER_ORDER = ["bf", "ohada", "uemoa", "cima"];

const FOLDER_LABELS: Record<string, string> = {
  bf: "Burkina Faso",
  ohada: "OHADA",
  uemoa: "UEMOA",
  cima: "CIMA",
};

const AUTHORITY_LABELS: Record<string, string> = {
  constitution: "Constitution",
  treaty_ohada: "Traité OHADA",
  law: "Loi",
  amended_law: "Loi modifiée",
  decree: "Décret",
  order: "Arrêté",
  ministerial_circular: "Circulaire",
  official_gazette: "Journal officiel",
  case_law: "Jurisprudence",
  official_press_release: "Communiqué officiel",
  official_news: "Actualité officielle",
  uploaded_document: "Document fourni",
  trusted_legal_site: "Site juridique",
  news: "Presse",
  blog: "Blog",
};

const DOC_TYPE_LABELS: Record<string, string> = {
  treaty: "Traité",
  code: "Code",
  ordinance: "Ordonnance",
  decree: "Décret",
  decision: "Décision",
  case_law: "Jurisprudence",
  law: "Loi",
  other: "Autre",
};

const SEARCH_DEBOUNCE_MS = 350;
const SEARCH_TOP_K = 10;
const FRESHNESS_LIMIT = 6;

/** publication_date can be date-only ("YYYY-MM-DD"): anchor it at noon UTC
 * so it never rolls back a day in negative-offset timezones. */
function formatPubDate(raw: string): string {
  return formatLongDate(/^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T12:00:00Z` : raw);
}

function Badge({ label }: { label: string }) {
  return (
    <span className="rounded-full border border-gray-300 bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-600">
      {label}
    </span>
  );
}

/**
 * The article lookup returns every chunk tagged with the article — the parent
 * (full text) AND its child excerpts (alinéas). Render only the "maximal"
 * chunks: a chunk whose content is contained in another returned chunk is a
 * child excerpt and would duplicate the parent's text.
 */
function maximalChunks(data: ArticleLookupResponse) {
  const chunks = data.chunks;
  return chunks.filter(
    (c, i) =>
      !chunks.some(
        (o, j) =>
          j !== i &&
          o.content.includes(c.content) &&
          (o.content.length > c.content.length || j < i),
      ),
  );
}

export default function ExplorerPage() {
  const [token] = useAuthToken();
  const [sources, setSources] = useState<SourceListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [events, setEvents] = useState<FreshnessEvent[] | null>(null);

  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<EvidenceChunk[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [selectedDoc, setSelectedDoc] = useState<SourceListItem | null>(null);
  const [articles, setArticles] = useState<ArticleIndexEntry[] | null>(null);
  const [articlesLoading, setArticlesLoading] = useState(false);
  const [articlesError, setArticlesError] = useState<string | null>(null);

  const [selectedArticle, setSelectedArticle] = useState<string | null>(null);
  const [articleData, setArticleData] = useState<ArticleLookupResponse | null>(null);
  const [articleLoading, setArticleLoading] = useState(false);
  const [articleError, setArticleError] = useState<string | null>(null);
  /** Search hit opened directly in the reader (chunk without article). */
  const [looseChunk, setLooseChunk] = useState<EvidenceChunk | null>(null);

  // Sources + freshness feed load once the session is known.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listSources(token)
      .then((items) => {
        if (!cancelled) setSources(items);
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Une erreur est survenue.");
        }
      });
    listFreshnessEvents(FRESHNESS_LIMIT, token)
      .then((rows) => {
        if (!cancelled) setEvents(rows);
      })
      .catch(() => {
        // Le fil de nouveautés est optionnel : la carte reste masquée.
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Debounced corpus search; an empty query restores the document list.
  useEffect(() => {
    const q = search.trim();
    if (!token || q.length === 0) {
      setSearchResults(null);
      setSearchError(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      searchCorpus(q, SEARCH_TOP_K, token)
        .then((res) => {
          setSearchResults(res.results);
          setSearchError(null);
        })
        .catch((err) => {
          setSearchResults(null);
          setSearchError(err instanceof Error ? err.message : "Échec de la recherche.");
        })
        .finally(() => setSearching(false));
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [search, token]);

  const grouped = useMemo(() => {
    const byFolder = new Map<string, SourceListItem[]>();
    for (const src of sources ?? []) {
      const list = byFolder.get(src.folder) ?? [];
      list.push(src);
      byFolder.set(src.folder, list);
    }
    const folders = [...byFolder.keys()].sort((a, b) => {
      const ia = FOLDER_ORDER.indexOf(a);
      const ib = FOLDER_ORDER.indexOf(b);
      if (ia !== -1 || ib !== -1) {
        return (ia === -1 ? FOLDER_ORDER.length : ia) - (ib === -1 ? FOLDER_ORDER.length : ib);
      }
      return a.localeCompare(b);
    });
    return folders.map((folder) => ({
      folder,
      label: FOLDER_LABELS[folder] ?? (folder ? folder.toUpperCase() : "Autres"),
      docs: byFolder.get(folder) ?? [],
    }));
  }, [sources]);

  function openDocument(doc: SourceListItem) {
    setSelectedDoc(doc);
    setSelectedArticle(null);
    setArticleData(null);
    setArticleError(null);
    setLooseChunk(null);
    setArticles(null);
    setArticlesError(null);
    setArticlesLoading(true);
    listSourceArticles(doc.document_id, token)
      .then((entries) => setArticles(entries))
      .catch((err) =>
        setArticlesError(err instanceof Error ? err.message : "Échec du chargement des articles."),
      )
      .finally(() => setArticlesLoading(false));
  }

  function openArticle(article: string) {
    if (!selectedDoc) return;
    setSelectedArticle(article);
    setLooseChunk(null);
    setArticleData(null);
    setArticleError(null);
    setArticleLoading(true);
    getArticle(selectedDoc.document_id, article, token)
      .then((data) => setArticleData(data))
      .catch((err) =>
        setArticleError(
          err instanceof ApiError && err.status === 404
            ? "Article introuvable dans le corpus."
            : err instanceof Error
              ? err.message
              : "Échec du chargement de l'article.",
        ),
      )
      .finally(() => setArticleLoading(false));
  }

  function openSearchHit(chunk: EvidenceChunk) {
    if (chunk.document_id) {
      const known = (sources ?? []).find((s) => s.document_id === chunk.document_id);
      const doc: SourceListItem =
        known ?? {
          document_id: chunk.document_id,
          document_name: chunk.document_name,
          version: chunk.version ?? 1,
          chunk_count: 0,
          folder: "",
          status: "",
          authority: typeof chunk.authority === "string" ? chunk.authority : "",
          document_type: "",
          law_number: "",
          publication_date: chunk.publication_date ?? "",
          legal_domains: [],
        };
      if (selectedDoc?.document_id !== doc.document_id) {
        openDocument(doc);
      }
      if (chunk.article) {
        setSelectedArticle(chunk.article);
        setLooseChunk(null);
        setArticleData(null);
        setArticleError(null);
        setArticleLoading(true);
        getArticle(doc.document_id, chunk.article, token)
          .then((data) => setArticleData(data))
          .catch(() => {
            // Article lookup failed: fall back to the chunk excerpt itself.
            setLooseChunk(chunk);
          })
          .finally(() => setArticleLoading(false));
        return;
      }
    }
    // No document or no article attached: read the chunk excerpt directly.
    setLooseChunk(chunk);
    setSelectedArticle(null);
    setArticleData(null);
    setArticleError(null);
  }

  // Mobile navigation: one pane at a time (documents → articles → reader);
  // all three columns are shown side by side from lg up.
  const view: "docs" | "articles" | "reader" = !selectedDoc
    ? "docs"
    : selectedArticle === null && looseChunk === null
      ? "articles"
      : "reader";

  return (
    <PageShell
      header={<AppHeader token={token} />}
      disclaimer="Textes indexés à titre informatif — seule la version publiée au Journal officiel fait foi."
    >
      {!token ? (
        <GatePanel body="L'exploration du corpus nécessite un compte. Connectez-vous depuis l'icône de compte en haut à droite." />
      ) : loadError ? (
        <ErrorCard message={loadError}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
          >
            Réessayer
          </button>
        </ErrorCard>
      ) : sources === null ? (
        <LoadingState label="Chargement du corpus…" />
      ) : (
        <div className="mx-auto grid h-full max-w-7xl gap-4 lg:grid-cols-[19rem_17rem_minmax(0,1fr)]">
          {/* Column 1 — search, nouveautés, documents */}
          <section
            className={`${view === "docs" ? "flex" : "hidden"} min-h-0 flex-col overflow-y-auto lg:flex`}
            aria-label="Documents du corpus"
          >
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Rechercher dans le corpus…"
                aria-label="Rechercher dans le corpus"
                className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-8 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:outline-none"
              />
              {searching && (
                <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-accent" />
              )}
            </div>

            {searchResults === null && events !== null && events.length > 0 && (
              <section className="mb-4 rounded-xl border border-gray-200 bg-gray-50 p-3">
                <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  <Newspaper className="h-3.5 w-3.5" />
                  Nouveautés
                </h2>
                <ul className="space-y-2.5">
                  {events.map((ev, i) => (
                    <li key={`${ev.source_name}-${i}`}>
                      <a
                        href={ev.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="group block"
                      >
                        <span className="flex items-start justify-between gap-2 text-xs font-medium text-gray-800">
                          <span className="min-w-0">{ev.source_name}</span>
                          <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-gray-400 transition-colors group-hover:text-accent" />
                        </span>
                        {ev.detail && (
                          <span className="mt-0.5 line-clamp-2 block text-[11px] text-gray-500">
                            {ev.detail}
                          </span>
                        )}
                        <span className="mt-0.5 block text-[10px] text-gray-400">
                          {relativeDate(ev.detected_at)}
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {searchError && (
              <p className="mb-3 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                {searchError}
              </p>
            )}

            {searchResults !== null ? (
              <div className="space-y-1.5">
                <p className="px-1 text-[11px] text-gray-500">
                  {searchResults.length} résultat{searchResults.length > 1 ? "s" : ""}
                </p>
                {searchResults.length === 0 ? (
                  <p className="px-1 py-6 text-center text-xs text-gray-500">
                    Aucun passage ne correspond à votre recherche.
                  </p>
                ) : (
                  searchResults.map((chunk) => (
                    <button
                      key={chunk.chunk_id}
                      type="button"
                      onClick={() => openSearchHit(chunk)}
                      className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-left transition-colors hover:border-accent/40 hover:bg-gray-50"
                    >
                      <span className="flex items-center gap-1.5">
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-800">
                          {chunk.document_name}
                        </span>
                        {chunk.article && <Badge label={`Art. ${chunk.article}`} />}
                      </span>
                      <span className="mt-1 line-clamp-2 block text-[11px] leading-relaxed text-gray-500">
                        {chunk.content}
                      </span>
                    </button>
                  ))
                )}
              </div>
            ) : (
              grouped.map((group) => (
                <section key={group.folder || "autres"} className="mb-5">
                  <h2 className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                    <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                    {group.label}
                  </h2>
                  <div className="space-y-1.5">
                    {group.docs.map((doc) => {
                      const active = selectedDoc?.document_id === doc.document_id;
                      return (
                        <button
                          key={doc.document_id}
                          type="button"
                          onClick={() => openDocument(doc)}
                          className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                            active
                              ? "border-accent/40 bg-accent/10"
                              : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50"
                          }`}
                        >
                          <span className="block text-xs font-medium leading-snug text-gray-800">
                            {doc.document_name}
                          </span>
                          {(doc.document_type || doc.authority) && (
                            <span className="mt-1.5 flex flex-wrap gap-1">
                              {doc.document_type && (
                                <Badge label={DOC_TYPE_LABELS[doc.document_type] ?? doc.document_type} />
                              )}
                              {doc.authority && (
                                <Badge label={AUTHORITY_LABELS[doc.authority] ?? doc.authority} />
                              )}
                            </span>
                          )}
                          {doc.publication_date && (
                            <span className="mt-1 block text-[10px] text-gray-400">
                              Publié le {formatPubDate(doc.publication_date)}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </section>
              ))
            )}
          </section>

          {/* Column 2 — article index of the selected document */}
          <section
            className={`${view === "articles" ? "flex" : "hidden"} min-h-0 flex-col overflow-y-auto lg:flex`}
            aria-label="Index des articles"
          >
            <button
              type="button"
              onClick={() => {
                setSelectedDoc(null);
                setSelectedArticle(null);
                setArticleData(null);
                setLooseChunk(null);
              }}
              className="mb-3 flex items-center gap-1.5 text-xs text-gray-500 transition-colors hover:text-gray-900 lg:hidden"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Tous les documents
            </button>
            {selectedDoc ? (
              <>
                <div className="mb-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5">
                  <p className="text-xs font-medium leading-snug text-gray-800">
                    {selectedDoc.document_name}
                  </p>
                  {selectedDoc.law_number && (
                    <p className="mt-0.5 text-[11px] text-gray-500">Loi n°{selectedDoc.law_number}</p>
                  )}
                </div>
                {articlesLoading ? (
                  <div className="flex items-center justify-center gap-2 py-8 text-xs text-gray-500">
                    <Loader2 className="h-4 w-4 animate-spin text-accent" />
                    Chargement des articles…
                  </div>
                ) : articlesError ? (
                  <p className="rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                    {articlesError}
                  </p>
                ) : !articles || articles.length === 0 ? (
                  <p className="px-1 py-6 text-center text-xs text-gray-500">
                    Aucun article indexé pour ce document.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {articles.map((entry) => {
                      const active = selectedArticle === entry.article;
                      return (
                        <button
                          key={entry.article}
                          type="button"
                          onClick={() => openArticle(entry.article)}
                          className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                            active
                              ? "border-accent/40 bg-accent/10"
                              : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50"
                          }`}
                        >
                          <span className="block text-xs font-semibold text-gray-900">
                            Article {entry.article}
                          </span>
                          {entry.section && (
                            <span className="mt-0.5 block truncate text-[10px] text-gray-400">
                              {entry.section}
                            </span>
                          )}
                          {entry.preview && (
                            <span className="mt-0.5 line-clamp-2 block text-[11px] leading-relaxed text-gray-500">
                              {entry.preview}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <p className="hidden px-2 py-10 text-center text-xs text-gray-400 lg:block">
                Sélectionnez un document pour parcourir ses articles.
              </p>
            )}
          </section>

          {/* Column 3 — reader */}
          <section
            className={`${view === "reader" ? "flex" : "hidden"} min-h-0 flex-col overflow-y-auto lg:flex`}
            aria-label="Texte de l'article"
          >
            {(selectedArticle !== null || looseChunk !== null) && (
              <button
                type="button"
                onClick={() => {
                  setSelectedArticle(null);
                  setArticleData(null);
                  setArticleError(null);
                  setLooseChunk(null);
                }}
                className="mb-3 flex items-center gap-1.5 text-xs text-gray-500 transition-colors hover:text-gray-900 lg:hidden"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Index des articles
              </button>
            )}
            {articleLoading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-xs text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
                Chargement du texte…
              </div>
            ) : articleError ? (
              <p className="rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                {articleError}
              </p>
            ) : articleData ? (
              <article className="rounded-xl border border-gray-200 bg-white p-5 shadow-panel">
                <p className="text-xs text-gray-500">{articleData.chunks[0]?.document_name}</p>
                <h2 className="mt-1 text-lg font-semibold text-gray-900">
                  Article {articleData.article}
                </h2>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {articleData.chunks[0]?.section && (
                    <Badge label={articleData.chunks[0].section} />
                  )}
                  {articleData.chunks[0]?.publication_date && (
                    <span className="text-[11px] text-gray-500">
                      Publié le {formatPubDate(articleData.chunks[0].publication_date)}
                    </span>
                  )}
                  {articleData.chunks[0]?.url && (
                    <a
                      href={articleData.chunks[0].url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                    >
                      <ExternalLink className="h-3 w-3" />
                      Source officielle
                    </a>
                  )}
                </div>
                <div className="mt-4 space-y-4 border-t border-gray-200 pt-4">
                  {maximalChunks(articleData).map((chunk) => (
                    <p
                      key={chunk.chunk_id}
                      className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800"
                    >
                      {chunk.content}
                    </p>
                  ))}
                </div>
              </article>
            ) : looseChunk ? (
              <article className="rounded-xl border border-gray-200 bg-white p-5 shadow-panel">
                <p className="text-xs text-gray-500">{looseChunk.document_name}</p>
                <h2 className="mt-1 text-lg font-semibold text-gray-900">
                  {looseChunk.article ? `Article ${looseChunk.article}` : "Extrait"}
                </h2>
                <p className="mt-4 whitespace-pre-wrap border-t border-gray-200 pt-4 text-sm leading-relaxed text-gray-800">
                  {looseChunk.content}
                </p>
              </article>
            ) : (
              <div className="hidden flex-1 flex-col items-center justify-center gap-3 text-center lg:flex">
                <FileText className="h-8 w-8 text-gray-300" />
                <p className="max-w-xs text-xs text-gray-400">
                  Sélectionnez un article pour lire le texte intégral.
                </p>
              </div>
            )}
          </section>
        </div>
      )}
    </PageShell>
  );
}
