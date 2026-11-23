"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import CitationPanel from "@/components/CitationPanel";
import EvidenceViewer from "@/components/EvidenceViewer";
import type { Citation, EvidenceChunk } from "@/lib/api";

export type AnswerDetailsTab = "citations" | "preuves";

interface AnswerDetailsModalProps {
  citations: Citation[];
  evidence: EvidenceChunk[];
  /** Tab selected when the dialog opens. */
  initialTab: AnswerDetailsTab;
  onClose: () => void;
}

/** Citations / preuves of one assistant answer, shown as a scrollable dialog
 * above the chat (the right side panel was removed in favour of per-message
 * buttons). Closes on backdrop click, Escape or the X button. */
export default function AnswerDetailsModal({
  citations,
  evidence,
  initialTab,
  onClose,
}: AnswerDetailsModalProps) {
  const [tab, setTab] = useState<AnswerDetailsTab>(initialTab);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6">
      <button
        type="button"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        aria-label="Fermer les détails de la réponse"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Détails de la réponse"
        className="relative flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-surface-elevated shadow-panel"
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <span className="text-sm font-semibold text-gray-900">Détails de la réponse</span>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900"
            title="Fermer"
            aria-label="Fermer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex border-b border-gray-200 bg-gray-50">
          {(
            [
              { id: "citations", label: `Citations (${citations.length})` },
              { id: "preuves", label: `Preuves (${evidence.length})` },
            ] as { id: AnswerDetailsTab; label: string }[]
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`flex-1 px-2 py-2.5 text-xs font-medium transition-colors sm:text-sm ${
                tab === t.id
                  ? "border-b-2 border-accent text-accent"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto">
          {tab === "citations" && <CitationPanel citations={citations} />}
          {tab === "preuves" && <EvidenceViewer evidence={evidence} />}
        </div>
      </div>
    </div>
  );
}
