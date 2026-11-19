"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { AlertTriangle, CheckCircle2, ChevronDown, ShieldAlert, ShieldCheck } from "lucide-react";
import type { Claim, ConfidenceBreakdown, FinalAnswer, SupportLevel } from "@/lib/api";

interface AnswerViewProps {
  answer: FinalAnswer;
}

export function ConfidenceBadge({ confidence }: { confidence: number }) {
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

const SUPPORT_STYLES: Record<SupportLevel, { label: string; classes: string }> = {
  direct: {
    label: "Vérifiée",
    classes: "border-accent/30 bg-accent/10 text-accent",
  },
  indirect: {
    label: "Déduction",
    classes: "border-gray-300 bg-gray-100 text-gray-700",
  },
  insufficient: {
    label: "Non vérifiée",
    classes: "border-warn-border/60 bg-warn-bg text-warn-text",
  },
  contradictory: {
    label: "Contradiction",
    classes: "border-red-700/30 bg-red-700/10 text-red-700",
  },
};

function BreakdownBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[11px] text-gray-600">
        <span>{label}</span>
        <span className="font-medium text-gray-800">{pct}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-200">
        <div
          className={`h-full rounded-full ${pct >= 65 ? "bg-accent" : pct >= 45 ? "bg-amber-500" : "bg-red-600"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ReliabilityDetails({
  breakdown,
  claims,
}: {
  breakdown?: ConfidenceBreakdown | null;
  claims?: Claim[];
}) {
  const [open, setOpen] = useState(false);
  const hasClaims = Boolean(claims && claims.length > 0);
  if (!breakdown && !hasClaims) return null;

  const directCount = claims?.filter((c) => c.support_level === "direct").length ?? 0;
  const indirectCount = claims?.filter((c) => c.support_level === "indirect").length ?? 0;
  const unverifiedCount =
    claims?.filter((c) => c.support_level === "insufficient" || c.support_level === "contradictory")
      .length ?? 0;

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50/70">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-gray-600 hover:text-gray-900"
        aria-expanded={open}
      >
        <span className="flex items-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5 text-accent" />
          Preuves &amp; fiabilité
          {hasClaims && (
            <span className="text-[11px] font-normal text-gray-500">
              — {directCount + indirectCount}/{claims?.length} affirmations appuyées
              {unverifiedCount > 0 ? `, ${unverifiedCount} à vérifier` : ""}
            </span>
          )}
        </span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-gray-200 px-3 py-2.5">
          {breakdown && (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <BreakdownBar label="Autorité des sources" value={breakdown.source_confidence} />
              <BreakdownBar label="Pertinence recherche" value={breakdown.retrieval_confidence} />
              <BreakdownBar label="Support juridique" value={breakdown.legal_support_confidence} />
              <BreakdownBar label="Validité temporelle" value={breakdown.temporal_confidence} />
              <BreakdownBar label="Exactitude citations" value={breakdown.citation_confidence} />
              <BreakdownBar label="Couverture des sous-questions" value={breakdown.coverage} />
            </div>
          )}
          {hasClaims && claims && (
            <div className="space-y-1.5 pt-1">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                Vérification des affirmations ({claims.length})
              </p>
              <ul className="max-h-48 space-y-1.5 overflow-y-auto pr-1">
                {claims.map((claim) => {
                  const style = SUPPORT_STYLES[claim.support_level] ?? SUPPORT_STYLES.insufficient;
                  return (
                    <li
                      key={claim.claim_id}
                      className="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-gray-700">{claim.text}</p>
                        <span
                          className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${style.classes}`}
                        >
                          {style.label}
                        </span>
                      </div>
                      {claim.verification_note && (
                        <p className="mt-1 text-[11px] italic text-gray-500">
                          {claim.verification_note}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AnswerView({ answer }: AnswerViewProps) {
  // Conversation, general knowledge, and confidence fallbacks have no
  // meaningful retrieval-confidence score to display.
  const isUnscored =
    answer.metadata?.route === "direct" ||
    answer.metadata?.route === "general" ||
    answer.metadata?.low_confidence_fallback === true;
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
      {!isUnscored && (
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

      {!isUnscored && (
        <ReliabilityDetails breakdown={answer.confidence_breakdown} claims={answer.claims} />
      )}

      {/* Generic AI disclaimer on every answer: the pipeline only surfaces
          what it could verify, so unverified affirmations are not listed —
          this note is the honest user-facing caveat. */}
      <p className="text-[11px] leading-snug text-gray-400">
        {(answer.language || "fr").startsWith("en")
          ? "AI-generated answer — it may contain errors. Always double-check with a legal professional."
          : "Réponse générée par une IA — elle peut contenir des erreurs. Vérifiez toujours auprès d'un professionnel du droit."}
      </p>

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
