import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../theme";

interface UpgradePanelProps {
  body: string;
}

/**
 * Shown when a feature is tier-gated (HTTP 403). Display only: the app has
 * no purchase flow — upgrades happen on the web app or via an admin.
 */
export default function UpgradePanel({ body }: UpgradePanelProps) {
  return (
    <View style={styles.container}>
      <View style={styles.iconWrap}>
        <Ionicons name="lock-closed" size={26} color={colors.accent} />
      </View>
      <Text style={styles.title}>Offre supérieure requise</Text>
      <Text style={styles.body}>{body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    margin: 24,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 16,
    padding: 24,
    alignItems: "center",
    gap: 10,
  },
  iconWrap: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.accentLight,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { fontSize: 16, fontWeight: "600", color: colors.ink, textAlign: "center" },
  body: { fontSize: 13, lineHeight: 19, color: colors.muted, textAlign: "center" },
});
