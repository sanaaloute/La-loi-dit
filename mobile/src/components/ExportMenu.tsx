import React, { useState } from "react";
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { ChatResponse, ExportItem } from "../lib/api";
import { shareAnswerExport, type MenuFormat } from "../lib/export";
import { colors } from "../theme";

const FORMAT_LABELS: { id: MenuFormat; label: string }[] = [
  { id: "pdf", label: "PDF" },
  { id: "word", label: "Word (.docx)" },
  { id: "csv", label: "CSV" },
  { id: "md", label: "Markdown (.md)" },
];

interface ExportMenuProps {
  response: ChatResponse;
  /** The user question this response answers. */
  query: string;
  /** All question/answer exchanges of the conversation (conversation scope). */
  conversation?: ExportItem[];
}

/** Export trigger for a chat answer; formats offered via the native sheet. */
export default function ExportMenu({ response, query, conversation = [] }: ExportMenuProps) {
  const [loading, setLoading] = useState<MenuFormat | null>(null);

  async function handleExport(format: MenuFormat, items?: ExportItem[]) {
    setLoading(format);
    try {
      await shareAnswerExport(format, response, query, items);
    } catch (err) {
      Alert.alert("Échec de l'export", err instanceof Error ? err.message : "Une erreur est survenue.");
    } finally {
      setLoading(null);
    }
  }

  function openMenu() {
    const canConversation = conversation.length > 1;
    const buttons: Parameters<typeof Alert.alert>[2] = [];
    for (const f of FORMAT_LABELS) {
      buttons.push({ text: f.label, onPress: () => void handleExport(f.id) });
    }
    if (canConversation) {
      buttons.unshift({
        text: `Conversation complète (${conversation.length})`,
        onPress: () => void handleExport("pdf", conversation),
      });
    }
    buttons.push({ text: "Annuler", style: "cancel" });
    Alert.alert("Exporter la réponse", undefined, buttons);
  }

  return (
    <Pressable
      onPress={openMenu}
      disabled={loading !== null}
      style={styles.button}
      accessibilityLabel="Exporter la réponse"
    >
      {loading !== null ? (
        <ActivityIndicator size={14} color={colors.muted} />
      ) : (
        <Ionicons name="download-outline" size={16} color={colors.muted} />
      )}
      <Text style={styles.buttonText}>Exporter</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  buttonText: { fontSize: 12, color: colors.muted },
});
