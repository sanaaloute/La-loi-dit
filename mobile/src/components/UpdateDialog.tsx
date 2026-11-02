import React, { useMemo } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { LatestRelease } from "../lib/release";
import type { ThemeColors } from "../theme";
import { useTheme } from "../theme-context";

interface UpdateDialogProps {
  visible: boolean;
  currentVersion: string;
  release: LatestRelease;
  onLater: () => void;
  onUpdate: () => void;
}

/** "New version on the stores" card: notes + versions, dismiss or update. */
export function UpdateDialog({
  visible,
  currentVersion,
  release,
  onLater,
  onUpdate,
}: UpdateDialogProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onLater}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Ionicons name="arrow-up-circle-outline" size={32} color={colors.accent} />
          <Text style={styles.title}>Mise à jour disponible</Text>
          <Text style={styles.text}>Une nouvelle version de Yawoto est disponible.</Text>
          <View style={styles.versions}>
            <Text style={styles.versionText}>Votre version : {currentVersion || "—"}</Text>
            <Text style={styles.versionText}>
              Nouvelle version : {release.latest_version}
            </Text>
          </View>
          {release.release_notes ? (
            <View style={styles.notesBox}>
              <Text style={styles.notesTitle}>Nouveautés</Text>
              <Text style={styles.notesText}>{release.release_notes}</Text>
            </View>
          ) : null}
          <Pressable style={styles.updateButton} onPress={onUpdate} accessibilityRole="button">
            <Ionicons name="download-outline" size={16} color="#fff" />
            <Text style={styles.updateButtonText}>Mettre à jour</Text>
          </Pressable>
          <Pressable style={styles.laterButton} onPress={onLater} accessibilityRole="button">
            <Text style={styles.laterButtonText}>Plus tard</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const makeStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    backdrop: {
      flex: 1,
      backgroundColor: "rgba(0,0,0,0.45)",
      alignItems: "center",
      justifyContent: "center",
      padding: 32,
    },
    card: {
      alignSelf: "stretch",
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.surfaceElevated,
      borderRadius: 16,
      padding: 20,
      alignItems: "center",
      gap: 10,
    },
    title: { fontSize: 16, fontWeight: "700", color: colors.ink, textAlign: "center" },
    text: { fontSize: 13, color: colors.inkSoft, textAlign: "center" },
    versions: { alignSelf: "stretch", gap: 2 },
    versionText: { fontSize: 12, color: colors.muted },
    notesBox: {
      alignSelf: "stretch",
      borderWidth: 1,
      borderColor: colors.border,
      backgroundColor: colors.surface,
      borderRadius: 10,
      padding: 12,
      gap: 4,
    },
    notesTitle: {
      fontSize: 11,
      fontWeight: "600",
      textTransform: "uppercase",
      letterSpacing: 0.5,
      color: colors.muted,
    },
    notesText: { fontSize: 13, color: colors.ink },
    updateButton: {
      alignSelf: "stretch",
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "center",
      gap: 8,
      backgroundColor: colors.accent,
      borderRadius: 12,
      paddingVertical: 12,
    },
    updateButtonText: { fontSize: 14, fontWeight: "600", color: "#fff" },
    laterButton: { paddingVertical: 8, paddingHorizontal: 12 },
    laterButtonText: { fontSize: 14, fontWeight: "500", color: colors.inkSoft },
  });
