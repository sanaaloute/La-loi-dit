"use client";

import { useEffect, useState } from "react";
import { FolderPlus, Loader2, Sparkles, Trash2, Upload } from "lucide-react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import {
  adminApi,
  type DocumentIngestResult,
  type DocumentMetadata,
  type FolderInfo,
  type IngestionStatusResponse,
  type MetadataSuggestion,
} from "@/lib/api";
import { EmptyState, INPUT_CLASS, PRIMARY_BUTTON_CLASS, SECONDARY_BUTTON_CLASS, SectionCard, StatusBadge, TableShell, Td, Th, THead, formatDateTime, formatNumber } from "./ui";

const ACCEPTED_EXTENSIONS = ".pdf,.txt,.md,.markdown,.html,.htm";

const AUTHORITY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "—" },
  { value: "constitution", label: "Constitution" },
  { value: "treaty_ohada", label: "Traité OHADA" },
  { value: "law", label: "Loi" },
  { value: "decree", label: "Décret" },
  { value: "order", label: "Arrêté" },
  { value: "ministerial_circular", label: "Circulaire ministérielle" },
  { value: "official_gazette", label: "Journal officiel" },
  { value: "case_law", label: "Jurisprudence" },
  { value: "official_press_release", label: "Communiqué officiel" },
  { value: "unknown", label: "Inconnu" },
];

const DOCUMENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "—" },
  { value: "treaty", label: "Traité" },
  { value: "code", label: "Code" },
  { value: "ordinance", label: "Ordonnance" },
  { value: "decree", label: "Décret" },
  { value: "decision", label: "Décision" },
  { value: "case_law", label: "Jurisprudence" },
  { value: "law", label: "Loi" },
  { value: "other", label: "Autre" },
];

const EMPTY_SUGGESTION: MetadataSuggestion = {
  document_name: "",
  authority: "",
  document_type: "",
  law_number: "",
  legal_domains: [],
  publication_date: "",
  effective_date: "",
  government_body: "",
  url: "",
};

/** Keep only the non-empty fields of the edited metadata form. */
function compactMetadata(s: MetadataSuggestion): DocumentMetadata {
  const meta: DocumentMetadata = {};
  if (s.document_name.trim()) meta.document_name = s.document_name.trim();
  if (s.authority) meta.authority = s.authority;
  if (s.document_type) meta.document_type = s.document_type;
  if (s.law_number.trim()) meta.law_number = s.law_number.trim();
  if (s.legal_domains.length > 0) meta.legal_domains = s.legal_domains;
  if (s.publication_date) meta.publication_date = s.publication_date;
  if (s.effective_date) meta.effective_date = s.effective_date;
  if (s.government_body.trim()) meta.government_body = s.government_body.trim();
  if (s.url.trim()) meta.url = s.url.trim();
  return meta;
}

