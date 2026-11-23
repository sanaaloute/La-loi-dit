"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import ChatWindow from "@/components/ChatWindow";

/** Reads the deep-link query params (?q=, ?suggestion=, ?session=,
 * ?historique=1) in a Suspense boundary, as useSearchParams requires. */
function RechercheInner() {
  const params = useSearchParams();
  return (
    <ChatWindow
      initialQuery={params.get("q")}
      suggestion={params.get("suggestion")}
      sessionParam={params.get("session")}
      openHistory={params.get("historique") === "1"}
    />
  );
}

export default function RecherchePage() {
  return (
    <AppShell fill>
      <Suspense>
        <RechercheInner />
      </Suspense>
    </AppShell>
  );
}
