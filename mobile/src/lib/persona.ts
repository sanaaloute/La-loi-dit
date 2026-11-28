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
      "Quelle est la différence entre une loi et un décret au Burkina Faso ?",
      "Explique-moi la distinction entre adoption, promulgation et entrée en vigueur d'une loi.",
      "Qu'est-ce qu'une ordonnance et en quoi diffère-t-elle d'une loi ?",
      "Quels sont les organes de la justice au Burkina Faso ?",
      "Qu'est-ce que la CCJA et quel est son rôle dans l'espace OHADA ?",
      "Explique-moi le régime des collectivités locales au Burkina Faso.",
      "Qu'est-ce que la présomption d'innocence et où est-elle garantie ?",
      "Quelle différence entre le droit national et les actes uniformes de l'OHADA ?",
      "Qu'est-ce que la hiérarchie des normes ?",
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
      "Quel est le délai de recours contre un licenciement au Burkina Faso ?",
      "Quelle est la procédure de saisine de la CCJA en matière OHADA ?",
      "Quels sont les délais de prescription en matière civile ?",
      "Quels sont les motifs légitimes de licenciement selon le Code du travail ?",
      "Quel est le régime des sûretés mobilières selon l'AUS révisé ?",
      "Quelles sont les conditions de recevabilité d'un recours en annulation administrative ?",
      "Quelles sont les règles de fixation et de révision des loyers ?",
      "Comment sont organisées les procédures collectives en droit OHADA ?",
      "Quelles sont les nouveautés du Code du travail de 2026 ?",
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
      "Quels sont mes droits si mon employeur me licencie ?",
      "Comment faire reconnaître un enfant au Burkina Faso ?",
      "Quelles sont les démarches pour obtenir un acte de naissance ?",
      "Que faire en cas de non-paiement de mon salaire ?",
      "Quels sont les droits des époux en cas de divorce ?",
      "Comment régler un conflit de terrain avec mon voisin ?",
      "Quelles protections légales pour les victimes de violences ?",
      "Quels documents faut-il pour un mariage civil ?",
      "Comment déposer une plainte à la police ou à la gendarmerie ?",
    ],
  },
];

export function personaOption(id: string | null | undefined): PersonaOption | null {
  return PERSONA_OPTIONS.find((o) => o.id === id) ?? null;
}

export function personaLabel(id: string | null | undefined): string {
  return personaOption(id)?.label ?? "Non défini";
}

/** Persona-specific chat suggestion pool, or null to keep the defaults. */
export function suggestionsForPersona(id: string | null | undefined): string[] | null {
  return personaOption(id)?.suggestions ?? null;
}

/** Fisher-Yates sample of n items from a pool (no replacement). */
export function sampleSuggestions(pool: string[], n = 3): string[] {
  const copy = [...pool];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy.slice(0, Math.min(n, copy.length));
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
