"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  Landmark,
  Loader2,
  Search,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";

import { useAuthToken } from "@/lib/useAuth";
import {
  ApiError,
  getArticle,
  getSourceContent,
  listSourceArticles,
  listSources,
  searchCorpus,
  type ArticleIndexEntry,
  type ArticleLookupResponse,
  type EvidenceChunk,
  type SourceContent,
  type SourceListItem,
} from "@/lib/api";
import { formatLongDate } from "@/lib/dates";
import { displayDocName } from "@/lib/docNames";

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
  parliamentary_record: "Document parlementaire",
  other: "Autre",
};

const SEARCH_DEBOUNCE_MS = 350;
const SEARCH_TOP_K = 24;
/** Cards shown per category section in the "Tout" overview. */
const SECTION_PREVIEW = 8;
/** Cards per page when a single category is selected. */
const PAGE_SIZE = 24;

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

/** Document card used in the category grid and the section previews. */
function DocCard({
  doc,
  active,
  onOpen,
}: {
  doc: SourceListItem;
  active: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group flex flex-col rounded-xl border p-4 text-left transition-all ${
        active
          ? "border-accent/50 bg-accent-light"
          : "border-gray-200 bg-white hover:border-accent/40 hover:shadow-panel"
      }`}
    >
      <span className="mb-2 flex items-center justify-between gap-2">
        <FileText
          className={`h-4 w-4 shrink-0 transition-colors ${
            active ? "text-accent" : "text-gray-400 group-hover:text-accent"
          }`}
        />
        {doc.publication_date && (
          <span className="text-[10px] text-gray-400">{formatPubDate(doc.publication_date)}</span>
        )}
      </span>
      <span className="line-clamp-3 text-sm font-medium leading-snug text-gray-900">
        {displayDocName(doc.document_name)}
      </span>
      <span className="mt-auto flex flex-wrap items-center gap-1 pt-3">
        {doc.document_type && (
          <Badge label={DOC_TYPE_LABELS[doc.document_type] ?? doc.document_type} />
        )}
        {doc.authority && <Badge label={AUTHORITY_LABELS[doc.authority] ?? doc.authority} />}
      </span>
      {doc.law_number && (
        <span className="pt-1.5 text-[11px] text-gray-500">n° {doc.law_number}</span>
      )}
    </button>
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

  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<EvidenceChunk[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  /** "" = overview of every category; otherwise a folder key ("bf", …). */
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);

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
  /** Full text shown when the document has no article structure. */
  const [docContent, setDocContent] = useState<SourceContent | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  /** Mobile only: user tapped back from the full-text reader to the index. */
  const [contentDismissed, setContentDismissed] = useState(false);

  // Sources load once the session is known.
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
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Debounced corpus search; an empty query restores the document grid.
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
      label:
        FOLDER_LABELS[folder] ??
        (folder
          ? folder.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
          : "Autres"),
      docs: byFolder.get(folder) ?? [],
    }));
  }, [sources]);

  function selectCategory(next: string) {
    setCategory(next);
    setPage(1);
  }

  function loadArticle(docId: string, article: string) {
    setSelectedArticle(article);
    setLooseChunk(null);
    setArticleData(null);
    setArticleError(null);
    setArticleLoading(true);
    getArticle(docId, article, token)
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

  function openDocument(doc: SourceListItem) {
    setSelectedDoc(doc);
    setSelectedArticle(null);
    setArticleData(null);
    setArticleError(null);
    setLooseChunk(null);
    setArticles(null);
    setArticlesError(null);
    setDocContent(null);
    setContentLoading(false);
    setContentDismissed(false);
    setArticlesLoading(true);
    listSourceArticles(doc.document_id, token)
      .then((entries) => {
        setArticles(entries);
        if (entries.length > 0) {
          loadArticle(doc.document_id, entries[0].article);
        } else {
          // No article structure (parliamentary records, reports): show the
          // document's full text instead of a dead end.
          setContentLoading(true);
          getSourceContent(doc.document_id, token)
            .then((content) => setDocContent(content))
            .catch(() => setDocContent(null))
            .finally(() => setContentLoading(false));
        }
      })
      .catch((err) =>
        setArticlesError(err instanceof Error ? err.message : "Échec du chargement des articles."),
      )
      .finally(() => setArticlesLoading(false));
  }

  function backToDocuments() {
    setSelectedDoc(null);
    setSelectedArticle(null);
    setArticleData(null);
    setLooseChunk(null);
    setDocContent(null);
    setContentDismissed(false);
  }

  function openArticle(article: string) {
    if (!selectedDoc) return;
    loadArticle(selectedDoc.document_id, article);
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
  // articles and reader sit side by side from lg up.
  const mobilePane: "articles" | "reader" =
    selectedArticle === null && looseChunk === null && (docContent === null || contentDismissed)
      ? "articles"
      : "reader";

  const activeGroup = grouped.find((g) => g.folder === category) ?? null;
  const pageCount = activeGroup ? Math.max(1, Math.ceil(activeGroup.docs.length / PAGE_SIZE)) : 1;
  const pageDocs = activeGroup
    ? activeGroup.docs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    : [];

  return (
    <AppShell disclaimer="Textes indexés à titre informatif — seule la version publiée au Journal officiel fait foi.">
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
      ) : selectedDoc === null ? (
        <div className="mx-auto max-w-7xl">
          <header className="mb-6">
            <h1 className="flex items-center gap-2.5 font-serif text-2xl font-bold text-gray-900 sm:text-3xl">
              <Landmark className="h-7 w-7 text-gold" />
              Sources officielles
            </h1>
            <p className="mt-1 text-sm text-gray-500">
              Lois, décrets et textes de référence indexés à partir des publications officielles.
            </p>
          </header>

          {/* Search */}
          <div className="relative mb-4 max-w-xl">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Rechercher dans le corpus…"
              aria-label="Rechercher dans le corpus"
              className="w-full rounded-lg border border-gray-300 bg-white py-2.5 pl-9 pr-8 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:outline-none"
            />
            {searching && (
              <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-accent" />
            )}
          </div>

          {searchError && (
            <p className="mb-4 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
              {searchError}
            </p>
          )}

          {searchResults !== null ? (
            <>
              <p className="mb-3 text-xs text-gray-500">
                {searchResults.length} résultat{searchResults.length > 1 ? "s" : ""} pour «{" "}
                {search.trim()} »
              </p>
                {searchResults.length === 0 ? (
                <p className="py-10 text-center text-sm text-gray-500">
                  Aucun passage ne correspond à votre recherche.
                </p>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {searchResults.map((chunk) => (
                    <button
                      key={chunk.chunk_id}
                      type="button"
                      onClick={() => openSearchHit(chunk)}
                      className="rounded-xl border border-gray-200 bg-white p-4 text-left transition-all hover:border-accent/40 hover:shadow-panel"
                    >
                      <span className="flex items-center gap-1.5">
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-800">
                          {displayDocName(chunk.document_name)}
                        </span>
                        {chunk.article && <Badge label={`Art. ${chunk.article}`} />}
                      </span>
                      <span className="mt-2 line-clamp-3 block text-[11px] leading-relaxed text-gray-500">
                        {chunk.content}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              {/* Category chips */}
              <div className="mb-6 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => selectCategory("")}
                  className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                    category === ""
                      ? "bg-accent text-white"
                      : "border border-gray-300 bg-white text-gray-600 hover:border-gray-400 hover:text-gray-900"
                  }`}
                >
                  Tout
                </button>
                {grouped.map((g) => (
                  <button
                    key={g.folder || "autres"}
                    type="button"
                    onClick={() => selectCategory(g.folder)}
                    className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                      category === g.folder
                        ? "bg-accent text-white"
                        : "border border-gray-300 bg-white text-gray-600 hover:border-gray-400 hover:text-gray-900"
                    }`}
                  >
                    {g.label}
                    <span
                      className={`ml-1.5 ${category === g.folder ? "text-white/70" : "text-gray-400"}`}
                    >
                      {g.docs.length}
                    </span>
                  </button>
                ))}
              </div>

              {grouped.length === 0 ? (
                <p className="py-10 text-center text-sm text-gray-500">
                  Aucun document indexé pour le moment.
                </p>
              ) : category === "" ? (
                /* Overview: one section per category, preview cards + "voir tout". */
                grouped.map((group) => (
                  <section key={group.folder || "autres"} className="mb-8">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
                        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                        {group.label}
                        <span className="text-xs font-normal text-gray-400">
                          {group.docs.length} document{group.docs.length > 1 ? "s" : ""}
                        </span>
                      </h2>
                      {group.docs.length > SECTION_PREVIEW && (
                        <button
                          type="button"
                          onClick={() => selectCategory(group.folder)}
                          className="flex items-center gap-1 text-xs font-medium text-accent transition-colors hover:text-accent-hover"
                        >
                          Tout voir
                          <ChevronRight className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {group.docs.slice(0, SECTION_PREVIEW).map((doc) => (
                        <DocCard
                          key={doc.document_id}
                          doc={doc}
                          active={false}
                          onOpen={() => openDocument(doc)}
                        />
                      ))}
                    </div>
                  </section>
                ))
              ) : (
                /* Single category: full paginated grid. */
                activeGroup && (
                  <section>
                    <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-900">
                      <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                      {activeGroup.label}
                      <span className="text-xs font-normal text-gray-400">
                        {activeGroup.docs.length} document{activeGroup.docs.length > 1 ? "s" : ""}
                      </span>
                    </h2>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {pageDocs.map((doc) => (
                        <DocCard
                          key={doc.document_id}
                          doc={doc}
                          active={false}
                          onOpen={() => openDocument(doc)}
                        />
                      ))}
                    </div>
                    {pageCount > 1 && (
                      <div className="mt-6 flex items-center justify-center gap-3">
                        <button
                          type="button"
                          onClick={() => setPage((p) => Math.max(1, p - 1))}
                          disabled={page === 1}
                          className="flex h-9 w-9 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-600 transition-colors hover:bg-gray-100 disabled:opacity-40"
                          title="Page précédente"
                          aria-label="Page précédente"
                        >
                          <ChevronLeft className="h-4 w-4" />
                        </button>
                        <span className="text-xs text-gray-500">
                          Page {page} sur {pageCount}
                        </span>
                        <button
                          type="button"
                          onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                          disabled={page === pageCount}
                          className="flex h-9 w-9 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-600 transition-colors hover:bg-gray-100 disabled:opacity-40"
                          title="Page suivante"
                          aria-label="Page suivante"
                        >
                          <ChevronRight className="h-4 w-4" />
                        </button>
                      </div>
                    )}
                  </section>
                )
              )}
            </>
          )}
        </div>
      ) : (
        /* Reading view: article index + reader, side by side from lg up. */
        <div className="mx-auto grid h-full max-w-7xl gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
          {/* Articles index */}
          <section
            className={`${mobilePane === "articles" ? "flex" : "hidden"} min-h-0 flex-col overflow-y-auto lg:flex`}
            aria-label="Index des articles"
          >
            <button
              type="button"
              onClick={backToDocuments}
              className="mb-3 flex items-center gap-1.5 text-xs font-medium text-gray-500 transition-colors hover:text-accent"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Tous les documents
            </button>
            <div className="mb-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5">
              <p className="text-xs font-medium leading-snug text-gray-800">
                {displayDocName(selectedDoc.document_name)}
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
              contentLoading ? (
                <div className="flex items-center justify-center gap-2 py-8 text-xs text-gray-500">
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  Chargement du texte…
                </div>
              ) : docContent && docContent.blocks.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setContentDismissed(false)}
                  className="w-full rounded-lg border border-accent/40 bg-accent-light px-3 py-2 text-left"
                >
                  <span className="block text-xs font-semibold text-gray-900">Texte intégral</span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-gray-500">
                    Document non structuré en articles — affichage du texte dans le lecteur.
                  </span>
                </button>
              ) : (
                <p className="px-1 py-6 text-center text-xs text-gray-500">
                  Aucun article indexé pour ce document.
                </p>
              )
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
                          ? "border-accent/40 bg-accent-light"
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
          </section>

          {/* Reader */}
          <section
            className={`${mobilePane === "reader" ? "flex" : "hidden"} min-h-0 flex-col overflow-y-auto lg:flex`}
            aria-label="Texte de l'article"
          >
            {(selectedArticle !== null || looseChunk !== null || (docContent !== null && !contentDismissed)) && (
              <button
                type="button"
                onClick={() => {
                  setSelectedArticle(null);
                  setArticleData(null);
                  setArticleError(null);
                  setLooseChunk(null);
                  setContentDismissed(true);
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
                <p className="text-xs text-gray-500">
                  {displayDocName(articleData.chunks[0]?.document_name ?? "")}
                </p>
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
                <p className="text-xs text-gray-500">{displayDocName(looseChunk.document_name)}</p>
                <h2 className="mt-1 text-lg font-semibold text-gray-900">
                  {looseChunk.article ? `Article ${looseChunk.article}` : "Extrait"}
                </h2>
                <p className="mt-4 whitespace-pre-wrap border-t border-gray-200 pt-4 text-sm leading-relaxed text-gray-800">
                  {looseChunk.content}
                </p>
              </article>
            ) : docContent && docContent.blocks.length > 0 ? (
              <article className="rounded-xl border border-gray-200 bg-white p-5 shadow-panel">
                <p className="text-xs text-gray-500">
                  {displayDocName(selectedDoc?.document_name ?? "")}
                </p>
                <h2 className="mt-1 text-lg font-semibold text-gray-900">Texte intégral</h2>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge label="Document non structuré en articles" />
                  {selectedDoc?.publication_date && (
                    <span className="text-[11px] text-gray-500">
                      Publié le {formatPubDate(selectedDoc.publication_date)}
                    </span>
                  )}
                </div>
                <div className="mt-4 space-y-4 border-t border-gray-200 pt-4">
                  {docContent.blocks.map((block, i) => (
                    <p
                      key={i}
                      className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800"
                    >
                      {block.page !== null && (
                        <span className="mr-2 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-400">
                          p. {block.page}
                        </span>
                      )}
                      {block.text}
                    </p>
                  ))}
                </div>
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
    </AppShell>
  );
}
