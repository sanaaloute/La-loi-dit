"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { BookMarked, Loader2, Trash2 } from "lucide-react";
import AppHeader from "@/components/AppHeader";
import { ConfidenceBadge } from "@/components/AnswerView";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import { useAuthToken } from "@/lib/useAuth";
import { deleteBookmark, listBookmarks, type Bookmark } from "@/lib/api";
import { relativeDate } from "@/lib/dates";

export default function MarquesPage() {
  const [token] = useAuthToken();
  const [bookmarks, setBookmarks] = useState<Bookmark[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listBookmarks(token)
      .then((rows) => {
        if (!cancelled) setBookmarks(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Une erreur est survenue.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleDelete(bookmark: Bookmark) {
    if (!token || deletingId) return;
    if (!window.confirm("Supprimer ce marque-page ? Cette action est irréversible.")) return;
    setDeletingId(bookmark.id);
    try {
      await deleteBookmark(bookmark.id, token);
      setBookmarks((prev) => prev?.filter((b) => b.id !== bookmark.id) ?? null);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Échec de la suppression du marque-page.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <PageShell
      header={<AppHeader token={token} />}
      disclaimer="Les marque-pages sont des instantanés conservés à la date de leur enregistrement."
    >
      {!token ? (
        <GatePanel body="Vos marque-pages sont liés à votre compte. Connectez-vous pour les retrouver." />
      ) : error ? (
        <ErrorCard message={error}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
          >
            Réessayer
          </button>
        </ErrorCard>
      ) : bookmarks === null ? (
        <LoadingState label="Chargement de vos marque-pages…" />
      ) : (
        <div className="mx-auto max-w-3xl space-y-5">
          <div>
            <h2 className="flex items-center gap-2 text-xl font-semibold text-gray-900">
              <BookMarked className="h-5 w-5 text-accent" />
              Vos marque-pages
            </h2>
            <p className="mt-1 text-sm text-gray-500">
              Les réponses que vous avez enregistrées depuis l&apos;assistant.
            </p>
          </div>

          {bookmarks.length === 0 ? (
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-6 text-center">
              <p className="text-sm text-gray-500">
                Aucun marque-page pour le moment. Utilisez l&apos;icône de marque-page sous une
                réponse de l&apos;assistant pour l&apos;enregistrer ici.
              </p>
            </div>
          ) : (
            bookmarks.map((bookmark) => (
              <article
                key={bookmark.id}
                className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl"
              >
                <div className="mb-3 flex items-start justify-between gap-3 border-b border-gray-200 pb-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900">{bookmark.query}</p>
                    <p className="mt-0.5 text-[11px] text-gray-500">
                      {relativeDate(bookmark.created_at)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <ConfidenceBadge confidence={bookmark.confidence} />
                    <button
                      type="button"
                      onClick={() => void handleDelete(bookmark)}
                      disabled={deletingId === bookmark.id}
                      title="Supprimer ce marque-page"
                      aria-label="Supprimer ce marque-page"
                      className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50"
                    >
                      {deletingId === bookmark.id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                    </button>
                  </div>
                </div>
                <div className="markdown-body text-sm text-gray-700">
                  <ReactMarkdown>{bookmark.answer}</ReactMarkdown>
                </div>
              </article>
            ))
          )}
        </div>
      )}
    </PageShell>
  );
}
