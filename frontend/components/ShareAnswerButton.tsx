"use client";

import { useState } from "react";
import { Check, Loader2, Share2 } from "lucide-react";
import { createShare, type Citation } from "@/lib/api";

interface ShareAnswerButtonProps {
  query: string;
  answer: string;
  citations: Citation[];
  confidence: number;
  token: string | null;
}

/**
 * Creates a public read-only snapshot of an answer (POST /share) and copies
 * the absolute share URL to the clipboard. A short check state confirms the
 * copy ("Lien public copié !").
 */
export default function ShareAnswerButton({
  query,
  answer,
  citations,
  confidence,
  token,
}: ShareAnswerButtonProps) {
  const [pending, setPending] = useState(false);
  const [copied, setCopied] = useState(false);

  async function share(e: React.MouseEvent) {
    e.stopPropagation();
    if (!token || pending) return;
    setPending(true);
    try {
      const res = await createShare({ query, answer, citations, confidence }, token);
      await navigator.clipboard.writeText(`${window.location.origin}${res.url_path}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Share failed", err);
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      type="button"
      disabled={pending}
      onClick={(e) => void share(e)}
      className={`rounded p-1 transition-colors ${
        copied ? "text-accent" : "text-gray-500 hover:text-gray-600"
      }`}
      aria-label={copied ? "Lien public copié !" : "Partager (copier le lien public)"}
      title={copied ? "Lien public copié !" : "Partager (copier le lien public)"}
    >
      {pending ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : copied ? (
        <Check className="h-3.5 w-3.5" />
      ) : (
        <Share2 className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
