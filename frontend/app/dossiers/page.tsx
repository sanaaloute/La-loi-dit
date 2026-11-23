"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import {
  ArrowLeft,
  BookMarked,
  FileText,
  FolderOpen,
  FolderPlus,
  Loader2,
  Scale,
  Send,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import BookmarksPanel from "@/components/BookmarksPanel";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";

import { useAuthToken } from "@/lib/useAuth";
import {
  analyzeDossier,
  analyzeFolder,
  createFolder,
  deleteDossier,
  deleteFolder,
  getDossierText,
  listDossiers,
  listFolders,
  moveDossier,
  uploadDossier,
  type Dossier,
  type DossierFolder,
  type FolderAnalysis,
} from "@/lib/api";
import { formatLongDate, relativeDate } from "@/lib/dates";
import { displayDocName } from "@/lib/docNames";

const ACCEPTED = ".pdf,.txt,.md,.docx";

interface Exchange {
  question: string;
  answer: string;
  truncated: boolean;
}

interface FolderExchange {
  question: string;
  result: FolderAnalysis;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

function DossiersContent() {
  const [token] = useAuthToken();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<"documents" | "marques">(
    searchParams.get("tab") === "marques" ? "marques" : "documents",
  );

  const [dossiers, setDossiers] = useState<Dossier[] | null>(null);
  const [folders, setFolders] = useState<DossierFolder[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [selectedFolder, setSelectedFolder] = useState<DossierFolder | null>(null);
  const [selected, setSelected] = useState<Dossier | null>(null);
  const [creating, setCreating] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [folderExchanges, setFolderExchanges] = useState<FolderExchange[]>([]);
  const [analysisMode, setAnalysisMode] = useState<"case" | "documents">("case");
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [textOpen, setTextOpen] = useState(false);
  const [fullText, setFullText] = useState<string | null>(null);
  const [textLoading, setTextLoading] = useState(false);

  const refresh = useCallback(() => {
    if (!token) return;
    setLoadError(null);
    Promise.all([listDossiers(token), listFolders(token)])
      .then(([docs, folds]) => {
        setDossiers(docs);
        setFolders(folds);
        setSelectedFolder((cur) => (cur ? (folds.find((f) => f.id === cur.id) ?? null) : null));
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Une erreur est survenue."));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    refresh();
  }, [token, refresh]);

  async function handleFiles(files: FileList | null) {
    if (!files || !token || uploading) return;
    const file = files[0];
    setUploadError(null);
    setUploading(file.name);
    try {
      const created = await uploadDossier(file, "", token, selectedFolder?.id || undefined);
      setDossiers((prev) => (prev ? [created, ...prev] : [created]));
      setFolders((prev) =>
        prev?.map((f) =>
          f.id === created.folder_id ? { ...f, document_count: f.document_count + 1 } : f,
        ) ?? null,
      );
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Échec de l'envoi du document.");
    } finally {
      setUploading(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handleCreateFolder() {
    const name = newFolderName.trim();
    if (!name || !token || creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const folder = await createFolder(name, "", token);
      setFolders((prev) => [folder, ...(prev ?? [])]);
      setNewFolderName("");
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Échec de la création du dossier.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteFolder(folder: DossierFolder) {
    if (!token || deletingId) return;
    const withDocs =
      folder.document_count > 0 &&
      window.confirm(
        `« ${folder.name} » contient ${folder.document_count} document(s).\n\nOK : supprimer le dossier ET ses documents.\nAnnuler : choisir une autre option.`,
      );
    if (folder.document_count > 0 && !withDocs) {
      if (!window.confirm("Supprimer le dossier mais CONSERVER ses documents (ils seront déclassés) ?"))
        return;
    } else if (!withDocs && !window.confirm(`Supprimer le dossier « ${folder.name} » ?`)) {
      return;
    }
    setDeletingId(folder.id);
    try {
      await deleteFolder(folder.id, withDocs, token);
      setFolders((prev) => prev?.filter((f) => f.id !== folder.id) ?? null);
      if (withDocs) {
        setDossiers((prev) => prev?.filter((d) => d.folder_id !== folder.id) ?? null);
      } else {
        setDossiers((prev) =>
          prev?.map((d) => (d.folder_id === folder.id ? { ...d, folder_id: "" } : d)) ?? null,
        );
      }
      if (selectedFolder?.id === folder.id) setSelectedFolder(null);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Échec de la suppression.");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleDelete(dossier: Dossier) {
    if (!token || deletingId) return;
    if (!window.confirm(`Supprimer « ${displayDocName(dossier.name)} » ? Cette action est irréversible.`))
      return;
    setDeletingId(dossier.id);
    try {
      await deleteDossier(dossier.id, token);
      setDossiers((prev) => prev?.filter((d) => d.id !== dossier.id) ?? null);
      setFolders((prev) =>
        prev?.map((f) =>
          f.id === dossier.folder_id ? { ...f, document_count: Math.max(0, f.document_count - 1) } : f,
        ) ?? null,
      );
      if (selected?.id === dossier.id) setSelected(null);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Échec de la suppression.");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleMove(dossier: Dossier, folderId: string) {
    if (!token) return;
    try {
      const updated = await moveDossier(dossier.id, folderId, token);
      setDossiers((prev) => prev?.map((d) => (d.id === dossier.id ? updated : d)) ?? null);
      setSelected(updated);
      refresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Échec du déplacement.");
    }
  }

  function openDossier(dossier: Dossier) {
    setSelected(dossier);
    setExchanges([]);
    setQuestion("");
    setAskError(null);
    setTextOpen(false);
    setFullText(null);
  }

  function openFolder(folder: DossierFolder) {
    setSelectedFolder(folder);
    setFolderExchanges([]);
    setQuestion("");
    setAskError(null);
    setUploadError(null);
  }

  async function ask() {
    if (!token || asking) return;
    const q = question.trim();
    if (!q) return;
    setAsking(true);
    setAskError(null);
    setQuestion("");
    try {
      if (selectedFolder) {
        const res = await analyzeFolder(selectedFolder.id, q, analysisMode, token);
        setFolderExchanges((prev) => [...prev, { question: q, result: res }]);
      } else if (selected) {
        const res = await analyzeDossier(selected.id, q, token);
        setExchanges((prev) => [...prev, { question: q, answer: res.answer, truncated: res.truncated }]);
      }
    } catch (err) {
      setAskError(err instanceof Error ? err.message : "Échec de l'analyse.");
      setQuestion(q);
    } finally {
      setAsking(false);
    }
  }

  function toggleText() {
    if (!token || !selected) return;
    const next = !textOpen;
    setTextOpen(next);
    if (next && fullText === null && !textLoading) {
      setTextLoading(true);
      getDossierText(selected.id, token)
        .then((res) => setFullText(res.text))
        .catch(() => setFullText(""))
        .finally(() => setTextLoading(false));
    }
  }

  const folderDocs = selectedFolder
    ? (dossiers ?? []).filter((d) => d.folder_id === selectedFolder.id)
    : [];
  const unfiledDocs = (dossiers ?? []).filter((d) => !d.folder_id);

  const dropzone = (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        void handleFiles(e.dataTransfer.files);
      }}
      className={`mb-6 flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
        dragOver ? "border-accent bg-accent-light" : "border-gray-300 bg-white hover:border-accent/50"
      }`}
    >
      <UploadCloud className={`h-8 w-8 ${dragOver ? "text-accent" : "text-gray-400"}`} />
      {uploading ? (
        <p className="flex items-center gap-2 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          Envoi et lecture de « {uploading} »…
        </p>
      ) : (
        <>
          <p className="text-sm font-medium text-gray-700">
            Glissez un document ici, ou{" "}
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="font-semibold text-accent hover:underline"
            >
              parcourez vos fichiers
            </button>
          </p>
          <p className="text-xs text-gray-400">
            PDF, TXT, MD ou DOCX — le texte est extrait pour l'analyse.
            {selectedFolder ? ` Le document sera classé dans « ${selectedFolder.name} ».` : ""}
          </p>
        </>
      )}
      <input
        ref={fileInput}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        onChange={(e) => void handleFiles(e.target.files)}
      />
    </div>
  );

  function docCard(dossier: Dossier) {
    return (
      <div
        key={dossier.id}
        className="group relative flex flex-col rounded-xl border border-gray-200 bg-white p-4 text-left transition-all hover:border-accent/40 hover:shadow-panel"
      >
        <button type="button" onClick={() => openDossier(dossier)} className="flex flex-1 flex-col text-left">
          <span className="mb-2 flex items-center justify-between gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-light">
              <FileText className="h-4 w-4 text-accent" />
            </span>
            <span className="text-[10px] text-gray-400">{relativeDate(dossier.created_at)}</span>
          </span>
          <span className="line-clamp-2 text-sm font-medium leading-snug text-gray-900">
            {displayDocName(dossier.name)}
          </span>
          <span className="mt-auto flex flex-wrap items-center gap-1 pt-3">
            <span className="rounded-full border border-gray-300 bg-gray-50 px-2 py-0.5 text-[10px] font-medium uppercase text-gray-600">
              {dossier.extension.replace(".", "")}
            </span>
            {dossier.status === "error" ? (
              <span className="rounded-full border border-warn-border/60 bg-warn-bg px-2 py-0.5 text-[10px] font-medium text-warn-text">
                Texte non extrait
              </span>
            ) : (
              <span className="rounded-full border border-gray-300 bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-600">
                {formatSize(dossier.size_bytes)}
              </span>
            )}
          </span>
        </button>
        <span className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3">
          <span className="text-[11px] font-medium text-accent group-hover:underline">Analyser →</span>
          <button
            type="button"
            onClick={() => void handleDelete(dossier)}
            disabled={deletingId === dossier.id}
            title="Supprimer ce document"
            aria-label="Supprimer ce document"
            className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
          >
            {deletingId === dossier.id ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </button>
        </span>
      </div>
    );
  }

  return (
    <AppShell disclaimer="L'analyse de vos documents est fournie à titre informatif et ne constitue pas un conseil juridique.">
      {!token ? (
        <GatePanel body="Vos dossiers sont liés à votre compte. Connectez-vous pour les retrouver." />
      ) : (
        <div className="mx-auto max-w-6xl">
          <header className="mb-6">
            <h1 className="flex items-center gap-2.5 font-serif text-2xl font-bold text-gray-900 sm:text-3xl">
              <FolderOpen className="h-7 w-7 text-gold" />
              Mes dossiers
            </h1>
            <p className="mt-1 text-sm text-gray-500">
              Créez un dossier par affaire, déposez vos pièces, et laissez Yawoto analyser votre cas
              à la lumière de la loi.
            </p>
          </header>

          {/* Tabs */}
          <div className="mb-6 flex gap-2 border-b border-gray-200">
            {(
              [
                { key: "documents", label: "Dossiers & documents", icon: FileText },
                { key: "marques", label: "Marque-pages", icon: BookMarked },
              ] as const
            ).map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  tab === t.key
                    ? "border-accent text-accent"
                    : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-900"
                }`}
              >
                <t.icon className="h-4 w-4" />
                {t.label}
              </button>
            ))}
          </div>

          {tab === "marques" ? (
            <BookmarksPanel token={token} />
          ) : selected ? (
            /* -------- Document detail -------- */
            <div>
              <div className="mb-4 flex items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-accent"
                >
                  <ArrowLeft className="h-4 w-4" />
                  Retour
                </button>
                <div className="flex items-center gap-2">
                  <select
                    value={selected.folder_id}
                    onChange={(e) => void handleMove(selected, e.target.value)}
                    title="Classer dans un dossier"
                    aria-label="Classer dans un dossier"
                    className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-700 focus:border-accent/60 focus:outline-none"
                  >
                    <option value="">Non classé</option>
                    {(folders ?? []).map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => void handleDelete(selected)}
                    disabled={deletingId === selected.id}
                    className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
                  >
                    {deletingId === selected.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                    Supprimer
                  </button>
                </div>
              </div>

              <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-panel">
                <div className="flex items-start gap-3">
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent-light">
                    <FileText className="h-5 w-5 text-accent" />
                  </span>
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold text-gray-900">
                      {displayDocName(selected.name)}
                    </h2>
                    <p className="mt-0.5 text-xs text-gray-500">
                      Ajouté le {formatLongDate(selected.created_at)} · {formatSize(selected.size_bytes)}
                      {selected.page_count > 0 && ` · ${selected.page_count} page${selected.page_count > 1 ? "s" : ""}`}
                    </p>
                  </div>
                </div>

                {selected.status === "error" ? (
                  <p className="mt-4 rounded-lg border border-warn-border/60 bg-warn-bg px-3 py-2 text-xs text-warn-text">
                    {selected.error || "Le texte de ce document n'a pas pu être extrait."}
                  </p>
                ) : (
                  <>
                    {exchanges.length > 0 && (
                      <div className="mt-5 space-y-4 border-t border-gray-200 pt-5">
                        {exchanges.map((ex, i) => (
                          <div key={i} className="space-y-2">
                            <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-white">
                              {ex.question}
                            </p>
                            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                              <div className="markdown-body text-sm text-gray-700">
                                <ReactMarkdown>{ex.answer}</ReactMarkdown>
                              </div>
                              {ex.truncated && (
                                <p className="mt-2 border-t border-gray-200 pt-2 text-[11px] text-gray-400">
                                  Document long : l'analyse porte sur le début du texte extrait.
                                </p>
                              )}
                            </div>
                          </div>
                        ))}
                        {asking && (
                          <div className="flex items-center gap-2 text-xs text-gray-500">
                            <Loader2 className="h-4 w-4 animate-spin text-accent" />
                            Analyse du document en cours…
                          </div>
                        )}
                      </div>
                    )}

                    {askError && (
                      <p className="mt-4 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                        {askError}
                      </p>
                    )}

                    <div className="mt-5 flex items-end gap-2">
                      <textarea
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            void ask();
                          }
                        }}
                        rows={2}
                        placeholder={
                          exchanges.length === 0
                            ? "Posez une question sur ce document : résumé, clause clé, obligation, date…"
                            : "Une autre question sur ce document…"
                        }
                        aria-label="Question sur le document"
                        className="min-w-0 flex-1 resize-none rounded-xl border border-gray-300 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={() => void ask()}
                        disabled={asking || !question.trim()}
                        className="flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
                      >
                        {asking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                        Analyser
                      </button>
                    </div>

                    <button
                      type="button"
                      onClick={toggleText}
                      className="mt-3 text-xs font-medium text-accent hover:underline"
                    >
                      {textOpen ? "Masquer le texte extrait" : "Voir le texte extrait"}
                    </button>
                    {textOpen && (
                      <div className="mt-2 max-h-96 overflow-y-auto rounded-xl border border-gray-200 bg-gray-50 p-4">
                        {textLoading ? (
                          <p className="flex items-center gap-2 text-xs text-gray-500">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            Chargement du texte…
                          </p>
                        ) : fullText ? (
                          <p className="whitespace-pre-wrap text-xs leading-relaxed text-gray-600">{fullText}</p>
                        ) : (
                          <p className="text-xs text-gray-500">Texte indisponible.</p>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ) : selectedFolder ? (
            /* -------- Folder detail: case analysis -------- */
            <div>
              <div className="mb-4 flex items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => setSelectedFolder(null)}
                  className="flex items-center gap-1.5 text-sm font-medium text-gray-500 transition-colors hover:text-accent"
                >
                  <ArrowLeft className="h-4 w-4" />
                  Tous mes dossiers
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteFolder(selectedFolder)}
                  disabled={deletingId === selectedFolder.id}
                  className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
                >
                  {deletingId === selectedFolder.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                  Supprimer le dossier
                </button>
              </div>

              <div className="mb-4 rounded-2xl border border-gray-200 bg-white p-5 shadow-panel">
                <div className="flex items-start gap-3">
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gold-light">
                    <FolderOpen className="h-5 w-5 text-gold" />
                  </span>
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold text-gray-900">{selectedFolder.name}</h2>
                    <p className="mt-0.5 text-xs text-gray-500">
                      {folderDocs.length} document{folderDocs.length > 1 ? "s" : ""} · créé le{" "}
                      {formatLongDate(selectedFolder.created_at)}
                    </p>
                  </div>
                </div>
              </div>

              {dropzone}
              {uploadError && (
                <p className="mb-4 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                  {uploadError}
                </p>
              )}

              {folderDocs.length > 0 && (
                <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{folderDocs.map(docCard)}</div>
              )}

              {/* Case analysis panel */}
              <div className="rounded-2xl border border-accent/15 bg-white p-5 shadow-panel">
                <h3 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                  <Scale className="h-4 w-4 text-gold" />
                  Analyse du dossier
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  Yawoto lit toutes les pièces du dossier et applique la loi burkinabè à votre affaire.
                </p>

                {/* Mode toggle */}
                <div className="mt-3 flex gap-2">
                  {(
                    [
                      { key: "case", label: "Analyse complète (documents + loi)" },
                      { key: "documents", label: "Documents seuls" },
                    ] as const
                  ).map((m) => (
                    <button
                      key={m.key}
                      type="button"
                      onClick={() => setAnalysisMode(m.key)}
                      className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                        analysisMode === m.key
                          ? "bg-accent text-white"
                          : "border border-gray-300 bg-white text-gray-600 hover:border-accent/40"
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>

                {folderExchanges.length > 0 && (
                  <div className="mt-5 space-y-4 border-t border-gray-200 pt-5">
                    {folderExchanges.map((ex, i) => (
                      <div key={i} className="space-y-2">
                        <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-white">
                          {ex.question}
                        </p>
                        <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                          <div className="markdown-body text-sm text-gray-700">
                            <ReactMarkdown>{ex.result.answer}</ReactMarkdown>
                          </div>
                          {ex.result.citations.length > 0 && (
                            <div className="mt-3 border-t border-gray-200 pt-2">
                              <p className="mb-1 text-[11px] font-semibold text-gray-500">Sources</p>
                              <ul className="space-y-0.5 text-[11px] text-gray-500">
                                {ex.result.citations.map((c, j) => (
                                  <li key={j}>
                                    {c.label}
                                    {c.document_name ? ` — ${displayDocName(c.document_name)}` : ""}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                          <p className="mt-2 border-t border-gray-200 pt-2 text-[11px] text-gray-400">
                            {ex.result.mode === "case"
                              ? "Analyse croisant vos documents et le corpus juridique indexé."
                              : "Analyse fondée uniquement sur vos documents."}
                            {ex.result.truncated &&
                              " Dossier volumineux : l'analyse porte sur une fiche de synthèse des pièces."}
                          </p>
                        </div>
                      </div>
                    ))}
                    {asking && (
                      <div className="flex items-center gap-2 text-xs text-gray-500">
                        <Loader2 className="h-4 w-4 animate-spin text-accent" />
                        {analysisMode === "case"
                          ? "Analyse de l'affaire en cours (lecture des pièces + recherche juridique)…"
                          : "Lecture des documents en cours…"}
                      </div>
                    )}
                  </div>
                )}

                {askError && (
                  <p className="mt-4 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                    {askError}
                  </p>
                )}

                <div className="mt-4 flex items-end gap-2">
                  <textarea
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void ask();
                      }
                    }}
                    rows={2}
                    placeholder="Décrivez votre affaire ou posez votre question : quels sont mes recours, quels risques, quelle procédure…"
                    aria-label="Question sur le dossier"
                    className="min-w-0 flex-1 resize-none rounded-xl border border-gray-300 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => void ask()}
                    disabled={asking || !question.trim() || folderDocs.length === 0}
                    className="flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
                  >
                    {asking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    Analyser
                  </button>
                </div>
                {folderDocs.length === 0 && (
                  <p className="mt-2 text-[11px] text-gray-400">
                    Ajoutez au moins un document au dossier pour lancer l'analyse.
                  </p>
                )}
              </div>
            </div>
          ) : (
            /* -------- Home: folders + unfiled documents -------- */
            <div>
              {/* New folder */}
              <div className="mb-6 flex items-center gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-gray-300 bg-white px-3.5 py-2.5">
                  <FolderPlus className="h-4 w-4 shrink-0 text-gold" />
                  <input
                    type="text"
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleCreateFolder();
                    }}
                    placeholder="Nouveau dossier (ex. : Litige bail commercial Kaboré)…"
                    aria-label="Nom du nouveau dossier"
                    className="min-w-0 flex-1 bg-transparent text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void handleCreateFolder()}
                  disabled={creating || !newFolderName.trim()}
                  className="flex h-10 shrink-0 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
                >
                  {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderPlus className="h-4 w-4" />}
                  Créer
                </button>
              </div>
              {createError && (
                <p className="mb-4 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                  {createError}
                </p>
              )}

              {/* Folders grid */}
              {folders && folders.length > 0 && (
                <div className="mb-8">
                  <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">
                    Mes affaires
                  </h2>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {folders.map((folder) => (
                      <div
                        key={folder.id}
                        className="group relative flex flex-col rounded-xl border border-gold/30 bg-white p-4 text-left transition-all hover:border-gold/60 hover:shadow-panel"
                      >
                        <button
                          type="button"
                          onClick={() => openFolder(folder)}
                          className="flex flex-1 flex-col text-left"
                        >
                          <span className="mb-2 flex items-center justify-between gap-2">
                            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-gold-light">
                              <FolderOpen className="h-4 w-4 text-gold" />
                            </span>
                            <span className="text-[10px] text-gray-400">{relativeDate(folder.created_at)}</span>
                          </span>
                          <span className="line-clamp-2 text-sm font-medium leading-snug text-gray-900">
                            {folder.name}
                          </span>
                          <span className="mt-auto pt-3 text-[11px] text-gray-500">
                            {folder.document_count} document{folder.document_count > 1 ? "s" : ""}
                          </span>
                        </button>
                        <span className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3">
                          <span className="text-[11px] font-medium text-accent group-hover:underline">
                            Analyser l'affaire →
                          </span>
                          <button
                            type="button"
                            onClick={() => void handleDeleteFolder(folder)}
                            disabled={deletingId === folder.id}
                            title="Supprimer ce dossier"
                            aria-label="Supprimer ce dossier"
                            className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
                          >
                            {deletingId === folder.id ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="h-3.5 w-3.5" />
                            )}
                          </button>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {dropzone}
              {uploadError && (
                <p className="mb-4 rounded-lg border border-red-700/30 bg-red-700/10 px-3 py-2 text-xs text-red-700">
                  {uploadError}
                </p>
              )}

              {loadError ? (
                <ErrorCard message={loadError}>
                  <button
                    type="button"
                    onClick={refresh}
                    className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
                  >
                    Réessayer
                  </button>
                </ErrorCard>
              ) : dossiers === null || folders === null ? (
                <LoadingState label="Chargement de vos dossiers…" />
              ) : dossiers.length === 0 && folders.length === 0 ? (
                <div className="rounded-2xl border border-gray-200 bg-white p-10 text-center shadow-panel">
                  <Sparkles className="mx-auto h-8 w-8 text-gold" />
                  <p className="mt-3 text-sm font-medium text-gray-700">Aucun dossier pour le moment</p>
                  <p className="mx-auto mt-1 max-w-md text-xs text-gray-500">
                    Créez un dossier pour votre affaire, déposez contrats, décisions de justice ou
                    courriers : Yawoto les lit et les analyse à la lumière de la loi.
                  </p>
                </div>
              ) : unfiledDocs.length > 0 ? (
                <div>
                  <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">
                    Documents non classés
                  </h2>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{unfiledDocs.map(docCard)}</div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}

export default function DossiersPage() {
  return (
    <Suspense fallback={<LoadingState label="Chargement…" />}>
      <DossiersContent />
    </Suspense>
  );
}
