"use client";

import { useState } from "react";
import { ChevronDown, FileText, Layers } from "lucide-react";
import type { EvidenceChunk } from "@/lib/api";

interface EvidenceViewerProps {
  evidence: EvidenceChunk[];
}

const AUTHORITY_STYLES: Record<string, { label: string; classes: string }> = {
  constitution: { label: "Constitution", classes: "border-purple-400/30 bg-purple-400/10 text-purple-300" },
  treaty_ohada: { label: "Traité OHADA", classes: "border-indigo-400/30 bg-indigo-400/10 text-indigo-300" },
  amended_law: { label: "Loi modifiée", classes: "border-blue-400/30 bg-blue-400/10 text-blue-300" },
  law: { label: "Loi", classes: "border-blue-400/30 bg-blue-400/10 text-blue-300" },
  decree: { label: "Décret", classes: "border-sky-400/30 bg-sky-400/10 text-sky-300" },
  order: { label: "Arrêté", classes: "border-sky-400/30 bg-sky-400/10 text-sky-300" },
  ministerial_circular: { label: "Circulaire", classes: "border-cyan-400/30 bg-cyan-400/10 text-cyan-300" },
  official_gazette: { label: "Journal officiel", classes: "border-teal-400/30 bg-teal-400/10 text-teal-300" },
  case_law: { label: "Jurisprudence", classes: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300" },
  official_press_release: { label: "Communiqué officiel", classes: "border-green-400/30 bg-green-400/10 text-green-300" },
  official_news: { label: "Actualité officielle", classes: "border-green-400/30 bg-green-400/10 text-green-300" },
  uploaded_document: { label: "Document fourni", classes: "border-amber-400/30 bg-amber-400/10 text-amber-300" },
  trusted_legal_site: { label: "Site juridique", classes: "border-amber-400/30 bg-amber-400/10 text-amber-300" },
  news: { label: "Presse", classes: "border-orange-400/30 bg-orange-400/10 text-orange-300" },
  blog: { label: "Blog", classes: "border-slate-500/30 bg-slate-500/10 text-slate-400" },
  unknown: { label: "Inconnu", classes: "border-slate-500/30 bg-slate-500/10 text-slate-400" },
};

function AuthorityBadge({ authority }: { authority: string }) {
  const style = AUTHORITY_STYLES[authority] ?? AUTHORITY_STYLES.unknown;
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${style.classes}`}>
      {style.label}
    </span>
  );
}

function Score({ label, value, dash }: { label: string; value: number; dash?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      <span className={`text-xs font-medium ${dash ? "text-slate-500" : "text-slate-200"}`}>
        {dash ? "—" : value.toFixed(2)}
      </span>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value?: string | number | null }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      <span className="break-words text-xs text-slate-300">{String(value)}</span>
    </div>
  );
}

function EvidenceCard({ chunk, index }: { chunk: EvidenceChunk; index: number }) {
  const [open, setOpen] = useState(false);
  const title = chunk.document_name || "Document inconnu";
  // Backend stamps metadata.expansion = "parent" on chunks expanded to their
  // parent context around a retrieved excerpt.
  const expandedParent = chunk.metadata?.expansion === "parent";
  // Defensive: a displayed chunk whose three scores are all 0 shows "—".
  const allScoresZero =
    chunk.confidence === 0 && chunk.retrieval_score === 0 && chunk.rerank_score === 0;

  return (
    <li className="rounded-xl border border-slate-700/40 bg-surface-elevated">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start justify-between gap-2 p-3 text-left"
        aria-expanded={open}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileText className="h-3.5 w-3.5 shrink-0 text-slate-500" />
            <p className="truncate text-sm font-medium text-slate-200">
              {index + 1}. {title}
            </p>
            {expandedParent && (
              <span
                className="inline-flex shrink-0 items-center gap-1 rounded-full border border-law-purple/40 bg-law-purple/10 px-2 py-0.5 text-[10px] font-medium text-law-purple"
                title="Contexte élargi autour d'un extrait pertinent"
              >
                <Layers className="h-3 w-3" />
                Contexte élargi
              </span>
            )}
          </div>
          {chunk.article && <p className="mt-0.5 pl-5 text-xs text-slate-400">Article {chunk.article}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <AuthorityBadge authority={chunk.authority} />
          <ChevronDown
            className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </div>
      </button>
      {open && (
        <div className="border-t border-slate-700/40 p-3">
          <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-300">
            {chunk.content}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <MetaRow label="Document" value={chunk.document_name} />
            <MetaRow label="Article" value={chunk.article} />
            <MetaRow label="Section" value={chunk.section} />
            <MetaRow label="Page" value={chunk.page} />
            <MetaRow label="Publication" value={chunk.publication_date} />
            <MetaRow label="Entrée en vigueur" value={chunk.effective_date} />
            <MetaRow label="Organe" value={chunk.government_body} />
            <MetaRow label="Type de source" value={chunk.source_kind} />
            <MetaRow label="Version" value={chunk.version} />
            <Score label="Confiance" value={chunk.confidence} dash={allScoresZero} />
            <Score label="Score recherche" value={chunk.retrieval_score} dash={allScoresZero} />
            <Score label="Score reclassement" value={chunk.rerank_score} dash={allScoresZero} />
          </div>
          {chunk.url && (
            <a
              href={chunk.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 block truncate text-xs text-law-cyan hover:underline"
            >
              {chunk.url}
            </a>
          )}
        </div>
      )}
    </li>
  );
}

function hasNonZeroScore(chunk: EvidenceChunk): boolean {
  return chunk.confidence !== 0 || chunk.retrieval_score !== 0 || chunk.rerank_score !== 0;
}

/**
 * Pure-noise entries: all three scores at 0 AND no child chunk carries a
 * non-zero score either. These are expanded parents the backend could not
 * backfill with a best-child score — hiding them keeps the list meaningful.
 */
function isNoise(chunk: EvidenceChunk): boolean {
  if (hasNonZeroScore(chunk)) return false;
  return !(chunk.child_chunks ?? []).some(hasNonZeroScore);
}

export default function EvidenceViewer({ evidence }: EvidenceViewerProps) {
  const visible = evidence.filter((chunk) => !isNoise(chunk));
  return (
    <div className="p-4">
      <h3 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
        <span className="h-1.5 w-1.5 rounded-full bg-law-purple" />
        Preuves ({visible.length})
      </h3>
      {visible.length === 0 ? (
        <div className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-4 text-center">
          <p className="text-xs text-slate-400">Aucune preuve pour cette réponse.</p>
        </div>
      ) : (
        <ul className="space-y-2">
          {visible.map((chunk, i) => (
            <EvidenceCard key={chunk.chunk_id || i} chunk={chunk} index={i} />
          ))}
        </ul>
      )}
    </div>
  );
}
