"use client";

import { useState } from "react";
import { ChevronDown, FileText, Layers } from "lucide-react";
import type { EvidenceChunk } from "@/lib/api";

interface EvidenceViewerProps {
  evidence: EvidenceChunk[];
}

// Neutral badge for every authority level: the label carries the meaning,
// the palette stays restrained (no multi-hue "soup").
const AUTHORITY_BADGE_CLASSES = "border-gray-300 bg-gray-50 text-gray-600";

const AUTHORITY_STYLES: Record<string, { label: string; classes: string }> = {
  constitution: { label: "Constitution", classes: AUTHORITY_BADGE_CLASSES },
  treaty_ohada: { label: "Traité OHADA", classes: AUTHORITY_BADGE_CLASSES },
  amended_law: { label: "Loi modifiée", classes: AUTHORITY_BADGE_CLASSES },
  law: { label: "Loi", classes: AUTHORITY_BADGE_CLASSES },
  decree: { label: "Décret", classes: AUTHORITY_BADGE_CLASSES },
  order: { label: "Arrêté", classes: AUTHORITY_BADGE_CLASSES },
  ministerial_circular: { label: "Circulaire", classes: AUTHORITY_BADGE_CLASSES },
  official_gazette: { label: "Journal officiel", classes: AUTHORITY_BADGE_CLASSES },
  case_law: { label: "Jurisprudence", classes: AUTHORITY_BADGE_CLASSES },
  official_press_release: { label: "Communiqué officiel", classes: AUTHORITY_BADGE_CLASSES },
  official_news: { label: "Actualité officielle", classes: AUTHORITY_BADGE_CLASSES },
  uploaded_document: { label: "Document fourni", classes: AUTHORITY_BADGE_CLASSES },
  trusted_legal_site: { label: "Site juridique", classes: AUTHORITY_BADGE_CLASSES },
  news: { label: "Presse", classes: AUTHORITY_BADGE_CLASSES },
  blog: { label: "Blog", classes: AUTHORITY_BADGE_CLASSES },
  unknown: { label: "Inconnu", classes: AUTHORITY_BADGE_CLASSES },
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
      <span className="text-[10px] uppercase tracking-wide text-gray-500">{label}</span>
      <span className={`text-xs font-medium ${dash ? "text-gray-500" : "text-gray-700"}`}>
        {dash ? "—" : value.toFixed(2)}
      </span>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value?: string | number | null }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-gray-500">{label}</span>
      <span className="break-words text-xs text-gray-600">{String(value)}</span>
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
    <li className="rounded-xl border border-gray-200 bg-surface-elevated">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start justify-between gap-2 p-3 text-left"
        aria-expanded={open}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileText className="h-3.5 w-3.5 shrink-0 text-gray-500" />
            <p className="truncate text-sm font-medium text-gray-700">
              {index + 1}. {title}
            </p>
            {expandedParent && (
              <span
                className="inline-flex shrink-0 items-center gap-1 rounded-full border border-ink/40 bg-ink/10 px-2 py-0.5 text-[10px] font-medium text-ink"
                title="Contexte élargi autour d'un extrait pertinent"
              >
                <Layers className="h-3 w-3" />
                Contexte élargi
              </span>
            )}
          </div>
          {chunk.article && <p className="mt-0.5 pl-5 text-xs text-gray-500">Article {chunk.article}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <AuthorityBadge authority={chunk.authority} />
          <ChevronDown
            className={`h-4 w-4 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </div>
      </button>
      {open && (
        <div className="border-t border-gray-200 p-3">
          <p className="whitespace-pre-wrap text-xs leading-relaxed text-gray-600">
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
              className="mt-2 block truncate text-xs text-accent hover:underline"
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
      <h3 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        <span className="h-1.5 w-1.5 rounded-full bg-ink" />
        Preuves ({visible.length})
      </h3>
      {visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-center">
          <p className="text-xs text-gray-500">Aucune preuve pour cette réponse.</p>
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
