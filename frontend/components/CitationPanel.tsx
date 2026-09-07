"use client";

import { ExternalLink, CheckCircle2, AlertCircle } from "lucide-react";
import type { Citation } from "@/lib/api";

interface CitationPanelProps {
  citations: Citation[];
}

export default function CitationPanel({ citations }: CitationPanelProps) {
  return (
    <div className="p-4">
      <h3 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        Citations ({citations.length})
      </h3>
      {citations.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-center">
          <p className="text-xs text-gray-500">Aucune citation pour cette réponse.</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {citations.map((citation, i) => (
            <li
              key={`${citation.label}-${i}`}
              className="rounded-xl border border-gray-200 bg-surface-elevated p-3 transition-colors hover:border-accent/30"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900">{citation.label}</p>
                  {citation.article && (
                    <p className="mt-0.5 text-xs text-gray-500">Article {citation.article}</p>
                  )}
                </div>
                {citation.verified ? (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
                    <CheckCircle2 className="h-3 w-3" />
                    Vérifiée
                  </span>
                ) : (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-warn-border/60 bg-warn-bg px-2 py-0.5 text-[10px] font-medium text-warn-text">
                    <AlertCircle className="h-3 w-3" />
                    Non vérifiée
                  </span>
                )}
              </div>
              <p className="mt-1 truncate text-[11px] text-gray-500">{citation.document_name}</p>
              {citation.law_number && (
                <p className="mt-0.5 text-[11px] text-gray-500">Loi n°{citation.law_number}</p>
              )}
              {citation.url && (
                <a
                  href={citation.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-flex max-w-full items-center gap-1 text-xs text-accent hover:text-accent/80 hover:underline"
                >
                  <ExternalLink className="h-3 w-3 shrink-0" />
                  <span className="truncate">{citation.url}</span>
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
