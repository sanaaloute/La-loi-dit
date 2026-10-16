"use client";

import { useEffect, useState } from "react";
import ErrorCard from "@/components/ui/ErrorCard";
import LoadingState from "@/components/ui/LoadingState";
import {
  adminApi,
  type AdminRole,
  type AdminUsageResponse,
  type AdminUserEntry,
  type AdminUserPatch,
} from "@/lib/api";
import { EmptyState, INPUT_CLASS, SectionCard, TableShell, Td, Th, THead, formatNumber } from "./ui";

const ROLE_OPTIONS: { value: AdminRole; label: string }[] = [
  { value: "viewer", label: "Lecteur" },
  { value: "user", label: "Utilisateur" },
  { value: "legal_expert", label: "Expert juridique" },
  { value: "admin", label: "Administrateur" },
];

function formatDate(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

export default function UsersTab() {
  const [users, setUsers] = useState<AdminUserEntry[] | null>(null);
  const [usage, setUsage] = useState<AdminUsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [u, a] = await Promise.all([adminApi.users(), adminApi.usage(30)]);
      setUsers(u.users);
      setUsage(a);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function patchUser(id: string, body: AdminUserPatch) {
    setSavingId(id);
    setActionError(null);
    try {
      const updated = await adminApi.patchUser(id, body);
      setUsers((prev) => prev?.map((u) => (u.id === id ? updated : u)) ?? null);
    } catch (err) {
      // e.g. 400 "an admin cannot change their own role"
      setActionError(err instanceof Error ? err.message : "Échec de la mise à jour de l'utilisateur.");
    } finally {
      setSavingId(null);
    }
  }

  async function toggleActive(user: AdminUserEntry) {
    setSavingId(user.id);
    setActionError(null);
    try {
      const updated = user.is_active
        ? await adminApi.suspendUser(user.id)
        : await adminApi.reactivateUser(user.id);
      setUsers((prev) => prev?.map((u) => (u.id === user.id ? updated : u)) ?? null);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de la modification du statut du compte.");
    } finally {
      setSavingId(null);
    }
  }

  async function deleteUser(user: AdminUserEntry) {
    const confirmed = window.confirm(
      `Supprimer définitivement ${user.email} et toutes les données associées ? Cette action est irréversible.`,
    );
    if (!confirmed) return;
    setSavingId(user.id);
    setActionError(null);
    try {
      await adminApi.deleteUser(user.id);
      setUsers((prev) => prev?.filter((u) => u.id !== user.id) ?? null);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Échec de la suppression du compte.");
    } finally {
      setSavingId(null);
    }
  }

  if (loading) return <LoadingState label="Chargement des utilisateurs…" />;
  if (error) {
    return (
      <ErrorCard message={error}>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100"
        >
          Réessayer
        </button>
      </ErrorCard>
    );
  }

  const usageRows = [...(usage?.per_user ?? [])].sort(
    (a, b) => b.tokens_in + b.tokens_out - (a.tokens_in + a.tokens_out),
  );
  const totals = usage?.totals ?? {};

  return (
    <div className="space-y-5">
      {actionError && <ErrorCard message={actionError} />}

      <SectionCard title={`Comptes utilisateurs${users ? ` (${users.length})` : ""}`}>
        {!users || users.length === 0 ? (
          <EmptyState message="Aucun utilisateur enregistré." />
        ) : (
          <TableShell>
            <THead>
              <tr>
                <Th>E-mail</Th>
                <Th>Nom</Th>
                <Th>Rôle</Th>
                <Th>Statut</Th>
                <Th>Actions</Th>
                <Th>Créé le</Th>
                <Th>Tokens (jour)</Th>
                <Th>Requêtes (jour)</Th>
              </tr>
            </THead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <Td className="text-xs">{u.email}</Td>
                  <Td className="text-xs">{u.name || "—"}</Td>
                  <Td>
                    <select
                      value={u.role}
                      onChange={(e) => void patchUser(u.id, { role: e.target.value as AdminRole })}
                      disabled={savingId === u.id}
                      aria-label={`Rôle de ${u.email}`}
                      className={INPUT_CLASS}
                    >
                      {ROLE_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                      {!ROLE_OPTIONS.some((o) => o.value === u.role) && (
                        <option value={u.role}>{u.role}</option>
                      )}
                    </select>
                  </Td>
                  <Td>
                    <span
                      className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                        u.is_active
                          ? "border-accent/40 bg-accent/10 text-accent"
                          : "border-warn-border/60 bg-warn-bg text-warn-text"
                      }`}
                    >
                      {u.is_active ? "Actif" : "Suspendu"}
                    </span>
                  </Td>
                  <Td>
                    <div className="flex flex-col gap-1.5 sm:flex-row">
                      <button
                        type="button"
                        onClick={() => void toggleActive(u)}
                        disabled={savingId === u.id}
                        className="rounded-lg border border-gray-300 bg-gray-50 px-2 py-1 text-[11px] font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-50"
                      >
                        {u.is_active ? "Suspendre" : "Réactiver"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void deleteUser(u)}
                        disabled={savingId === u.id}
                        className="rounded-lg border border-red-700/30 bg-red-700/10 px-2 py-1 text-[11px] font-medium text-red-700 transition-colors hover:bg-red-700/20 disabled:opacity-50"
                      >
                        Supprimer
                      </button>
                    </div>
                  </Td>
                  <Td className="whitespace-nowrap text-xs">{formatDate(u.created_at)}</Td>
                  <Td className="whitespace-nowrap text-xs">
                    {formatNumber(u.today_tokens_in)} / {formatNumber(u.today_tokens_out)}
                  </Td>
                  <Td className="text-xs">{formatNumber(u.today_requests)}</Td>
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </SectionCard>

      <SectionCard title="Consommation par utilisateur (30 jours)">
        {usageRows.length === 0 ? (
          <EmptyState message="Aucune consommation enregistrée sur les 30 derniers jours." />
        ) : (
          <TableShell>
            <THead>
              <tr>
                <Th>Utilisateur</Th>
                <Th>Tokens en entrée</Th>
                <Th>Tokens en sortie</Th>
                <Th>Requêtes</Th>
              </tr>
            </THead>
            <tbody>
              {usageRows.map((row) => (
                <tr key={row.user_id}>
                  <Td className="text-xs">{row.email || row.user_id}</Td>
                  <Td>{formatNumber(row.tokens_in)}</Td>
                  <Td>{formatNumber(row.tokens_out)}</Td>
                  <Td>{formatNumber(row.requests)}</Td>
                </tr>
              ))}
              <tr className="bg-gray-50 font-medium">
                <Td className="text-xs text-gray-700">Total</Td>
                <Td className="text-gray-700">{formatNumber(totals.tokens_in ?? 0)}</Td>
                <Td className="text-gray-700">{formatNumber(totals.tokens_out ?? 0)}</Td>
                <Td className="text-gray-700">{formatNumber(totals.requests ?? 0)}</Td>
              </tr>
            </tbody>
          </TableShell>
        )}
      </SectionCard>
    </div>
  );
}
