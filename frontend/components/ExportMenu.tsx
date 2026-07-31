"use client";

import { useState } from "react";
import { Download, FileCode, FileSpreadsheet, FileText, Loader2 } from "lucide-react";
import { downloadBlob, exportAnswer, exportMarkdown, type ChatResponse, type ExportFormat } from "@/lib/api";

interface ExportMenuProps {
  response: ChatResponse;
  query: string;
}

type MenuFormat = ExportFormat | "md";

const FORMATS: { id: MenuFormat; label: string; icon: React.ElementType; ext: string }[] = [
  { id: "pdf", label: "PDF", icon: FileText, ext: "pdf" },
  { id: "word", label: "Word", icon: FileText, ext: "docx" },
  { id: "csv", label: "CSV", icon: FileSpreadsheet, ext: "csv" },
  { id: "md", label: "Markdown (.md)", icon: FileCode, ext: "md" },
];

export default function ExportMenu({ response, query }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState<MenuFormat | null>(null);

  async function handleExport(format: MenuFormat) {
    setLoading(format);
    try {
      const payload = {
        query,
        answer: response.answer,
        session_id: response.session_id,
        latency_ms: response.latency_ms,
      };
      const blob =
        format === "md" ? await exportMarkdown(payload) : await exportAnswer(format, payload);
      const filename = `reponse-juridique-${response.session_id.slice(0, 8)}.${FORMATS.find((f) => f.id === format)?.ext}`;
      downloadBlob(blob, filename);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Échec de l'export");
    } finally {
      setLoading(null);
      setOpen(false);
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-600/40 bg-slate-800/50 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:border-law-cyan/40 hover:bg-slate-700/50"
      >
        <Download className="h-4 w-4" />
        Exporter la réponse
      </button>
      {open && (
        <div className="absolute bottom-full left-0 right-0 z-50 mb-2 rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-1.5 shadow-2xl backdrop-blur-xl">
          {FORMATS.map((format) => (
            <button
              key={format.id}
              type="button"
              onClick={() => void handleExport(format.id)}
              disabled={loading !== null}
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-white/5 disabled:opacity-50"
            >
              {loading === format.id ? (
                <Loader2 className="h-4 w-4 animate-spin text-law-cyan" />
              ) : (
                <format.icon className="h-4 w-4 text-law-cyan" />
              )}
              {format.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
