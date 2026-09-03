"use client";

import { useState } from "react";
import { Bookmark, BookmarkCheck, Loader2 } from "lucide-react";
import { addBookmark, deleteBookmark } from "@/lib/api";

interface BookmarkButtonProps {
  query: string;
  answer: string;
  confidence: number;
  sessionId: string;
  token: string | null;
}

/**
 * Saves an answer snapshot to the user's bookmarks ("/marques"). Toggles:
 * once saved, a second click removes the bookmark again. The saved state is
 * local to the conversation view — a reloaded history starts unsaved (the
 * backend dedupes nothing by design, bookmarks are curated snapshots).
 */
export default function BookmarkButton({
  query,
  answer,
  confidence,
  sessionId,
  token,
}: BookmarkButtonProps) {
  const [bookmarkId, setBookmarkId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    if (!token || pending) return;
    setPending(true);
    try {
      if (bookmarkId) {
        await deleteBookmark(bookmarkId, token);
        setBookmarkId(null);
      } else {
        const created = await addBookmark(
          { query, answer, confidence, session_id: sessionId },
          token,
        );
        setBookmarkId(created.id);
      }
    } catch (err) {
      // Non-blocking, same policy as feedback: the icon simply stays put.
      console.error("Bookmark failed", err);
    } finally {
      setPending(false);
    }
  }

  const saved = bookmarkId !== null;
  return (
    <button
      type="button"
      disabled={pending}
      onClick={(e) => void toggle(e)}
      className={`rounded p-1 transition-colors ${
        saved ? "text-accent" : "text-gray-500 hover:text-gray-600"
      }`}
      aria-label={saved ? "Retirer des marque-pages" : "Enregistrer dans les marque-pages"}
      title={saved ? "Retirer des marque-pages" : "Enregistrer dans les marque-pages"}
    >
      {pending ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : saved ? (
        <BookmarkCheck className="h-3.5 w-3.5" />
      ) : (
        <Bookmark className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
