"use client";

import ReactMarkdown from "react-markdown";
import { AlertTriangle, CheckCircle2, ShieldAlert } from "lucide-react";
import type { FinalAnswer } from "@/lib/api";

interface AnswerViewProps {
  answer: FinalAnswer;
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  if (confidence >= 0.55) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent/10 px-2.5 py-0.5 text-xs font-semibold text-accent">
        <CheckCircle2 className="h-3 w-3" />
        Confiance {pct}%
      </span>
    );
  }
  if (confidence >= 0.4) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-warn-border/60 bg-warn-bg px-2.5 py-0.5 text-xs font-semibold text-warn-text">
        <AlertTriangle className="h-3 w-3" />
        Confiance {pct}%
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-red-700/30 bg-red-700/10 px-2.5 py-0.5 text-xs font-semibold text-red-700">
      <ShieldAlert className="h-3 w-3" />
      Confiance {pct}%
    </span>
  );
}

export default function AnswerView({ answer }: AnswerViewProps) {
  // Direct-route answers (casual conversation, no legal retrieval): render
  // the reply plainly — a confidence score is meaningless there.
  const isDirect = answer.metadata?.route === "direct";
  if (answer.refused) {
    return (
      <div className="rounded-xl border border-red-700/30 bg-red-700/10 p-4">
        <p className="mb-1 flex items-center gap-2 text-sm font-semibold text-red-700">
          <ShieldAlert className="h-4 w-4" />
          Demande refusée
        </p>
        {answer.refusal_reason && <p className="text-sm text-red-800">{answer.refusal_reason}</p>}
        {answer.answer && (
          <div className="markdown-body mt-2 text-red-800">
            <ReactMarkdown>{answer.answer}</ReactMarkdown>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {!isDirect && (
        <div className="flex flex-wrap items-center gap-2">
          <ConfidenceBadge confidence={answer.confidence} />
          {answer.requires_human_review && (
            <span className="inline-flex items-center gap-1 rounded-full border border-red-700/30 bg-red-700/10 px-2.5 py-0.5 text-xs font-semibold text-red-700">
              <ShieldAlert className="h-3 w-3" />
              Révision humaine requise
            </span>
          )}
        </div>
      )}

      {answer.requires_human_review && (
        <div className="rounded-xl border border-red-700/20 bg-red-700/10 px-3 py-2 text-xs text-red-800">
          Cette réponse doit être validée par un juriste avant toute utilisation.
        </div>
      )}

      <div className="markdown-body">
        <ReactMarkdown>{answer.answer}</ReactMarkdown>
      </div>

      {answer.warnings.length > 0 && (
        <div className="rounded-xl border border-warn-border/60 bg-warn-bg p-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-warn-text">
            <AlertTriangle className="h-3.5 w-3.5" />
            Avertissements
          </p>
          <ul className="list-disc space-y-1 pl-4 text-xs text-warn-text">
            {answer.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {answer.conflicts.length > 0 && (
        <div className="space-y-2">
          {answer.conflicts.map((c, i) => (
            <div
              key={i}
              className={`rounded-xl border p-3 text-xs ${
                c.resolved
                  ? "border-gray-200 bg-gray-50 text-gray-600"
                  : "border-warn-border/60 bg-warn-bg text-warn-text"
              }`}
            >
              <p className="font-semibold">
                {c.resolved ? "Conflit résolu" : "Conflit non résolu"} : {c.topic}
              </p>
              <p className="mt-1">{c.reason}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
