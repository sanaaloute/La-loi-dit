"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Cpu } from "lucide-react";
import { listModels, setModel, type ModelInfo, type Tier } from "@/lib/api";

interface ModelPickerProps {
  token: string | null;
  value: string | null;
  onChange: (model: string | null) => void;
}

const TIER_BADGES: Partial<Record<Tier, { label: string; className: string }>> = {
  pro: { label: "Pro", className: "border-accent/40 bg-accent/10 text-accent" },
  cabinet: { label: "Cabinet", className: "border-ink/40 bg-ink/10 text-ink" },
};

export default function ModelPicker({ token, value, onChange }: ModelPickerProps) {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!token) {
      setModels(null);
      setDefaultModel(null);
      return;
    }
    let cancelled = false;
    listModels(token)
      .then((res) => {
        if (!cancelled) {
          setModels(res.models);
          setDefaultModel(res.default_model);
        }
      })
      .catch(() => {
        if (!cancelled) setModels(null);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Hidden when logged out or when the model list cannot be loaded.
  if (!token || !models || models.length === 0) return null;

  // No explicit choice: the backend default (Ollama Cloud) applies — show it
  // as the effective selection so the picker reflects what actually runs.
  const effective = value ?? defaultModel;
  const current = models.find((m) => m.id === effective);

  function select(model: ModelInfo) {
    if (!model.allowed) return;
    setModel(model.id);
    onChange(model.id);
    setOpen(false);
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700 backdrop-blur-sm transition-colors hover:border-gray-400 hover:bg-gray-100"
        title="Choisir le modèle"
      >
        <Cpu className="h-4 w-4 text-accent" />
        {/* Icon-only on small screens: the header is crowded on phones. */}
        <span className="hidden max-w-40 truncate sm:inline">{current?.label ?? "Modèle"}</span>
        <ChevronDown className="h-3.5 w-3.5 text-gray-500" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-64 rounded-xl border border-gray-200 bg-white p-1.5 shadow-2xl backdrop-blur-xl">
          {models.map((model) => {
            const badge = model.allowed ? undefined : TIER_BADGES[model.tier_required];
            return (
              <button
                key={model.id}
                type="button"
                onClick={() => select(model)}
                disabled={!model.allowed}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  model.allowed
                    ? "text-gray-700 hover:bg-gray-100"
                    : "cursor-not-allowed text-gray-500"
                }`}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{model.label}</span>
                  {model.id === effective && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                  )}
                </span>
                {badge && (
                  <span
                    className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}
                  >
                    {badge.label}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
