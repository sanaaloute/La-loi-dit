// Persona onboarding: shown once after login (modal). The choice is stored
// server-side in the user preferences (`persona`) and drives the chat
// suggestion prompts; the SecureStore flag keeps the modal from reappearing.
import React, { useMemo, useState } from "react";
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { putPreferences } from "../src/lib/api";
import { markOnboarded, PERSONA_OPTIONS, type PersonaId } from "../src/lib/persona";
import type { ThemeColors } from "../src/theme";
import { useTheme } from "../src/theme-context";

export default function OnboardingScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [busy, setBusy] = useState<PersonaId | "skip" | null>(null);

  async function choose(persona: PersonaId | null) {
    if (busy) return;
    setBusy(persona ?? "skip");
    try {
      if (persona) await putPreferences({ persona });
      await markOnboarded();
      router.back();
    } catch (err) {
      Alert.alert(
        "Préférences",
        err instanceof Error ? err.message : "Une erreur est survenue.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <View style={styles.logo}>
          <Ionicons name="scale" size={30} color="#fff" />
        </View>
        <Text style={styles.title}>Bienvenue sur Yawoto</Text>
        <Text style={styles.subtitle}>
          Pour adapter les réponses et les suggestions, dites-nous qui vous êtes.
        </Text>
      </View>
      <View style={styles.cards}>
        {PERSONA_OPTIONS.map((option) => (
          <Pressable
            key={option.id}
            onPress={() => void choose(option.id)}
            disabled={busy !== null}
            style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}
          >
            <View style={styles.cardIcon}>
              <Ionicons name={option.icon} size={22} color={colors.accent} />
            </View>
            <View style={styles.cardText}>
              <Text style={styles.cardLabel}>{option.label}</Text>
              <Text style={styles.cardDescription}>{option.description}</Text>
            </View>
            {busy === option.id ? (
              <ActivityIndicator size={16} color={colors.accent} />
            ) : (
              <Ionicons name="chevron-forward" size={16} color={colors.faint} />
            )}
          </Pressable>
        ))}
        <Pressable
          onPress={() => void choose(null)}
          disabled={busy !== null}
          style={({ pressed }) => [styles.card, styles.skipCard, pressed && styles.cardPressed]}
        >
          <View style={styles.cardIcon}>
            <Ionicons name="arrow-forward-outline" size={22} color={colors.muted} />
          </View>
          <View style={styles.cardText}>
            <Text style={styles.skipLabel}>Passer</Text>
            <Text style={styles.cardDescription}>
              Continuer sans personnalisation (modifiable dans Compte).
            </Text>
          </View>
          {busy === "skip" ? (
            <ActivityIndicator size={16} color={colors.muted} />
          ) : (
            <Ionicons name="chevron-forward" size={16} color={colors.faint} />
          )}
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  header: { alignItems: "center", paddingHorizontal: 24, paddingTop: 32, paddingBottom: 8 },
  logo: {
    width: 64,
    height: 64,
    borderRadius: 18,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 14,
  },
  title: { fontSize: 21, fontWeight: "700", color: colors.ink, textAlign: "center" },
  subtitle: {
    fontSize: 14,
    lineHeight: 20,
    color: colors.muted,
    textAlign: "center",
    marginTop: 8,
  },
  cards: { padding: 20, gap: 10 },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 14,
    padding: 14,
  },
  cardPressed: { borderColor: colors.accent, backgroundColor: colors.accentLight },
  cardIcon: {
    width: 42,
    height: 42,
    borderRadius: 12,
    backgroundColor: colors.accentLight,
    alignItems: "center",
    justifyContent: "center",
  },
  cardText: { flex: 1 },
  cardLabel: { fontSize: 15, fontWeight: "600", color: colors.ink },
  skipCard: { borderStyle: "dashed" },
  skipLabel: { fontSize: 15, fontWeight: "600", color: colors.inkSoft },
  cardDescription: { fontSize: 12, color: colors.muted, marginTop: 2 },
});
