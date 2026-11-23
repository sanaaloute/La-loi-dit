"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import LoadingState from "@/components/ui/LoadingState";

/** Marque-pages now live under Mes dossiers (/dossiers, tab « Marque-pages »). */
export default function MarquesPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/dossiers?tab=marques");
  }, [router]);
  return (
    <AppShell>
      <LoadingState label="Redirection vers vos dossiers…" />
    </AppShell>
  );
}
