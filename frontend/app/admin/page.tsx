"use client";

import { useEffect, useState } from "react";
import { Cpu, FileText, Gauge, LayoutDashboard, Users } from "lucide-react";
import AppHeader from "@/components/AppHeader";
import DocumentsTab from "@/components/admin/DocumentsTab";
import OverviewTab from "@/components/admin/OverviewTab";
import ProvidersTab from "@/components/admin/ProvidersTab";
import QuotasTab from "@/components/admin/QuotasTab";
import UsersTab from "@/components/admin/UsersTab";
import ErrorCard from "@/components/ui/ErrorCard";
import GatePanel from "@/components/ui/GatePanel";
import LoadingState from "@/components/ui/LoadingState";
import PageShell from "@/components/ui/PageShell";
import { useAuthToken } from "@/lib/useAuth";
import { me, type UserProfile } from "@/lib/api";

type TabId = "apercu" | "documents" | "utilisateurs" | "quotas" | "fournisseurs";

const TABS: { id: TabId; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "apercu", label: "Aperçu", icon: LayoutDashboard },
  { id: "documents", label: "Documents", icon: FileText },
  { id: "utilisateurs", label: "Utilisateurs", icon: Users },
  { id: "quotas", label: "Quotas", icon: Gauge },
  { id: "fournisseurs", label: "Fournisseurs", icon: Cpu },
];

export default function AdminPage() {
  const [token] = useAuthToken();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("apercu");

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    me(token)
      .then((p) => {
        if (!cancelled) setProfile(p);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Une erreur est survenue.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const isAdmin = profile?.role === "admin";

  return (
    <PageShell
      header={<AppHeader token={token} />}
      disclaimer="Administration — les actions effectuées ici (ingestion, suppression, rôles) sont immédiates."
    >
      {!token ? (
        <GatePanel body="Connectez-vous depuis l'icône de compte en haut à droite pour accéder au tableau de bord d'administration." />
      ) : error ? (
        <ErrorCard message={error}>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
          >
            Réessayer
          </button>
        </ErrorCard>
      ) : loading || !profile ? (
        <LoadingState label="Vérification des droits d'accès…" />
      ) : !isAdmin ? (
        <GatePanel
          title="Accès restreint"
          body="Ce tableau de bord est réservé aux administrateurs. Votre compte ne dispose pas du rôle requis."
        />
      ) : (
        <div className="mx-auto max-w-5xl space-y-5">
          {/* Tab bar */}
          <div className="flex flex-wrap gap-1 rounded-xl border border-gray-200 bg-white p-1.5 shadow-2xl backdrop-blur-xl">
            {TABS.map((t) => {
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                    active
                      ? "border-accent/40 bg-accent/10 text-accent"
                      : "border-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                  }`}
                >
                  <t.icon className="h-4 w-4" />
                  {t.label}
                </button>
              );
            })}
          </div>

          {tab === "apercu" && <OverviewTab />}
          {tab === "documents" && <DocumentsTab />}
          {tab === "utilisateurs" && <UsersTab />}
          {tab === "quotas" && <QuotasTab />}
          {tab === "fournisseurs" && <ProvidersTab />}
        </div>
      )}
    </PageShell>
  );
}
