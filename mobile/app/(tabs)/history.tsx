import React, { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import Markdown from "../../src/components/Markdown";
import {
  deleteBookmark,
  deleteSession,
  listBookmarks,
  listFreshnessEvents,
  listSessions,
  type Bookmark,
  type ChatSessionSummary,
  type FreshnessEvent,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import { chatEngine } from "../../src/lib/chat";
import { relativeDate } from "../../src/lib/format";
import type { ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

type Segment = "sessions" | "bookmarks";

/** Small confidence pill (same thresholds as AnswerView's badge). */
function ConfidencePill({ confidence }: { confidence: number }) {
  const { colors } = useTheme();
  const pct = Math.round(confidence * 100);
  const fg = confidence >= 0.55 ? colors.accent : confidence >= 0.4 ? colors.warnText : colors.danger;
  const bg = confidence >= 0.55 ? colors.accentLight : confidence >= 0.4 ? colors.warnBg : colors.dangerBg;
  const border = confidence >= 0.55 ? colors.accent : confidence >= 0.4 ? colors.warnBorder : colors.dangerBorder;
  return (
    <View
      style={{
        borderWidth: 1,
        borderColor: border,
        backgroundColor: bg,
        borderRadius: 999,
        paddingHorizontal: 8,
        paddingVertical: 2,
      }}
    >
      <Text style={{ fontSize: 10, fontWeight: "600", color: fg }}>Confiance {pct}%</Text>
    </View>
  );
}

export default function HistoryScreen() {
  const { token } = useAuth();
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { sessionId, historyRefresh } = useSyncExternalStore(
    chatEngine.subscribe,
    chatEngine.getSnapshot,
  );
  const [segment, setSegment] = useState<Segment>("sessions");
  const [sessions, setSessions] = useState<ChatSessionSummary[] | null>(null);
  const [bookmarks, setBookmarks] = useState<Bookmark[] | null>(null);
  const [events, setEvents] = useState<FreshnessEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!token) return;
    setLoading(true);
    Promise.allSettled([listSessions(), listBookmarks(), listFreshnessEvents(10)])
      .then(([s, b, e]) => {
        setSessions(s.status === "fulfilled" ? s.value.sessions : null);
        setBookmarks(b.status === "fulfilled" ? b.value : null);
        setEvents(e.status === "fulfilled" ? e.value : []);
      })
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

  function handleDeleteBookmark(bookmark: Bookmark) {
    Alert.alert("Supprimer le marque-page", "Retirer cette réponse de vos marque-pages ?", [
      { text: "Annuler", style: "cancel" },
      {
        text: "Supprimer",
        style: "destructive",
        onPress: () => {
          setDeletingId(bookmark.id);
          deleteBookmark(bookmark.id)
            .then(() => {
              setBookmarks((prev) => prev?.filter((b) => b.id !== bookmark.id) ?? null);
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
    ]);
  }

  function openSession(id: string) {
    void chatEngine.loadSession(id).then(() => router.navigate("/"));
  }

  // "Nouveautés": latest detected changes on the official sources. Hidden
  // entirely when the feed is empty.
  const nouveautesCard =
    events.length === 0 ? null : (
      <View style={styles.newsCard}>
        <View style={styles.newsHeader}>
          <Ionicons name="megaphone-outline" size={15} color={colors.accent} />
          <Text style={styles.newsTitle}>Nouveautés</Text>
        </View>
        {events.map((event, i) => (
          <Pressable
            key={`${event.source_name}-${event.detected_at}-${i}`}
            onPress={() => {
              if (event.url) void Linking.openURL(event.url).catch(() => {});
            }}
            style={[styles.newsRow, i > 0 && styles.newsRowBorder]}
            disabled={!event.url}
          >
            <View style={styles.newsTextBlock}>
              <Text style={styles.newsSource} numberOfLines={1}>
                {event.source_name}
              </Text>
              {event.detail ? (
                <Text style={styles.newsDetail} numberOfLines={2}>
                  {event.detail}
                </Text>
              ) : null}
              <Text style={styles.newsDate}>{relativeDate(event.detected_at)}</Text>
            </View>
            {event.url ? (
              <Ionicons name="open-outline" size={14} color={colors.faint} />
            ) : null}
          </Pressable>
        ))}
      </View>
    );

  const initialLoading = loading && sessions === null && bookmarks === null;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Historique</Text>
      </View>

      {/* Segmented toggle: conversations | saved answers */}
      <View style={styles.segmentRow}>
        {(
          [
            { id: "sessions", label: "Sessions", icon: "time-outline" as const },
            { id: "bookmarks", label: "Marqués", icon: "bookmark-outline" as const },
          ] as { id: Segment; label: string; icon: "time-outline" | "bookmark-outline" }[]
        ).map((s) => (
          <Pressable
            key={s.id}
            onPress={() => setSegment(s.id)}
            style={[styles.segmentButton, segment === s.id && styles.segmentButtonActive]}
          >
            <Ionicons
              name={s.icon}
              size={13}
              color={segment === s.id ? colors.accent : colors.muted}
            />
            <Text style={[styles.segmentText, segment === s.id && styles.segmentTextActive]}>
              {s.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {initialLoading ? (
        <View style={styles.centerFill}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.mutedText}>Chargement…</Text>
        </View>
      ) : segment === "sessions" ? (
        !sessions || (sessions.length === 0 && events.length === 0) ? (
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
            ListHeaderComponent={nouveautesCard}
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
        )
      ) : !bookmarks || bookmarks.length === 0 ? (
        <View style={styles.centerFill}>
          <Ionicons name="bookmark-outline" size={32} color={colors.faint} />
          <Text style={styles.mutedText}>
            Aucun marque-page. Enregistrez une réponse depuis le chat (icône marque-page).
          </Text>
        </View>
      ) : (
        <FlatList
          data={bookmarks}
          keyExtractor={(b) => b.id}
          contentContainerStyle={styles.list}
          refreshing={loading}
          onRefresh={reload}
          renderItem={({ item }) => {
            const expanded = expandedId === item.id;
            const deleting = deletingId === item.id;
            return (
              <View style={styles.bookmarkItem}>
                <Pressable
                  onPress={() => setExpandedId(expanded ? null : item.id)}
                  style={styles.bookmarkMain}
                >
                  {item.query ? (
                    <Text style={styles.bookmarkQuery} numberOfLines={expanded ? undefined : 2}>
                      {item.query}
                    </Text>
                  ) : null}
                  {expanded ? (
                    <Markdown>{item.answer}</Markdown>
                  ) : (
                    <Text style={styles.bookmarkPreview} numberOfLines={3}>
                      {item.answer}
                    </Text>
                  )}
                  <View style={styles.bookmarkMetaRow}>
                    <ConfidencePill confidence={item.confidence} />
                    <Text style={styles.itemMeta}>{relativeDate(item.created_at)}</Text>
                    <Ionicons
                      name={expanded ? "chevron-up" : "chevron-down"}
                      size={12}
                      color={colors.faint}
                    />
                  </View>
                </Pressable>
                <Pressable
                  onPress={() => handleDeleteBookmark(item)}
                  disabled={deleting}
                  style={styles.deleteButton}
                  accessibilityLabel="Supprimer le marque-page"
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
  segmentRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  segmentButton: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 10,
    paddingVertical: 8,
  },
  segmentButtonActive: { borderColor: colors.accent, backgroundColor: colors.accentLight },
  segmentText: { fontSize: 13, fontWeight: "500", color: colors.muted },
  segmentTextActive: { color: colors.accent },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8, padding: 24 },
  mutedText: { fontSize: 13, color: colors.muted, textAlign: "center" },
  list: { padding: 12, paddingTop: 0, gap: 6 },
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
  newsCard: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 12,
    padding: 12,
    marginBottom: 6,
    gap: 4,
  },
  newsHeader: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 2 },
  newsTitle: {
    fontSize: 12,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.accent,
  },
  newsRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 6 },
  newsRowBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  newsTextBlock: { flex: 1 },
  newsSource: { fontSize: 13, fontWeight: "500", color: colors.ink },
  newsDetail: { fontSize: 12, color: colors.inkSoft, marginTop: 1 },
  newsDate: { fontSize: 10, color: colors.faint, marginTop: 2 },
  bookmarkItem: {
    flexDirection: "row",
    alignItems: "flex-start",
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    backgroundColor: colors.surfaceElevated,
    overflow: "hidden",
  },
  bookmarkMain: { flex: 1, padding: 12, gap: 6 },
  bookmarkQuery: { fontSize: 13, fontWeight: "600", color: colors.ink },
  bookmarkPreview: { fontSize: 12, lineHeight: 17, color: colors.inkSoft },
  bookmarkMetaRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 2 },
});
