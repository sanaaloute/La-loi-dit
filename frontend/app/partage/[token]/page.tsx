import type { Metadata } from "next";
import SharedAnswerClient from "./SharedAnswerClient";

// Public, unguessable-token pages: never indexed.
export const metadata: Metadata = {
  title: "Réponse partagée — Yawoto",
  robots: { index: false, follow: false },
};

export default function PartagePage() {
  return <SharedAnswerClient />;
}
