// User persona (onboarding profile) shared by the onboarding modal, the chat
// suggestions and the account page.

export type PersonaKey = "etudiant" | "juriste" | "citoyen" | "autre";

export const PERSONA_LABELS: Record<PersonaKey, string> = {
  etudiant: "Étudiant en droit",
  juriste: "Juriste ou avocat",
  citoyen: "Citoyen",
  autre: "Autre",
};

/** Window event dispatched after the persona is saved, so open views refresh. */
export const PERSONA_CHANGED_EVENT = "yawoto-persona-changed";

export function notifyPersonaChanged(persona: PersonaKey): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PERSONA_CHANGED_EVENT, { detail: { persona } }));
}

/** Read a validated persona key out of a preferences record (null if absent). */
export function readPersona(preferences: Record<string, unknown>): PersonaKey | null {
  const persona = preferences.persona;
  return typeof persona === "string" && persona in PERSONA_LABELS
    ? (persona as PersonaKey)
    : null;
}
