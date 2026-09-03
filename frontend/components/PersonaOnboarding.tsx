"use client";

import { useEffect, useState } from "react";
import { Briefcase, GraduationCap, Loader2, User, Users } from "lucide-react";
import { getPreferences, putPreferences } from "@/lib/api";
import {
  notifyPersonaChanged,
  readPersona,
  PERSONA_LABELS,
  type PersonaKey,
} from "@/lib/persona";

const PERSONA_CARDS: { key: PersonaKey; icon: React.ElementType; hint: string }[] = [
  { key: "etudiant", icon: GraduationCap, hint: "Explications et notions" },
  { key: "juriste", icon: Briefcase, hint: "Procédures et délais" },
  { key: "citoyen", icon: Users, hint: "Vos droits au quotidien" },
  { key: "autre", icon: User, hint: "Usage général" },
];

/**
 * One-time onboarding modal: the first time an account logs in (no `persona`
 * stored in the user preferences), ask who they are so the assistant can
 * adapt its suggestions. Rendered once per account — the choice is persisted
 * server-side, so the modal never reappears afterwards.
 */
export default function PersonaOnboarding({ token }: { token: string | null }) {
  const [visible, setVisible] = useState(false);
  const [saving, setSaving] = useState<PersonaKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setVisible(false);
      return;
    }
    let cancelled = false;
    getPreferences(token)
      .then((res) => {
        if (!cancelled) setVisible(readPersona(res.preferences) === null);
      })
      .catch(() => {
        // Preferences unreadable (e.g. memory store down): skip onboarding.
        if (!cancelled) setVisible(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function choose(persona: PersonaKey) {
    if (!token || saving) return;
    setSaving(persona);
    setError(null);
    try {
      await putPreferences({ persona }, token);
      notifyPersonaChanged(persona);
      setVisible(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setSaving(null);
    }
  }

  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="persona-onboarding-title"
        className="w-full max-w-lg rounded-2xl border border-gray-200 bg-white p-6 shadow-2xl"
      >
        <h2 id="persona-onboarding-title" className="text-lg font-semibold text-gray-900">
          Bienvenue sur Yawoto
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Vous êtes… — une seule question, pour adapter les suggestions de l&apos;assistant à
          votre profil. Modifiable à tout moment dans votre compte.
        </p>
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {PERSONA_CARDS.map(({ key, icon: Icon, hint }) => (
            <button
              key={key}
              type="button"
              onClick={() => void choose(key)}
              disabled={saving !== null}
              className="flex items-start gap-3 rounded-xl border border-gray-200 bg-gray-50 p-4 text-left transition-all hover:border-accent/50 hover:bg-gray-100 disabled:opacity-60"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                {saving === key ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
              </span>
              <span>
                <span className="block text-sm font-medium text-gray-900">
                  {PERSONA_LABELS[key]}
                </span>
                <span className="mt-0.5 block text-xs text-gray-500">{hint}</span>
              </span>
            </button>
          ))}
        </div>
        {error && <p className="mt-3 text-xs text-red-700">{error}</p>}
      </div>
    </div>
  );
}
