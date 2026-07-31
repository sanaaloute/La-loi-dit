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
  pro: { label: "Pro", className: "border-law-cyan/40 bg-law-cyan/10 text-law-cyan" },
  cabinet: { label: "Cabinet", className: "border-law-purple/40 bg-law-purple/10 text-law-purple" },
};

export default function ModelPicker({ token, value, onChange }: ModelPickerProps) {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!token) {
      setModels(null);
      return;
    }
    let cancelled = false;
    listModels(token)
      .then((res) => {
        if (!cancelled) setModels(res.models);
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

  const current = models.find((m) => m.id === value);

  function select(model: ModelInfo) {
    if (!model.allowed) return;
    setModel(model.id);
    onChange(model.id);
    setOpen(false);
  }

  return (
    <div ref={ref} className="relative hidden sm:block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-2 text-xs font-medium text-slate-200 backdrop-blur-sm transition-colors hover:border-slate-500 hover:bg-slate-700/60"
        title="Choisir le modèle"
      >
        <Cpu className="h-4 w-4 text-law-cyan" />
        <span className="max-w-40 truncate">{current?.label ?? "Modèle"}</span>
        <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-64 rounded-xl border border-slate-600/40 bg-[#0f172a]/95 p-1.5 shadow-2xl backdrop-blur-xl">
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
                    ? "text-slate-200 hover:bg-white/5"
                    : "cursor-not-allowed text-slate-500"
                }`}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{model.label}</span>
                  {model.id === value && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-law-cyan" />
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
