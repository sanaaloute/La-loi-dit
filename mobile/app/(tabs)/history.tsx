import React, { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  deleteSession,
  listSessions,
  type ChatSessionSummary,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { chatEngine } from "../../src/lib/chat";
import { relativeDate } from "../../src/lib/format";
import { colors } from "../../src/theme";

export default function HistoryScreen() {
  const { token } = useAuth();
  const router = useRouter();
  const { sessionId, historyRefresh } = useSyncExternalStore(
    chatEngine.subscribe,
    chatEngine.getSnapshot,
  );
  const [sessions, setSessions] = useState<ChatSessionSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    listSessions()
      .then((res) => setSessions(res.sessions))
      .catch(() => setSessions(null))
      .finally(() => setLoading(false));
  }, [token]);

  // Refresh on focus and whenever the chat engine bumps historyRefresh.
  useFocusEffect(
    useCallback(() => {
      reload();
    }, [reload]),
  );
  useEffect(() => {
    if (historyRefresh > 0) reload();
  }, [historyRefresh, reload]);

  function handleDelete(session: ChatSessionSummary) {
    Alert.alert(
      "Supprimer la conversation",
      `Supprimer la conversation « ${session.title} » ? Cette action est irréversible.`,
      [
        { text: "Annuler", style: "cancel" },
        {
          text: "Supprimer",
          style: "destructive",
          onPress: () => {
            setDeletingId(session.session_id);
            deleteSession(session.session_id)
              .then(() => {
                setSessions((prev) =>
                  prev?.filter((s) => s.session_id !== session.session_id) ?? null,
                );
                // If the active conversation was deleted, reset to a fresh chat.
                if (session.session_id === sessionId) chatEngine.newConversation();
              })
              .catch((err) => {
                Alert.alert(
                  "Échec de la suppression",
                  err instanceof Error ? err.message : "Une erreur est survenue.",
                );
              })
              .finally(() => setDeletingId(null));
          },
        },
      ],
    );
  }

  function openSession(id: string) {
    void chatEngine.loadSession(id).then(() => router.navigate("/"));
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Historique</Text>
      </View>
      {loading && sessions === null ? (
        <View style={styles.centerFill}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.mutedText}>Chargement…</Text>
        </View>
      ) : !sessions || sessions.length === 0 ? (
        <View style={styles.centerFill}>
          <Ionicons name="time-outline" size={32} color={colors.faint} />
          <Text style={styles.mutedText}>Aucune conversation pour le moment.</Text>
        </View>
      ) : (
        <FlatList
          data={sessions}
          keyExtractor={(s) => s.session_id}
          contentContainerStyle={styles.list}
          refreshing={loading}
          onRefresh={reload}
          renderItem={({ item }) => {
            const active = item.session_id === sessionId;
            const deleting = deletingId === item.session_id;
            return (
              <View style={[styles.item, active && styles.itemActive]}>
                <Pressable onPress={() => openSession(item.session_id)} style={styles.itemMain}>
                  <Ionicons
                    name="chatbubble-ellipses-outline"
                    size={16}
                    color={active ? colors.accent : colors.muted}
                  />
                  <View style={styles.itemTextBlock}>
                    <Text style={[styles.itemTitle, active && styles.itemTitleActive]} numberOfLines={1}>
                      {item.title}
                    </Text>
                    <Text style={styles.itemMeta}>
                      {relativeDate(item.updated_at)} — {item.message_count} message
                      {item.message_count > 1 ? "s" : ""}
                    </Text>
                  </View>
                </Pressable>
                <Pressable
                  onPress={() => handleDelete(item)}
                  disabled={deleting}
                  style={styles.deleteButton}
                  accessibilityLabel={`Supprimer la conversation ${item.title}`}
                >
                  {deleting ? (
                    <ActivityIndicator size={14} color={colors.danger} />
                  ) : (
                    <Ionicons name="trash-outline" size={16} color={colors.faint} />
                  )}
                </Pressable>
              </View>
            );
          }}
        />
      )}
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
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  mutedText: { fontSize: 13, color: colors.muted },
  list: { padding: 12, gap: 6 },
  item: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "transparent",
    borderRadius: 12,
    backgroundColor: colors.surfaceElevated,
    overflow: "hidden",
  },
  itemActive: { borderColor: colors.accent, backgroundColor: colors.accentLight },
  itemMain: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  itemTextBlock: { flex: 1 },
  itemTitle: { fontSize: 14, color: colors.inkSoft },
  itemTitleActive: { color: colors.ink, fontWeight: "500" },
  itemMeta: { fontSize: 11, color: colors.muted, marginTop: 2 },
  deleteButton: { padding: 12 },
});
