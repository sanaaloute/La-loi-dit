import React, { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  getSubscription,
  listModels,
  usageMe,
  type ModelInfo,
  type ModelList,
  type SubscriptionInfo,
  type Tier,
  type UsageResponse,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { longDate, shortDay } from "../../src/lib/format";
import { getModel, setModel } from "../../src/lib/storage";
import { colors } from "../../src/theme";

const TIER_LABELS: Record<Tier, string> = {
  gratuit: "Gratuit",
  pro: "Pro",
  cabinet: "Cabinet",
};

const STATUS_LABELS: Record<SubscriptionInfo["status"], string> = {
  active: "Actif",
  past_due: "Paiement en retard",
  canceled: "Résilié",
  none: "Aucun abonnement",
};

const TIER_BADGES: Partial<Record<Tier, string>> = {
  pro: "Pro",
  cabinet: "Cabinet",
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} k`;
  return String(n);
}

export default function AccountScreen() {
  const { profile, token, refreshProfile, signOut, deleteAccount } = useAuth();
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [subscription, setSubscription] = useState<SubscriptionInfo | null>(null);
  const [modelList, setModelList] = useState<ModelList | null>(null);
  const [loading, setLoading] = useState(true);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string | null>(getModel());
  const [busyAction, setBusyAction] = useState(false);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    void refreshProfile();
    Promise.allSettled([usageMe(), getSubscription(), listModels()]).then((results) => {
      const [u, s, m] = results;
      setUsage(u.status === "fulfilled" ? u.value : null);
      setSubscription(s.status === "fulfilled" ? s.value : null);
      setModelList(m.status === "fulfilled" ? m.value : null);
      setLoading(false);
    });
  }, [token, refreshProfile]);

  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );

  // 30-day chart data: oldest first, zero-filled for display continuity.
  const chartDays = useMemo(() => {
    if (!usage) return [];
    const byDay = new Map(usage.history.map((d) => [d.day, d]));
    const days: { day: string; tokens: number }[] = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
        d.getDate(),
      ).padStart(2, "0")}`;
      const row = byDay.get(key);
      days.push({ day: key, tokens: (row?.tokens_in ?? 0) + (row?.tokens_out ?? 0) });
    }
    return days;
  }, [usage]);

  const maxDayTokens = Math.max(1, ...chartDays.map((d) => d.tokens));
  const todayTotal = (usage?.today.tokens_in ?? 0) + (usage?.today.tokens_out ?? 0);
  const usageRatio = usage && usage.daily_budget > 0 ? Math.min(1, todayTotal / usage.daily_budget) : 0;

  const effectiveModel = selectedModel ?? modelList?.default_model ?? null;
  const effectiveModelLabel =
    modelList?.models.find((m) => m.id === effectiveModel)?.label ?? "Modèle par défaut";

  function pickModel(model: ModelInfo | null) {
    if (model && !model.allowed) return;
    const id = model?.id ?? null;
    setModel(id);
    setSelectedModel(id);
    setModelPickerOpen(false);
  }

  function handleLogout() {
    Alert.alert("Se déconnecter", "Vous pourrez vous reconnecter à tout moment.", [
      { text: "Annuler", style: "cancel" },
      {
        text: "Se déconnecter",
        onPress: () => {
          setBusyAction(true);
          void signOut().finally(() => setBusyAction(false));
        },
      },
    ]);
  }

  function handleDeleteAccount() {
    Alert.alert(
      "Supprimer le compte",
      "Votre compte, vos conversations et vos données seront définitivement supprimés. Cette action est irréversible.",
      [
        { text: "Annuler", style: "cancel" },
        {
          text: "Continuer",
          style: "destructive",
          onPress: () => {
            Alert.alert(
              "Confirmer la suppression",
              "Dernière étape : confirmez-vous la suppression définitive de votre compte ?",
              [
                { text: "Annuler", style: "cancel" },
                {
                  text: "Supprimer définitivement",
                  style: "destructive",
                  onPress: () => {
                    setBusyAction(true);
                    deleteAccount()
                      .catch((err) => {
                        Alert.alert(
                          "Suppression impossible",
                          err instanceof Error ? err.message : "Une erreur est survenue.",
                        );
                      })
                      .finally(() => setBusyAction(false));
                  },
                },
              ],
            );
          },
        },
      ],
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Compte</Text>
      </View>
      <ScrollView contentContainerStyle={styles.scroll}>
        {/* Profil */}
        <View style={styles.card}>
          <View style={styles.profileRow}>
            <View style={styles.avatar}>
              <Ionicons name="person" size={22} color="#fff" />
            </View>
            <View style={styles.profileText}>
              <Text style={styles.profileName}>{profile?.name || "Sans nom"}</Text>
              <Text style={styles.profileDetail} numberOfLines={1}>
                {profile?.email || profile?.phone || "—"}
              </Text>
              <Text style={styles.profileDetail} numberOfLines={1}>
                {profile?.workspace_name ?? ""}
              </Text>
            </View>
            {profile && (
              <View style={styles.tierBadge}>
                <Text style={styles.tierBadgeText}>{TIER_LABELS[profile.tier]}</Text>
              </View>
            )}
          </View>
        </View>

        {/* Usage quotidien */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Utilisation du jour</Text>
          {loading && !usage ? (
            <ActivityIndicator color={colors.accent} />
          ) : usage ? (
            <>
              <View style={styles.usageBarTrack}>
                <View
                  style={[
                    styles.usageBarFill,
                    { width: `${Math.max(2, usageRatio * 100)}%` },
                    usageRatio >= 1 && { backgroundColor: colors.danger },
                  ]}
                />
              </View>
              <Text style={styles.usageText}>
                {formatTokens(todayTotal)} / {formatTokens(usage.daily_budget)} jetons —{" "}
                {usage.today.requests} requête{usage.today.requests > 1 ? "s" : ""}
              </Text>
              <Text style={styles.usageRemaining}>
                Restant : {formatTokens(usage.remaining_tokens)} jetons
              </Text>
              {chartDays.length > 0 && (
                <View style={styles.chart}>
                  <View style={styles.chartBars}>
                    {chartDays.map((d) => (
                      <View
                        key={d.day}
                        style={[
                          styles.chartBar,
                          { height: Math.max(2, (d.tokens / maxDayTokens) * 64) },
                          d.tokens === 0 && styles.chartBarEmpty,
                        ]}
                      />
                    ))}
                  </View>
                  <View style={styles.chartLabels}>
                    <Text style={styles.chartLabel}>{shortDay(chartDays[0].day)}</Text>
                    <Text style={styles.chartLabel}>30 derniers jours</Text>
                    <Text style={styles.chartLabel}>{shortDay(chartDays[chartDays.length - 1].day)}</Text>
                  </View>
                </View>
              )}
            </>
          ) : (
            <Text style={styles.mutedText}>Utilisation indisponible.</Text>
          )}
        </View>

        {/* Abonnement (lecture seule) */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Abonnement</Text>
          {subscription ? (
            <View style={styles.subscriptionBlock}>
              <View style={styles.subscriptionRow}>
                <Text style={styles.subscriptionLabel}>Offre</Text>
                <Text style={styles.subscriptionValue}>
                  {TIER_LABELS[subscription.tier as Tier] ?? subscription.tier}
                </Text>
              </View>
              <View style={styles.subscriptionRow}>
                <Text style={styles.subscriptionLabel}>Statut</Text>
                <Text style={styles.subscriptionValue}>{STATUS_LABELS[subscription.status]}</Text>
              </View>
              {subscription.current_period_end && (
                <View style={styles.subscriptionRow}>
                  <Text style={styles.subscriptionLabel}>
                    {subscription.cancel_at_period_end ? "Fin le" : "Renouvellement le"}
                  </Text>
                  <Text style={styles.subscriptionValue}>
                    {longDate(subscription.current_period_end)}
                  </Text>
                </View>
              )}
              <Text style={styles.subscriptionHint}>
                La souscription et le changement d'offre se gèrent sur l'application web.
              </Text>
            </View>
          ) : (
            <Text style={styles.mutedText}>
              {loading ? "Chargement…" : "Abonnement indisponible."}
            </Text>
          )}
        </View>

        {/* Modèle */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Modèle d'IA</Text>
          <Pressable style={styles.modelButton} onPress={() => setModelPickerOpen(true)}>
            <Ionicons name="hardware-chip-outline" size={16} color={colors.accent} />
            <Text style={styles.modelButtonText} numberOfLines={1}>
              {effectiveModelLabel}
            </Text>
            <Ionicons name="chevron-down" size={14} color={colors.muted} />
          </Pressable>
        </View>

        {/* Actions */}
        <Pressable
          style={styles.logoutButton}
          onPress={handleLogout}
          disabled={busyAction}
        >
          <Ionicons name="log-out-outline" size={16} color={colors.inkSoft} />
          <Text style={styles.logoutText}>Se déconnecter</Text>
        </Pressable>
        <Pressable
          style={styles.deleteButton}
          onPress={handleDeleteAccount}
          disabled={busyAction}
        >
          <Ionicons name="trash-outline" size={16} color={colors.danger} />
          <Text style={styles.deleteText}>Supprimer mon compte</Text>
        </Pressable>
        <Text style={styles.versionText}>Yawoto pour Burkina Faso — version 1.0.0</Text>
      </ScrollView>

      {/* Sélecteur de modèle */}
      <Modal
        visible={modelPickerOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setModelPickerOpen(false)}
      >
        <View style={styles.modalContainer}>
          <Pressable style={styles.modalBackdrop} onPress={() => setModelPickerOpen(false)} />
          <View style={styles.modelSheet}>
            <Text style={styles.modelSheetTitle}>Choisir le modèle</Text>
            <ScrollView>
              <Pressable style={styles.modelRow} onPress={() => pickModel(null)}>
                <View style={styles.modelRowText}>
                  <Text style={styles.modelLabel}>Par défaut</Text>
                  <Text style={styles.modelProvider}>
                    {modelList?.models.find((m) => m.id === modelList.default_model)?.label ??
                      "Choisi par le serveur"}
                  </Text>
                </View>
                {selectedModel === null && (
                  <Ionicons name="checkmark-circle" size={18} color={colors.accent} />
                )}
              </Pressable>
              {(modelList?.models ?? []).map((model) => {
                const badge = model.allowed ? undefined : TIER_BADGES[model.tier_required];
                const selected = selectedModel === model.id;
                return (
                  <Pressable
                    key={model.id}
                    style={[styles.modelRow, !model.allowed && { opacity: 0.55 }]}
                    onPress={() => pickModel(model)}
                    disabled={!model.allowed}
                  >
                    <View style={styles.modelRowText}>
                      <Text style={styles.modelLabel}>{model.label}</Text>
                      <Text style={styles.modelProvider}>{model.provider}</Text>
                    </View>
                    {selected && <Ionicons name="checkmark-circle" size={18} color={colors.accent} />}
                    {badge && (
                      <View style={styles.modelBadge}>
                        <Text style={styles.modelBadgeText}>{badge}</Text>
                      </View>
                    )}
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surfaceElevated,
  },
  headerTitle: { fontSize: 17, fontWeight: "700", color: colors.ink },
  scroll: { padding: 16, gap: 14, paddingBottom: 32 },
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 14,
    padding: 16,
    gap: 10,
  },
  cardTitle: {
    fontSize: 12,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.muted,
  },
  profileRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  profileText: { flex: 1 },
  profileName: { fontSize: 16, fontWeight: "600", color: colors.ink },
  profileDetail: { fontSize: 12, color: colors.muted, marginTop: 1 },
  tierBadge: {
    borderWidth: 1,
    borderColor: colors.accent,
    backgroundColor: colors.accentLight,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  tierBadgeText: { fontSize: 11, fontWeight: "600", color: colors.accent },
  mutedText: { fontSize: 13, color: colors.muted },
  usageBarTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.border,
    overflow: "hidden",
  },
  usageBarFill: { height: 8, borderRadius: 4, backgroundColor: colors.accent },
  usageText: { fontSize: 13, color: colors.ink, fontWeight: "500" },
  usageRemaining: { fontSize: 12, color: colors.muted },
  chart: { marginTop: 6 },
  chartBars: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 2,
    height: 66,
  },
  chartBar: {
    flex: 1,
    borderRadius: 2,
    backgroundColor: colors.accent,
    minWidth: 2,
  },
  chartBarEmpty: { backgroundColor: colors.border },
  chartLabels: { flexDirection: "row", justifyContent: "space-between", marginTop: 4 },
  chartLabel: { fontSize: 10, color: colors.faint },
  subscriptionBlock: { gap: 6 },
  subscriptionRow: { flexDirection: "row", justifyContent: "space-between" },
  subscriptionLabel: { fontSize: 13, color: colors.muted },
  subscriptionValue: { fontSize: 13, color: colors.ink, fontWeight: "500" },
  subscriptionHint: { fontSize: 11, color: colors.faint, marginTop: 6 },
  modelButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  modelButtonText: { flex: 1, fontSize: 13, fontWeight: "500", color: colors.ink },
  logoutButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 12,
    paddingVertical: 12,
  },
  logoutText: { fontSize: 14, fontWeight: "500", color: colors.inkSoft },
  deleteButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderWidth: 1,
    borderColor: colors.dangerBorder,
    backgroundColor: colors.dangerBg,
    borderRadius: 12,
    paddingVertical: 12,
  },
  deleteText: { fontSize: 14, fontWeight: "500", color: colors.danger },
  versionText: { fontSize: 11, color: colors.faint, textAlign: "center", marginTop: 4 },
  modalContainer: { flex: 1, justifyContent: "flex-end" },
  modalBackdrop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0,0,0,0.5)",
  },
  modelSheet: {
    maxHeight: "60%",
    backgroundColor: colors.surfaceElevated,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 16,
  },
  modelSheetTitle: { fontSize: 15, fontWeight: "600", color: colors.ink, marginBottom: 10 },
  modelRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  modelRowText: { flex: 1 },
  modelLabel: { fontSize: 14, fontWeight: "500", color: colors.ink },
  modelProvider: { fontSize: 11, color: colors.muted, marginTop: 1 },
  modelBadge: {
    borderWidth: 1,
    borderColor: colors.accent,
    backgroundColor: colors.accentLight,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  modelBadgeText: { fontSize: 10, fontWeight: "500", color: colors.accent },
});
