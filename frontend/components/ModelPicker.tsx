"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Cpu } from "lucide-react";
import { listModels, setModel, type ModelInfo } from "@/lib/api";

interface ModelPickerProps {
  token: string | null;
  value: string | null;
  onChange: (model: string | null) => void;
}

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
          {models.map((model) => (
            <button
              key={model.id}
              type="button"
              onClick={() => select(model)}
              className="flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-gray-100"
            >
              <span className="flex min-w-0 items-center gap-2">
                <span className="truncate">{model.label}</span>
                {model.id === effective && (
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
