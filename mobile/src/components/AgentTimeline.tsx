import React from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { PIPELINE_NODES } from "../lib/api";
import type { NodeStatus } from "../lib/chat";
import { colors } from "../theme";

interface AgentTimelineProps {
  statuses: Record<string, NodeStatus>;
  active: boolean;
}

function StatusIcon({ status }: { status: NodeStatus }) {
  if (status === "done") {
    return (
      <View style={[styles.icon, styles.iconDone]}>
        <Ionicons name="checkmark" size={13} color={colors.accent} />
      </View>
    );
  }
  if (status === "running") {
    return (
      <View style={[styles.icon, styles.iconDone]}>
        <ActivityIndicator size={13} color={colors.accent} />
      </View>
    );
  }
  return (
    <View style={[styles.icon, styles.iconPending]}>
      <View style={styles.pendingDot} />
    </View>
  );
}

export default function AgentTimeline({ statuses, active }: AgentTimelineProps) {
  const anyActivity = active || PIPELINE_NODES.some((n) => statuses[n.id] !== "pending");

  if (!anyActivity) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyText}>
          La chaîne d'agents s'affichera ici pendant le traitement d'une question.
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.list}>
      {PIPELINE_NODES.map((node) => {
        const status = statuses[node.id] ?? "pending";
        return (
          <View
            key={node.id}
            style={[
              styles.item,
              status === "running" && styles.itemRunning,
              status === "done" && styles.itemDone,
            ]}
          >
            <StatusIcon status={status} />
            <Text
              style={[
                styles.itemText,
                status === "running" && styles.itemTextRunning,
                status === "pending" && styles.itemTextPending,
              ]}
            >
              {node.label}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  list: { gap: 8, padding: 16 },
  empty: {
    margin: 16,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 16,
  },
  emptyText: { fontSize: 12, color: colors.muted, textAlign: "center" },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 9,
  },
  itemRunning: { borderColor: colors.accentLight, backgroundColor: colors.accentLight },
  itemDone: { borderColor: colors.accentLight, backgroundColor: colors.surface },
  itemText: { fontSize: 13, color: colors.inkSoft },
  itemTextRunning: { color: colors.accent, fontWeight: "600" },
  itemTextPending: { color: colors.faint },
  icon: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  iconDone: { borderColor: colors.accentLight, backgroundColor: colors.accentLight },
  iconPending: { borderColor: colors.border, backgroundColor: colors.surface },
  pendingDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.faint },
});
