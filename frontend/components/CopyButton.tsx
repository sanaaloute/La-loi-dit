"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

/** Copies the given text to the clipboard; shows a checkmark on success. */
export default function CopyButton({ text, label = "Copier la réponse" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy(e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable (permissions / non-secure context) — no-op.
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`rounded p-1 transition-colors ${
        copied ? "text-accent" : "text-gray-500 hover:text-gray-600"
      }`}
      title={copied ? "Copié !" : label}
      aria-label={copied ? "Copié !" : label}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}
