// Persona: the user profile chosen during onboarding (stored server-side in
// the preferences as `persona`). Drives the chat suggestion prompts.
import type { ComponentProps } from "react";
import { Ionicons } from "@expo/vector-icons";
import * as SecureStore from "expo-secure-store";

/** SecureStore flag set once the onboarding has been shown (choice or skip). */
export const ONBOARDED_KEY = "yawoto-onboarded";

export type PersonaId = "etudiant" | "juriste" | "citoyen";

export type IoniconName = ComponentProps<typeof Ionicons>["name"];

export interface PersonaOption {
  id: PersonaId;
  label: string;
  description: string;
  icon: IoniconName;
  suggestions: string[];
}

export const PERSONA_OPTIONS: PersonaOption[] = [
  {
    id: "etudiant",
    label: "Étudiant en droit",
    description: "Explications pédagogiques, notions et définitions.",
    icon: "school-outline",
    suggestions: [
      "Explique-moi la notion de garde à vue en procédure pénale.",
      "Quelle est la différence entre une SARL et une SA en droit OHADA ?",
      "Qu'est-ce que la force majeure en droit des contrats ?",
    ],
  },
  {
    id: "juriste",
    label: "Juriste ou avocat",
    description: "Procédures, délais et références précises.",
    icon: "briefcase-outline",
    suggestions: [
      "Quel est le délai pour former appel d'un jugement civil au Burkina Faso ?",
      "Quelle est la procédure de licenciement pour motif économique ?",
      "Quelle procédure suivre pour contester un licenciement devant le tribunal du travail ?",
    ],
  },
  {
    id: "citoyen",
    label: "Citoyen",
    description: "Vos droits et démarches, en langage simple.",
    icon: "person-outline",
    suggestions: [
      "Quels sont mes droits si je suis arrêté par la police ?",
      "Mon employeur peut-il me licencier sans préavis ?",
      "Comment signaler un litige de voisinage à la justice ?",
    ],
  },
];

export function personaOption(id: string | null | undefined): PersonaOption | null {
  return PERSONA_OPTIONS.find((o) => o.id === id) ?? null;
}

export function personaLabel(id: string | null | undefined): string {
  return personaOption(id)?.label ?? "Non défini";
}

/** Persona-specific chat suggestions, or null to keep the defaults. */
export function suggestionsForPersona(id: string | null | undefined): string[] | null {
  return personaOption(id)?.suggestions ?? null;
}

/** True once the onboarding has been shown on this device. */
export async function hasOnboarded(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(ONBOARDED_KEY)) !== null;
  } catch {
    return false;
  }
}

/** Remember that the onboarding has been shown (choice made or skipped). */
export function markOnboarded(): Promise<void> {
  return SecureStore.setItemAsync(ONBOARDED_KEY, "1").catch(() => {});
}
