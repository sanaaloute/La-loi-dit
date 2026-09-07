"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { ArrowRight, Loader2, Scale } from "lucide-react";
import { ConfidenceBadge } from "@/components/AnswerView";
import CitationPanel from "@/components/CitationPanel";
import { getSharedAnswer, type SharedAnswer } from "@/lib/api";
import { formatLongDate } from "@/lib/dates";

/**
 * PUBLIC read-only view of a shared answer (no auth — AuthGate exempts the
 * "/partage" prefix and GET /share/{token} is a public endpoint).
 */
export default function SharedAnswerClient() {
  const params = useParams<{ token: string }>();
  const shareToken = typeof params?.token === "string" ? params.token : "";
  const [shared, setShared] = useState<SharedAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!shareToken) return;
    let cancelled = false;
    getSharedAnswer(shareToken)
      .then((data) => {
        if (!cancelled) setShared(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Une erreur est survenue.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [shareToken]);

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="glass z-10 flex items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
            <Scale className="h-5 w-5 text-white" />
          </div>
          <div>
            <span className="block text-base font-semibold text-gray-900">
              Réponse partagée — Yawoto
            </span>
            <span className="block text-xs text-gray-500">Assistant juridique</span>
          </div>
        </div>
        <Link
          href="/"
          className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-accent-hover"
        >
          Poser une question
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        {error ? (
          <div className="mx-auto mt-16 max-w-md rounded-xl border border-red-700/30 bg-red-700/10 p-6 text-center">
            <p className="text-sm text-red-700">{error}</p>
            <Link
              href="/"
              className="mt-4 inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
            >
              Retour à l&apos;accueil
            </Link>
          </div>
        ) : shared === null ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin text-accent" />
            Chargement de la réponse partagée…
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-5">
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-2xl backdrop-blur-xl sm:p-6">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 pb-4">
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
                    Question
                  </p>
                  <p className="mt-1 text-sm font-medium text-gray-900">{shared.query}</p>
                </div>
                <ConfidenceBadge confidence={shared.confidence} />
              </div>
              <div className="markdown-body text-sm text-gray-700">
                <ReactMarkdown>{shared.answer}</ReactMarkdown>
              </div>
              <p className="mt-4 border-t border-gray-200 pt-3 text-[11px] text-gray-500">
                Réponse générée le {formatLongDate(shared.created_at)} — instantané partagé, les
                textes peuvent avoir évolué depuis.
              </p>
            </div>

            {shared.citations.length > 0 && (
              <div className="rounded-xl border border-gray-200 bg-white shadow-2xl backdrop-blur-xl">
                <CitationPanel citations={shared.citations} />
              </div>
            )}
          </div>
        )}
      </main>

      <footer className="glass z-10 px-4 py-2 sm:px-6">
        <p className="mx-auto max-w-3xl text-center text-[10px] text-gray-500">
          Avertissement : cette réponse est générée par un assistant automatisé à partir de textes
          officiels indexés. Elle ne constitue pas un conseil juridique.
        </p>
      </footer>
    </div>
  );
}
