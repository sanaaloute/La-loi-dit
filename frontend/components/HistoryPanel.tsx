"use client";

import { useEffect, useState } from "react";
import {
  Building2,
  ChevronsLeft,
  ChevronsRight,
  History,
  Loader2,
  MessageSquare,
  Trash2,
  X,
} from "lucide-react";
import { deleteSession, listSessions, me, type ChatSessionSummary } from "@/lib/api";

interface HistoryPanelProps {
  token: string | null;
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  /** Called after a conversation is deleted (so the chat can reset if it was active). */
  onDeleted?: (sessionId: string) => void;
  refreshKey: number;
  open: boolean;
  onClose: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

/** Relative date in French, e.g. "il y a 2 h" (no library). */
function relativeDate(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `il y a ${days} j`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `il y a ${weeks} sem.`;
  const months = Math.floor(days / 30);
  if (months < 12) return `il y a ${months} mois`;
  const years = Math.floor(days / 365);
  return `il y a ${years} an${years > 1 ? "s" : ""}`;
}

export default function HistoryPanel({
  token,
  activeSessionId,
  onSelect,
  onDeleted,
  refreshKey,
  open,
  onClose,
  collapsed,
  onToggleCollapsed,
}: HistoryPanelProps) {
  const [sessions, setSessions] = useState<ChatSessionSummary[] | null>(null);
  const [workspaceName, setWorkspaceName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setSessions(null);
      setWorkspaceName(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    listSessions(token)
      .then((res) => {
        if (!cancelled) setSessions(res.sessions);
      })
      .catch(() => {
        if (!cancelled) setSessions(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, refreshKey]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    me(token)
      .then((p) => {
        if (!cancelled) setWorkspaceName(p.workspace_name ?? null);
      })
      .catch(() => {
        // Nom d'espace indisponible : on affiche simplement "Historique".
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleDelete(session: ChatSessionSummary) {
    if (!token || deletingId) return;
    if (!window.confirm(`Supprimer la conversation « ${session.title} » ? Cette action est irréversible.`)) {
      return;
    }
    setDeletingId(session.session_id);
    try {
      await deleteSession(session.session_id, token);
      setSessions((prev) => prev?.filter((s) => s.session_id !== session.session_id) ?? null);
      onDeleted?.(session.session_id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Échec de la suppression de la conversation.");
    } finally {
      setDeletingId(null);
    }
  }

  // Hidden entirely when logged out.
  if (!token) return null;

  // `collapsed` is desktop-only: every hiding class below is scoped to md:,
  // so the mobile drawer always shows the full list.
  return (
    <aside
      className={`absolute inset-y-0 left-0 z-30 flex w-[min(86vw,18rem)] flex-col border-r border-gray-200 bg-white shadow-2xl backdrop-blur-xl transition-all duration-300 md:static md:bg-surface/70 md:shadow-none ${
        open ? "translate-x-0" : "-translate-x-full md:translate-x-0"
      } ${collapsed ? "md:w-14" : "md:w-72"}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-gray-200 px-3 py-3">
        <span
          className={`flex min-w-0 items-center gap-2 text-xs text-gray-500 ${collapsed ? "md:hidden" : ""}`}
        >
          <Building2 className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{workspaceName ?? "Historique"}</span>
        </span>
        {collapsed && (
          <History className="mx-auto hidden h-4 w-4 shrink-0 text-gray-500 md:block" />
        )}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="hidden h-10 w-10 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 md:flex"
            title={collapsed ? "Afficher l'historique" : "Réduire l'historique"}
          >
            {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-900 md:hidden"
            title="Fermer l'historique"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto p-2">
        {loading && sessions === null ? (
          <div className="flex items-center justify-center gap-2 py-6 text-xs text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin text-accent" />
            <span className={collapsed ? "md:hidden" : ""}>Chargement…</span>
          </div>
        ) : !sessions || sessions.length === 0 ? (
          <p className={`px-2 py-6 text-center text-xs text-gray-500 ${collapsed ? "md:hidden" : ""}`}>
            Aucune conversation pour le moment.
          </p>
        ) : (
          <div className="space-y-1">
            {sessions.map((session) => {
              const active = session.session_id === activeSessionId;
              const deleting = deletingId === session.session_id;
              return (
                <div
                  key={session.session_id}
                  className={`group relative rounded-lg border transition-colors ${
                    active
                      ? "border-accent/40 bg-accent/10"
                      : "border-transparent hover:bg-gray-100"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(session.session_id)}
                    title={session.title}
                    className={`w-full rounded-lg px-3 py-2 pr-9 text-left ${
                      collapsed ? "md:px-2 md:pr-2" : ""
                    }`}
                  >
                    <span className={`flex items-start gap-2 ${collapsed ? "md:justify-center" : ""}`}>
                      <MessageSquare
                        className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${active ? "text-accent" : "text-gray-500"}`}
                      />
                      <span className={`min-w-0 flex-1 ${collapsed ? "md:hidden" : ""}`}>
                        <span
                          className={`block truncate text-sm ${active ? "text-gray-900" : "text-gray-700"}`}
                        >
                          {session.title}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-gray-500">
                          {relativeDate(session.updated_at)} — {session.message_count} message
                          {session.message_count > 1 ? "s" : ""}
                        </span>
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDelete(session)}
                    disabled={deleting}
                    title={`Supprimer « ${session.title} »`}
                    aria-label={`Supprimer la conversation ${session.title}`}
                    className={`absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100 ${
                      collapsed ? "md:hidden" : ""
                    }`}
                  >
                    {deleting ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}
