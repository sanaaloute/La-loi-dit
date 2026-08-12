"use client";

import { useEffect, useRef, useState } from "react";
import { Download, FileCode, FileSpreadsheet, FileText, Loader2 } from "lucide-react";
import {
  downloadBlob,
  exportAnswer,
  exportMarkdown,
  type ChatResponse,
  type ExportFormat,
  type ExportItem,
} from "@/lib/api";

interface ExportMenuProps {
  response: ChatResponse;
  /** The user question this response answers. */
  query: string;
  /** All question/answer exchanges of the conversation (conversation scope). */
  conversation?: ExportItem[];
  /** Fixed scope: no toggle is shown. Default "response". */
  scope?: Scope;
  /** Icon-only trigger for inline placement under a message. */
  iconOnly?: boolean;
}

type MenuFormat = ExportFormat | "md";
type Scope = "response" | "conversation";

const FORMATS: { id: MenuFormat; label: string; icon: React.ElementType; ext: string }[] = [
  { id: "pdf", label: "PDF", icon: FileText, ext: "pdf" },
  { id: "word", label: "Word", icon: FileText, ext: "docx" },
  { id: "csv", label: "CSV", icon: FileSpreadsheet, ext: "csv" },
  { id: "md", label: "Markdown (.md)", icon: FileCode, ext: "md" },
];

export default function ExportMenu({
  response,
  query,
  conversation = [],
  scope: scopeProp,
  iconOnly = false,
}: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState<MenuFormat | null>(null);
  const [scope, setScope] = useState<Scope>(scopeProp ?? "response");
  const rootRef = useRef<HTMLDivElement>(null);

  const canExportConversation = conversation.length > 0;
  const effectiveScope: Scope =
    scopeProp ?? (scope === "conversation" && canExportConversation ? "conversation" : "response");

  // Close the dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  async function handleExport(format: MenuFormat) {
    setLoading(format);
    try {
      const items: ExportItem[] =
        effectiveScope === "conversation" && canExportConversation
          ? conversation
          : [{ query, answer: response.answer }];
      const payload = {
        query,
        answer: response.answer,
        items,
        session_id: response.session_id,
        latency_ms: response.latency_ms,
      };
      const blob =
        format === "md" ? await exportMarkdown(payload) : await exportAnswer(format, payload);
      const prefix = items.length > 1 ? "conversation-juridique" : "reponse-juridique";
      const filename = `${prefix}-${response.session_id.slice(0, 8)}.${FORMATS.find((f) => f.id === format)?.ext}`;
      downloadBlob(blob, filename);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Échec de l'export");
    } finally {
      setLoading(null);
      setOpen(false);
    }
  }

  const label = effectiveScope === "conversation" ? "Exporter la conversation" : "Exporter la réponse";

  return (
    <div ref={rootRef} className="relative">
      {iconOnly ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((v) => !v);
          }}
          className="rounded p-1 text-gray-500 transition-colors hover:text-gray-600"
          title={label}
          aria-label={label}
        >
          <Download className="h-3.5 w-3.5" />
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:border-accent/40 hover:bg-gray-100"
        >
          <Download className="h-4 w-4" />
          {label}
        </button>
      )}
      {open && (
        <div
          className={`absolute bottom-full z-50 mb-2 rounded-xl border border-gray-200 bg-white p-1.5 shadow-2xl backdrop-blur-xl ${
            iconOnly ? "left-0 w-44" : "left-0 right-0"
          }`}
        >
          {!scopeProp && canExportConversation && (
            <div className="mb-1.5 flex gap-1 rounded-lg bg-gray-100 p-1">
              <button
                type="button"
                onClick={() => setScope("response")}
                className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
                  scope === "response"
                    ? "bg-white text-accent shadow-sm"
                    : "text-gray-600 hover:text-gray-900"
                }`}
              >
                Cette réponse
              </button>
              <button
                type="button"
                onClick={() => setScope("conversation")}
                className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
                  scope === "conversation"
                    ? "bg-white text-accent shadow-sm"
                    : "text-gray-600 hover:text-gray-900"
                }`}
              >
                Conversation ({conversation.length})
              </button>
            </div>
          )}
          {FORMATS.map((format) => (
            <button
              key={format.id}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                void handleExport(format.id);
              }}
              disabled={loading !== null}
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-50"
            >
              {loading === format.id ? (
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
              ) : (
                <format.icon className="h-4 w-4 text-accent" />
              )}
              {format.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
