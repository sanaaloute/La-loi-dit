import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  apiUrl,
  changePassword,
  getPreferences,
  putPreferences,
  updateMe,
  uploadAvatar,
  usageMe,
  type UsageResponse,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { shortDay } from "../../src/lib/format";
import { personaLabel, PERSONA_OPTIONS, type PersonaId } from "../../src/lib/persona";
import { getToken } from "../../src/lib/storage";
import type { ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";
import { ErrorText, PasswordField, PrimaryButton, TextField } from "../../src/components/ui";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} k`;
  return String(n);
}

/** The backend returns avatar_url as a site-relative "/api/v1/..." path. */
function absoluteAvatarUrl(avatarUrl: string): string {
  return apiUrl(avatarUrl.replace(/^\/api\/v1/, ""));
}

export default function AccountScreen() {
  const { profile, token, refreshProfile, signOut, deleteAccount } = useAuth();
  const { colors, isDark, toggleTheme } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);

  // Edit-profile modal
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPersona, setEditPersona] = useState<PersonaId | null>(null);
  const [editPassword, setEditPassword] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);

  // Password modal
  const [pwOpen, setPwOpen] = useState(false);
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");
  const [pwError, setPwError] = useState<string | null>(null);
  const [pwBusy, setPwBusy] = useState(false);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    void refreshProfile();
    Promise.allSettled([usageMe()]).then(([u]) => {
      setUsage(u.status === "fulfilled" ? u.value : null);
      setLoading(false);
    });
  }, [token, refreshProfile]);

  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );

  // Current persona: shown on the card and preselected in the edit modal.
  useEffect(() => {
    if (!token) return;
    getPreferences()
      .then((res) => {
        const p = res.persona;
        setEditPersona(p === "etudiant" || p === "juriste" || p === "citoyen" ? p : null);
      })
      .catch(() => {});
  }, [token]);

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

  const avatarUri = profile?.avatar_url ? absoluteAvatarUrl(profile.avatar_url) : null;
  const authHeader = { Authorization: `Bearer ${getToken() ?? ""}` };

  function openEdit() {
    setEditName(profile?.name ?? "");
    setEditPhone(profile?.phone ?? "");
    setEditEmail(profile?.email ?? "");
    setEditPassword("");
    setEditError(null);
    setEditOpen(true);
  }

  async function pickAvatar() {
    setAvatarBusy(true);
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ["images"],
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.8,
      });
      if (result.canceled || !result.assets?.[0]?.uri) return;
      await uploadAvatar(result.assets[0].uri);
      await refreshProfile();
    } catch (err) {
      Alert.alert(
        "Photo de profil",
        err instanceof Error ? err.message : "L'envoi de la photo a échoué.",
      );
    } finally {
      setAvatarBusy(false);
    }
  }

  async function saveProfile() {
    if (!editPassword) {
      setEditError("Saisissez votre mot de passe actuel pour confirmer.");
      return;
    }
    setEditBusy(true);
    setEditError(null);
    try {
      await updateMe({
        current_password: editPassword,
        name: editName.trim(),
        phone: editPhone.trim(),
        email: editEmail.trim(),
      });
      await putPreferences(editPersona ? { persona: editPersona } : { persona: null });
      await refreshProfile();
      setEditOpen(false);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "La mise à jour a échoué.");
    } finally {
      setEditBusy(false);
    }
  }

  async function savePassword() {
    if (pwNew.length < 6) {
      setPwError("Le nouveau mot de passe doit contenir au moins 6 caractères.");
      return;
    }
    if (pwNew !== pwConfirm) {
      setPwError("La confirmation ne correspond pas au nouveau mot de passe.");
      return;
    }
    setPwBusy(true);
    setPwError(null);
    try {
      await changePassword(pwCurrent, pwNew);
      setPwOpen(false);
      setPwCurrent("");
      setPwNew("");
      setPwConfirm("");
      Alert.alert("Mot de passe", "Votre mot de passe a été modifié.");
    } catch (err) {
      setPwError(err instanceof Error ? err.message : "Le changement a échoué.");
    } finally {
      setPwBusy(false);
    }
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
            <Pressable
              onPress={() => void pickAvatar()}
              disabled={avatarBusy}
              style={styles.avatarWrap}
              accessibilityLabel="Changer la photo de profil"
            >
              {avatarUri ? (
                <Image source={{ uri: avatarUri, headers: authHeader }} style={styles.avatarImg} />
              ) : (
                <View style={styles.avatar}>
                  <Ionicons name="person" size={22} color="#fff" />
                </View>
              )}
              <View style={styles.avatarBadge}>
                {avatarBusy ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <Ionicons name="camera" size={11} color="#fff" />
                )}
              </View>
            </Pressable>
            <View style={styles.profileText}>
              <Text style={styles.profileName}>{profile?.name || "Sans nom"}</Text>
              <Text style={styles.profileDetail} numberOfLines={1}>
                {profile?.email || profile?.phone || "—"}
              </Text>
              <Text style={styles.profileDetail} numberOfLines={1}>
                {personaLabel(editPersona)}
              </Text>
            </View>
            <Pressable
              onPress={openEdit}
              style={styles.editButton}
              accessibilityLabel="Modifier mes informations"
            >
              <Ionicons name="pencil-outline" size={16} color={colors.accent} />
            </Pressable>
          </View>
          <Pressable style={styles.passwordButton} onPress={() => {
            setPwCurrent("");
            setPwNew("");
            setPwConfirm("");
            setPwError(null);
            setPwOpen(true);
          }}>
            <Ionicons name="key-outline" size={16} color={colors.accent} />
            <Text style={styles.passwordButtonText}>Changer le mot de passe</Text>
          </Pressable>
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
                {formatTokens(todayTotal)} / {usage.daily_token_budget > 0 ? formatTokens(usage.daily_token_budget) : "∞"} jetons —{" "}
                {usage.today.requests} / {usage.daily_request_budget > 0 ? usage.daily_request_budget : "∞"} requêtes
              </Text>
              <Text style={styles.usageRemaining}>
                Restant : {formatTokens(usage.remaining_tokens)} jetons — {usage.remaining_requests} requêtes
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

        {/* Thème */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Thème</Text>
          <Pressable style={styles.themeButton} onPress={toggleTheme}>
            <Ionicons name={isDark ? "moon" : "sunny"} size={16} color={colors.accent} />
            <Text style={styles.themeButtonText}>{isDark ? "Sombre" : "Clair"}</Text>
            <Ionicons name="swap-horizontal-outline" size={14} color={colors.muted} />
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
        <Text style={styles.versionText}>Yawoto pour Burkina Faso — version 1.0.1</Text>
      </ScrollView>

      {/* Modification du profil */}
      <Modal visible={editOpen} transparent animationType="slide" onRequestClose={() => setEditOpen(false)}>
        <View style={styles.modalBackdrop}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <View style={styles.sheet}>
              <Text style={styles.sheetTitle}>Mes informations</Text>
              <ScrollView contentContainerStyle={styles.sheetFields} keyboardShouldPersistTaps="handled">
                <TextField label="Nom" value={editName} onChangeText={setEditName} />
                <TextField
                  label="Téléphone"
                  value={editPhone}
                  onChangeText={setEditPhone}
                  keyboardType="phone-pad"
                  optional
                />
                <TextField
                  label="Email"
                  value={editEmail}
                  onChangeText={setEditEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  optional
                />
                <Text style={styles.fieldLabel}>Profil utilisateur</Text>
                <View style={styles.personaRow}>
                  {PERSONA_OPTIONS.map((option) => (
                    <Pressable
                      key={option.id}
                      onPress={() => setEditPersona(option.id)}
                      style={[styles.personaChip, editPersona === option.id && styles.personaChipActive]}
                    >
                      <Ionicons
                        name={option.icon}
                        size={13}
                        color={editPersona === option.id ? "#fff" : colors.inkSoft}
                      />
                      <Text
                        style={[
                          styles.personaChipText,
                          editPersona === option.id && styles.personaChipTextActive,
                        ]}
                      >
                        {option.label}
                      </Text>
                    </Pressable>
                  ))}
                </View>
                <PasswordField
                  label="Mot de passe actuel (confirmation)"
                  value={editPassword}
                  onChangeText={setEditPassword}
                />
                <ErrorText message={editError} />
              </ScrollView>
              <View style={styles.sheetActions}>
                <Pressable onPress={() => setEditOpen(false)} style={styles.sheetCancel}>
                  <Text style={styles.sheetCancelText}>Annuler</Text>
                </Pressable>
                <View style={styles.sheetPrimary}>
                  <PrimaryButton title="Enregistrer" onPress={() => void saveProfile()} busy={editBusy} />
                </View>
              </View>
            </View>
          </KeyboardAvoidingView>
        </View>
      </Modal>

      {/* Changement de mot de passe */}
      <Modal visible={pwOpen} transparent animationType="slide" onRequestClose={() => setPwOpen(false)}>
        <View style={styles.modalBackdrop}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <View style={styles.sheet}>
              <Text style={styles.sheetTitle}>Changer le mot de passe</Text>
              <View style={styles.sheetFields}>
                <PasswordField label="Mot de passe actuel" value={pwCurrent} onChangeText={setPwCurrent} />
                <PasswordField label="Nouveau mot de passe" value={pwNew} onChangeText={setPwNew} />
                <PasswordField label="Confirmer le nouveau mot de passe" value={pwConfirm} onChangeText={setPwConfirm} />
                <ErrorText message={pwError} />
              </View>
              <View style={styles.sheetActions}>
                <Pressable onPress={() => setPwOpen(false)} style={styles.sheetCancel}>
                  <Text style={styles.sheetCancelText}>Annuler</Text>
                </Pressable>
                <View style={styles.sheetPrimary}>
                  <PrimaryButton title="Valider" onPress={() => void savePassword()} busy={pwBusy} />
                </View>
              </View>
            </View>
          </KeyboardAvoidingView>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
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
  avatarWrap: { position: "relative" },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarImg: { width: 48, height: 48, borderRadius: 24 },
  avatarBadge: {
    position: "absolute",
    right: -2,
    bottom: -2,
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 2,
    borderColor: colors.surfaceElevated,
  },
  profileText: { flex: 1 },
  profileName: { fontSize: 16, fontWeight: "600", color: colors.ink },
  profileDetail: { fontSize: 12, color: colors.muted, marginTop: 1 },
  editButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  passwordButton: {
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
  passwordButtonText: { fontSize: 13, fontWeight: "500", color: colors.ink },
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
  themeButton: {
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
  themeButtonText: { flex: 1, fontSize: 13, fontWeight: "500", color: colors.ink },
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
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.4)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: colors.surfaceElevated,
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    padding: 20,
    gap: 14,
    maxHeight: "92%",
  },
  sheetTitle: { fontSize: 16, fontWeight: "700", color: colors.ink },
  sheetFields: { gap: 12 },
  fieldLabel: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  personaRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  personaChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  personaChipActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  personaChipText: { fontSize: 12, fontWeight: "500", color: colors.inkSoft },
  personaChipTextActive: { color: "#fff" },
  sheetActions: { flexDirection: "row", alignItems: "center", gap: 12 },
  sheetCancel: { paddingVertical: 12, paddingHorizontal: 8 },
  sheetCancelText: { fontSize: 14, fontWeight: "500", color: colors.inkSoft },
  sheetPrimary: { flex: 1 },
});
