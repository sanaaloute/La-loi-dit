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
      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-300">
        <CheckCircle2 className="h-3 w-3" />
        Confiance {pct}%
      </span>
    );
  }
  if (confidence >= 0.4) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-xs font-semibold text-amber-300">
        <AlertTriangle className="h-3 w-3" />
        Confiance {pct}%
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-rose-400/30 bg-rose-400/10 px-2.5 py-0.5 text-xs font-semibold text-rose-300">
      <ShieldAlert className="h-3 w-3" />
      Confiance {pct}%
    </span>
  );
}

export default function AnswerView({ answer }: AnswerViewProps) {
  if (answer.refused) {
    return (
      <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4">
        <p className="mb-1 flex items-center gap-2 text-sm font-semibold text-rose-300">
          <ShieldAlert className="h-4 w-4" />
          Demande refusée
        </p>
        {answer.refusal_reason && <p className="text-sm text-rose-200">{answer.refusal_reason}</p>}
        {answer.answer && (
          <div className="markdown-body mt-2 text-rose-100">
            <ReactMarkdown>{answer.answer}</ReactMarkdown>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ConfidenceBadge confidence={answer.confidence} />
        {answer.requires_human_review && (
          <span className="inline-flex items-center gap-1 rounded-full border border-rose-400/30 bg-rose-400/10 px-2.5 py-0.5 text-xs font-semibold text-rose-300">
            <ShieldAlert className="h-3 w-3" />
            Révision humaine requise
          </span>
        )}
      </div>

      {answer.requires_human_review && (
        <div className="rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          Cette réponse doit être validée par un juriste avant toute utilisation.
        </div>
      )}

      <div className="markdown-body">
        <ReactMarkdown>{answer.answer}</ReactMarkdown>
      </div>

      {answer.warnings.length > 0 && (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5" />
            Avertissements
          </p>
          <ul className="list-disc space-y-1 pl-4 text-xs text-amber-100">
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
                  ? "border-slate-600/40 bg-slate-800/40 text-slate-300"
                  : "border-amber-500/20 bg-amber-500/10 text-amber-100"
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
