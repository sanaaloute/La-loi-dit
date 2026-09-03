import React, { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { FinalAnswer } from "../lib/api";
import type { ThemeColors } from "../theme";
import { useTheme } from "../theme-context";
import Markdown from "./Markdown";

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const pct = Math.round(confidence * 100);
  const palette =
    confidence >= 0.55
      ? { fg: colors.accent, bg: colors.accentLight, border: colors.accent, icon: "checkmark-circle" as const }
      : confidence >= 0.4
        ? { fg: colors.warnText, bg: colors.warnBg, border: colors.warnBorder, icon: "alert-circle" as const }
        : { fg: colors.danger, bg: colors.dangerBg, border: colors.dangerBorder, icon: "shield-half" as const };
  return (
    <View style={[styles.badge, { backgroundColor: palette.bg, borderColor: palette.border }]}>
      <Ionicons name={palette.icon} size={12} color={palette.fg} />
      <Text style={[styles.badgeText, { color: palette.fg }]}>Confiance {pct}%</Text>
    </View>
  );
}

interface AnswerViewProps {
  answer: FinalAnswer;
}

export default function AnswerView({ answer }: AnswerViewProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  // Direct-route answers (casual conversation, no legal retrieval): render
  // the reply plainly — a confidence score is meaningless there.
  const isDirect = answer.metadata?.route === "direct";

  if (answer.refused) {
    return (
      <View style={styles.refused}>
        <View style={styles.row}>
          <Ionicons name="shield-half" size={16} color={colors.danger} />
          <Text style={styles.refusedTitle}>Demande refusée</Text>
        </View>
        {answer.refusal_reason ? <Text style={styles.refusedBody}>{answer.refusal_reason}</Text> : null}
        {answer.answer ? (
          <View style={styles.refusedMarkdown}>
            <Markdown>{answer.answer}</Markdown>
          </View>
        ) : null}
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {!isDirect && (
        <View style={[styles.row, styles.badges]}>
          <ConfidenceBadge confidence={answer.confidence} />
          {answer.requires_human_review && (
            <View style={[styles.badge, { backgroundColor: colors.dangerBg, borderColor: colors.dangerBorder }]}>
              <Ionicons name="shield-half" size={12} color={colors.danger} />
              <Text style={[styles.badgeText, { color: colors.danger }]}>Révision humaine requise</Text>
            </View>
          )}
        </View>
      )}

      {answer.requires_human_review && (
        <View style={styles.reviewNotice}>
          <Text style={styles.reviewNoticeText}>
            Cette réponse doit être validée par un juriste avant toute utilisation.
          </Text>
        </View>
      )}

      <Markdown>{answer.answer}</Markdown>

      {answer.warnings.length > 0 && (
        <View style={styles.warnings}>
          <View style={styles.row}>
            <Ionicons name="alert-circle" size={14} color={colors.warnText} />
            <Text style={styles.warningsTitle}>Avertissements</Text>
          </View>
          {answer.warnings.map((w, i) => (
            <View key={i} style={styles.warningItem}>
              <Text style={styles.warningBullet}>•</Text>
              <Text style={styles.warningText}>{w}</Text>
            </View>
          ))}
        </View>
      )}

      {answer.conflicts.length > 0 && (
        <View style={styles.conflictList}>
          {answer.conflicts.map((c, i) => (
            <View
              key={i}
              style={[styles.conflict, c.resolved ? styles.conflictResolved : styles.conflictOpen]}
            >
              <Text style={[styles.conflictTitle, { color: c.resolved ? colors.inkSoft : colors.warnText }]}>
                {c.resolved ? "Conflit résolu" : "Conflit non résolu"} : {c.topic}
              </Text>
              <Text style={[styles.conflictReason, { color: c.resolved ? colors.inkSoft : colors.warnText }]}>
                {c.reason}
              </Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  container: { gap: 10 },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  badges: { flexWrap: "wrap", gap: 8 },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 3,
  },
  badgeText: { fontSize: 12, fontWeight: "600" },
  reviewNotice: {
    borderWidth: 1,
    borderColor: colors.dangerBorder,
    backgroundColor: colors.dangerBg,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  reviewNoticeText: { fontSize: 12, color: colors.danger },
  refused: {
    borderWidth: 1,
    borderColor: colors.dangerBorder,
    backgroundColor: colors.dangerBg,
    borderRadius: 12,
    padding: 14,
    gap: 6,
  },
  refusedTitle: { fontSize: 14, fontWeight: "600", color: colors.danger },
  refusedBody: { fontSize: 13, color: colors.danger },
  refusedMarkdown: { marginTop: 4 },
  warnings: {
    borderWidth: 1,
    borderColor: colors.warnBorder,
    backgroundColor: colors.warnBg,
    borderRadius: 12,
    padding: 12,
    gap: 4,
  },
  warningsTitle: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.warnText,
  },
  warningItem: { flexDirection: "row", gap: 6, paddingLeft: 4 },
  warningBullet: { fontSize: 12, color: colors.warnText },
  warningText: { flex: 1, fontSize: 12, color: colors.warnText },
  conflictList: { gap: 8 },
  conflict: { borderWidth: 1, borderRadius: 12, padding: 12 },
  conflictResolved: { borderColor: colors.border, backgroundColor: colors.surface },
  conflictOpen: { borderColor: colors.warnBorder, backgroundColor: colors.warnBg },
  conflictTitle: { fontSize: 12, fontWeight: "600" },
  conflictReason: { fontSize: 12, marginTop: 4 },
});