export default function DocumentsTab() {
  const [status, setStatus] = useState<IngestionStatusResponse | null>(null);
  const [folders, setFolders] = useState<FolderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Upload flow
  const [file, setFile] = useState<File | null>(null);
  const [folder, setFolder] = useState("");
  const [newFolder, setNewFolder] = useState("");
  const [suggestion, setSuggestion] = useState<MetadataSuggestion | null>(null);
  const [availableDomains, setAvailableDomains] = useState<string[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<DocumentIngestResult | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [s, f] = await Promise.all([adminApi.ingestionStatus(), adminApi.folders()]);
      setStatus(s);
      setFolders(f.folders);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  /** Refresh only the ingestion table (after upload/delete); keeps the form. */
  async function refreshStatus() {
    try {
      setStatus(await adminApi.ingestionStatus());
    } catch {
      // Keep the previous table; the next explicit refresh will retry.
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    setFile(e.target.files?.[0] ?? null);
    // A new file invalidates the previous suggestion and result.
    setSuggestion(null);
    setResult(null);
    setActionError(null);
  }

  async function handleCreateFolder(e: React.FormEvent) {
    e.preventDefault();
    const name = newFolder.trim();
    if (!name) return;
    setCreatingFolder(true);
    setActionError(null);
    try {
      const res = await adminApi.createFolder(name);
      const f = await adminApi.folders();
      setFolders(f.folders);
      setFolder(res.name);
      setNewFolder("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de la création du dossier.");
    } finally {
      setCreatingFolder(false);
    }
  }

  async function handleSuggest() {
    if (!file) return;
    setSuggesting(true);
    setActionError(null);
    try {
      const res = await adminApi.suggestMetadata(file);
      setSuggestion({ ...EMPTY_SUGGESTION, ...res.suggestion });
      setAvailableDomains(res.available_domains);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de la suggestion de métadonnées.");
    } finally {
      setSuggesting(false);
    }
  }

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setActionError(null);
    setResult(null);
    try {
      // Without a prior suggestion the form is absent: metadata stays empty.
      const metadata = suggestion ? compactMetadata(suggestion) : {};
      const res = await adminApi.uploadDocument(file, folder, metadata);
      setResult(res);
      await refreshStatus();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de l'ingestion du document.");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(documentId: string) {
    if (!window.confirm(`Supprimer le document « ${documentId} » ? Cette action est irréversible.`)) {
      return;
    }
    setDeletingId(documentId);
    setActionError(null);
    try {
      await adminApi.deleteDocument(documentId);
      await refreshStatus();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de la suppression du document.");
    } finally {
      setDeletingId(null);
    }
  }

  function updateSuggestion<K extends keyof MetadataSuggestion>(key: K, value: MetadataSuggestion[K]) {
    setSuggestion((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function toggleDomain(domain: string) {
    setSuggestion((prev) => {
      if (!prev) return prev;
      const has = prev.legal_domains.includes(domain);
      return {
        ...prev,
        legal_domains: has
          ? prev.legal_domains.filter((d) => d !== domain)
          : [...prev.legal_domains, domain],
      };
    });
  }

  if (loading) return <LoadingState label="Chargement des documents…" />;
  if (error) {
    return (
      <ErrorCard message={error}>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-600/60 bg-slate-800/60 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-700/60"
        >
          Réessayer
        </button>
      </ErrorCard>
    );
  }

  // Domains suggested by the LLM but absent from the catalog stay selectable.
  const domainChoices = suggestion
    ? [...new Set([...availableDomains, ...suggestion.legal_domains])]
    : availableDomains;

  return (
    <div className="space-y-5">
      {actionError && <ErrorCard message={actionError} />}

      {/* Upload */}
      <SectionCard title="Téléverser un document">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <input
              id="admin-doc-file"
              type="file"
              accept={ACCEPTED_EXTENSIONS}
              onChange={handleFileChange}
              className="hidden"
            />
            <label htmlFor="admin-doc-file" className={`cursor-pointer ${SECONDARY_BUTTON_CLASS}`}>
              <Upload className="h-4 w-4 text-law-cyan" />
              Choisir un fichier
            </label>
            <span className="min-w-0 truncate text-xs text-slate-400">
              {file ? file.name : "Aucun fichier sélectionné (PDF, TXT, MD, HTML)"}
            </span>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-44">
              <label htmlFor="admin-doc-folder" className="mb-1 block text-xs font-medium text-slate-300">
                Dossier de destination
              </label>
              <select
                id="admin-doc-folder"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                className={INPUT_CLASS}
              >
                <option value="">Racine</option>
                {folders.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name} ({f.files} fichier{f.files > 1 ? "s" : ""})
                  </option>
                ))}
              </select>
            </div>
            <form onSubmit={handleCreateFolder} className="flex flex-wrap items-end gap-2">
              <div>
                <label htmlFor="admin-new-folder" className="mb-1 block text-xs font-medium text-slate-300">
                  Nouveau dossier
                </label>
                <input
                  id="admin-new-folder"
                  type="text"
                  value={newFolder}
                  onChange={(e) => setNewFolder(e.target.value)}
                  placeholder="ex. droit-du-travail"
                  className={INPUT_CLASS}
                />
              </div>
              <button
                type="submit"
                disabled={creatingFolder || !newFolder.trim()}
                className={SECONDARY_BUTTON_CLASS}
              >
                {creatingFolder ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FolderPlus className="h-4 w-4 text-law-cyan" />
                )}
                Créer
              </button>
            </form>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void handleSuggest()}
              disabled={!file || suggesting || uploading}
              className={SECONDARY_BUTTON_CLASS}
            >
              {suggesting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4 text-law-purple" />
              )}
              {suggesting ? "Analyse en cours…" : "Suggérer les métadonnées (LLM)"}
            </button>
            <button
              type="button"
              onClick={() => void handleUpload()}
              disabled={!file || uploading || suggesting}
              className={PRIMARY_BUTTON_CLASS}
            >
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {uploading ? "Ingestion en cours…" : "Ingérer"}
            </button>
          </div>
          {!suggestion && (
            <p className="text-[11px] text-slate-500">
              Sans suggestion, « Ingérer » envoie le document tel quel (métadonnées inférées par le
              pipeline).
            </p>
          )}
        </div>
      </SectionCard>

      {/* Editable metadata form (after a suggestion) */}
      {suggestion && (
        <SectionCard title="Métadonnées du document">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label htmlFor="meta-name" className="mb-1 block text-xs font-medium text-slate-300">
                Nom du document
              </label>
              <input
                id="meta-name"
                type="text"
                value={suggestion.document_name}
                onChange={(e) => updateSuggestion("document_name", e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="meta-authority" className="mb-1 block text-xs font-medium text-slate-300">
                Autorité
              </label>
              <select
                id="meta-authority"
                value={suggestion.authority}
                onChange={(e) => updateSuggestion("authority", e.target.value)}
                className={INPUT_CLASS}
              >
                {AUTHORITY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="meta-type" className="mb-1 block text-xs font-medium text-slate-300">
                Type de document
              </label>
              <select
                id="meta-type"
                value={suggestion.document_type}
                onChange={(e) => updateSuggestion("document_type", e.target.value)}
                className={INPUT_CLASS}
              >
                {DOCUMENT_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="meta-law-number" className="mb-1 block text-xs font-medium text-slate-300">
                Numéro de loi
              </label>
              <input
                id="meta-law-number"
                type="text"
                value={suggestion.law_number}
                onChange={(e) => updateSuggestion("law_number", e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="meta-body" className="mb-1 block text-xs font-medium text-slate-300">
                Organisme
              </label>
              <input
                id="meta-body"
                type="text"
                value={suggestion.government_body}
                onChange={(e) => updateSuggestion("government_body", e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="meta-pub-date" className="mb-1 block text-xs font-medium text-slate-300">
                Date de publication
              </label>
              <input
                id="meta-pub-date"
                type="date"
                value={suggestion.publication_date}
                onChange={(e) => updateSuggestion("publication_date", e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="meta-eff-date" className="mb-1 block text-xs font-medium text-slate-300">
                Date d&apos;entrée en vigueur
              </label>
              <input
                id="meta-eff-date"
                type="date"
                value={suggestion.effective_date}
                onChange={(e) => updateSuggestion("effective_date", e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="meta-url" className="mb-1 block text-xs font-medium text-slate-300">
                URL source
              </label>
              <input
                id="meta-url"
                type="url"
                value={suggestion.url}
                onChange={(e) => updateSuggestion("url", e.target.value)}
                placeholder="https://…"
                className={INPUT_CLASS}
              />
            </div>
            {domainChoices.length > 0 && (
              <div className="sm:col-span-2">
                <span className="mb-1 block text-xs font-medium text-slate-300">Domaines juridiques</span>
                <div className="flex flex-wrap gap-1.5">
                  {domainChoices.map((domain) => {
                    const active = suggestion.legal_domains.includes(domain);
                    return (
                      <button
                        key={domain}
                        type="button"
                        onClick={() => toggleDomain(domain)}
                        aria-pressed={active}
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                          active
                            ? "border-law-cyan/40 bg-law-cyan/10 text-law-cyan"
                            : "border-slate-600/60 bg-slate-800/60 text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        {domain}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </SectionCard>
      )}

      {/* Ingestion result */}
      {result && (
        <SectionCard title="Résultat de l'ingestion">
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge value={result.status} />
            <div className="text-sm text-slate-300">
              <span className="font-medium text-white">{result.document_name}</span>
              {" — "}
              {formatNumber(result.chunks_created)} chunk{result.chunks_created > 1 ? "s" : ""} créé
              {result.chunks_created > 1 ? "s" : ""}, version {result.version}
            </div>
          </div>
          {result.detail && <p className="mt-2 text-xs text-slate-400">{result.detail}</p>}
        </SectionCard>
      )}

      {/* Ingested documents */}
      <SectionCard
        title={`Documents ingérés${status ? ` (${formatNumber(status.total_documents)})` : ""}`}
        actions={
          status?.store_updated_at ? (
            <span className="text-[11px] text-slate-500">
              Registre mis à jour le {formatDateTime(status.store_updated_at)}
            </span>
          ) : undefined
        }
      >
        {!status || status.documents.length === 0 ? (
          <EmptyState message="Aucun document indexé pour le moment." />
        ) : (
          <TableShell>
            <THead>
              <tr>
                <Th>Document</Th>
                <Th>Version</Th>
                <Th>Hachage</Th>
                <Th>Articles</Th>
                <Th />
              </tr>
            </THead>
            <tbody>
              {status.documents.map((doc) => (
                <tr key={doc.document_id}>
                  <Td className="font-mono text-xs">{doc.document_id}</Td>
                  <Td>{doc.version}</Td>
                  <Td className="font-mono text-xs" title={doc.content_hash}>
                    {doc.content_hash ? `${doc.content_hash.slice(0, 12)}…` : "—"}
                  </Td>
                  <Td>{formatNumber(doc.article_count)}</Td>
                  <Td>
                    <button
                      type="button"
                      onClick={() => void handleDelete(doc.document_id)}
                      disabled={deletingId === doc.document_id}
                      title={`Supprimer ${doc.document_id}`}
                      aria-label={`Supprimer ${doc.document_id}`}
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-300 transition-colors hover:bg-rose-500/20 disabled:opacity-50"
                    >
                      {deletingId === doc.document_id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </SectionCard>

      {/* Failed ingestions */}
      {status && status.failed_documents.length > 0 && (
        <SectionCard title={`Ingestions en échec (${status.failed_documents.length})`}>
          <ul className="space-y-2">
            {status.failed_documents.map((rec, i) => (
              <li
                key={String(rec.document_id ?? i)}
                className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs"
              >
                <span className="font-mono text-rose-200">{String(rec.document_id ?? "inconnu")}</span>
                {typeof rec.detail === "string" && rec.detail && (
                  <span className="block text-rose-300/80">{rec.detail}</span>
                )}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}
    </div>
  );
}
