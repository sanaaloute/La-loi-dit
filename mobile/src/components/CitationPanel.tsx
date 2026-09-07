import React, { useMemo } from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { Citation } from "../lib/api";
import type { ThemeColors } from "../theme";
import { useTheme } from "../theme-context";

interface CitationPanelProps {
  citations: Citation[];
}

export default function CitationPanel({ citations }: CitationPanelProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={styles.headerDot} />
        <Text style={styles.headerText}>Citations ({citations.length})</Text>
      </View>
      {citations.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyText}>Aucune citation pour cette réponse.</Text>
        </View>
      ) : (
        citations.map((citation, i) => (
          <View key={`${citation.label}-${i}`} style={styles.card}>
            <View style={styles.cardHeader}>
              <View style={styles.cardTitleBlock}>
                <Text style={styles.cardTitle}>{citation.label}</Text>
                {citation.article ? (
                  <Text style={styles.cardSubtitle}>Article {citation.article}</Text>
                ) : null}
              </View>
              {citation.verified ? (
                <View style={[styles.badge, styles.badgeVerified]}>
                  <Ionicons name="checkmark-circle" size={11} color={colors.accent} />
                  <Text style={[styles.badgeText, { color: colors.accent }]}>Vérifiée</Text>
                </View>
              ) : (
                <View style={[styles.badge, styles.badgeUnverified]}>
                  <Ionicons name="alert-circle" size={11} color={colors.warnText} />
                  <Text style={[styles.badgeText, { color: colors.warnText }]}>Non vérifiée</Text>
                </View>
              )}
            </View>
            <Text style={styles.documentName} numberOfLines={1}>
              {citation.document_name}
            </Text>
            {citation.law_number ? (
              <Text style={styles.lawNumber}>Loi n°{citation.law_number}</Text>
            ) : null}
            {citation.url ? (
              <Pressable
                onPress={() => void Linking.openURL(citation.url as string).catch(() => {})}
                style={styles.urlRow}
              >
                <Ionicons name="open-outline" size={12} color={colors.accent} />
                <Text style={styles.url} numberOfLines={1}>
                  {citation.url}
                </Text>
              </Pressable>
            ) : null}
          </View>
        ))
      )}
    </View>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  container: { padding: 16, gap: 12 },
  header: { flexDirection: "row", alignItems: "center", gap: 8 },
  headerDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent },
  headerText: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.muted,
  },
  empty: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
  },
  emptyText: { fontSize: 12, color: colors.muted, textAlign: "center" },
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 12,
    padding: 12,
    gap: 4,
  },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", gap: 8 },
  cardTitleBlock: { flex: 1 },
  cardTitle: { fontSize: 14, fontWeight: "500", color: colors.ink },
  cardSubtitle: { fontSize: 12, color: colors.muted, marginTop: 2 },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 2,
    alignSelf: "flex-start",
  },
  badgeVerified: { borderColor: colors.accentLight, backgroundColor: colors.accentLight },
  badgeUnverified: { borderColor: colors.warnBorder, backgroundColor: colors.warnBg },
  badgeText: { fontSize: 10, fontWeight: "500" },
  documentName: { fontSize: 11, color: colors.muted },
  lawNumber: { fontSize: 11, color: colors.muted },
  urlRow: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 4 },
  url: { flex: 1, fontSize: 12, color: colors.accent },
});
