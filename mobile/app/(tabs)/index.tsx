import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import { SafeAreaView } from "react-native-safe-area-context";
import AnswerView from "../../src/components/AnswerView";
import CitationPanel from "../../src/components/CitationPanel";
import EvidenceViewer from "../../src/components/EvidenceViewer";
import ExportMenu from "../../src/components/ExportMenu";
import Markdown from "../../src/components/Markdown";
import {
  addBookmark,
  createShare,
  deleteBookmark,
  getPreferences,
  type ChatResponse,
  type ExportItem,
} from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import {
  chatEngine,
  countWords,
  currentStepLabel,
  MAX_WORDS,
  type ChatMessage,
} from "../../src/lib/chat";
import { formatMessageTime } from "../../src/lib/format";
import { sampleSuggestions, suggestionsForPersona } from "../../src/lib/persona";
import { serifFont, type ThemeColors } from "../../src/theme";
import { useTheme } from "../../src/theme-context";

const SUGGESTIONS = [
  "Quels sont les droits d'un salarié licencié au Burkina Faso ?",
  "Quelle est la procédure de divorce selon le Code des personnes et de la famille ?",
  "Quelles sont les règles OHADA applicables à la création d'une SARL ?",
  "Que prévoit le Code du travail burkinabè sur la durée du préavis ?",
  "Quels recours contre une décision administrative au Burkina Faso ?",
  "Quelles sont les règles sur l'héritage en droit burkinabè ?",
  "Que dit la loi sur la protection des données personnelles au Burkina Faso ?",
  "Quelles sont les conditions d'acquisition de la nationalité burkinabè ?",
  "Quelles garanties l'OHADA offre-t-elle aux créanciers (sûretés) ?",
  "Quels sont les droits d'un locataire face à une expulsion ?",
  "Comment contester un permis de construire ?",
  "Quelles sont les étapes pour créer une petite entreprise seul ?",
];

/** Public web app origin — shared answer pages live under /partage/<token>. */
const PUBLIC_WEB_ORIGIN = "https://yawoto.neobytech.net";

type PanelTab = "citations" | "preuves";

/** Collapsible chain-of-thought panel (DeepSeek/ChatGPT pattern): open while
 * the reasoning streams in live, auto-collapses once the answer starts, and
 * stays available — collapsed behind a tap — above the final answer. */
function ThinkingBlock({
  text,
  live = false,
  colors,
  styles,
}: {
  text: string;
  live?: boolean;
  colors: ThemeColors;
  styles: ChatStyles;
}) {
  const [open, setOpen] = useState(live);
  useEffect(() => {
    if (!live) setOpen(false);
  }, [live]);
  return (
    <View style={styles.thinkingBox}>
      <Pressable onPress={() => setOpen((v) => !v)} style={styles.thinkingHeader}>
        <Ionicons name="bulb-outline" size={12} color={colors.muted} />
        <Text style={styles.thinkingTitle}>Réflexion</Text>
        <Ionicons name={open ? "chevron-down" : "chevron-forward"} size={12} color={colors.muted} />
      </Pressable>
      {open ? <Text style={styles.thinkingText}>{text}</Text> : null}
    </View>
  );
}
type ChatStyles = ReturnType<typeof makeStyles>;

export default function ChatScreen() {
  const { token } = useAuth();
  const router = useRouter();
  const { colors, isDark, toggleTheme } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const snapshot = useSyncExternalStore(chatEngine.subscribe, chatEngine.getSnapshot);
  const { messages, busy, historyLoading, statuses, selectedId } = snapshot;

  const [input, setInput] = useState("");
  const wordCount = countWords(input);
  const [panelOpen, setPanelOpen] = useState(false);
  const [tab, setTab] = useState<PanelTab>("citations");
  const listRef = useRef<FlatList<ChatMessage>>(null);
  // Auto-scroll follow: the stream is glued to the bottom while the user stays
  // near it. ONLY a user drag may release/re-arm it — programmatic scroll
  // events lag behind a growing content size and read as "not at the bottom",
  // which used to kill the follow mid-stream and hide the status line.
  const followRef = useRef(true);
  const draggingRef = useRef(false);
  const prevBusyRef = useRef(false);

  // Saved answers: message id -> bookmark id (local mirror of /bookmarks).
  const [savedBookmarks, setSavedBookmarks] = useState<Record<string, string>>({});
  const [bookmarkBusyId, setBookmarkBusyId] = useState<string | null>(null);
  const [sharingId, setSharingId] = useState<string | null>(null);

  // Suggestion prompts follow the onboarding persona when one is chosen;
  // three are sampled from the pool and re-sampled each time the chat
  // returns to the empty state (new conversation), so they rotate.
  const [suggestionPool, setSuggestionPool] = useState<string[]>(SUGGESTIONS);
  const [suggestions, setSuggestions] = useState<string[]>(() => sampleSuggestions(SUGGESTIONS));

  // Restore the persisted conversation once the token is available.
  useEffect(() => {
    if (token) void chatEngine.restoreSession();
  }, [token]);

  // Read the persona preference on mount (drives the suggestion prompts).
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getPreferences()
      .then((prefs) => {
        if (cancelled) return;
        const persona = typeof prefs.persona === "string" ? prefs.persona : null;
        setSuggestionPool(suggestionsForPersona(persona) ?? SUGGESTIONS);
      })
      .catch(() => {
        // Offline or unreadable: keep the default suggestions.
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Rotate the three suggestion cards each time the empty chat shows
  // (first load, then every new conversation).
  const chatEmpty = !historyLoading && messages.length === 0;
  useEffect(() => {
    if (chatEmpty) setSuggestions(sampleSuggestions(suggestionPool));
  }, [chatEmpty, suggestionPool]);

  // Elapsed-time indicator while a run is in flight (helps spot a stuck node).
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [busy]);

  // Follow the streaming text while pinned to the bottom; when the run
  // finishes, bring the START of the final answer into view instead of leaving
  // the user at the tail of the stream.
  useEffect(() => {
    const justFinished = prevBusyRef.current && !busy;
    prevBusyRef.current = busy;
    if (messages.length === 0) return;
    if (busy) {
      if (followRef.current) listRef.current?.scrollToEnd({ animated: false });
      return;
    }
    if (justFinished) {
      let idx = -1;
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === "assistant") {
          idx = i;
          break;
        }
      }
      if (idx >= 0) {
        const t = setTimeout(
          () => listRef.current?.scrollToIndex({ index: idx, viewPosition: 0, animated: true }),
          150,
        );
        return () => clearTimeout(t);
      }
      return;
    }
    const t = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
    return () => clearTimeout(t);
  }, [messages, busy]);


  // -------------------------------------------------------------------------
  // Derived view data
  // -------------------------------------------------------------------------
  const selectedIndex = messages.findIndex((m) => m.id === selectedId);
  const selectedMessage = selectedIndex >= 0 ? messages[selectedIndex] : undefined;
  const selectedAnswer = selectedMessage?.response?.answer;

  let selectedQuery = "";
  if (selectedMessage?.role === "assistant") {
    for (let i = selectedIndex - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        selectedQuery = messages[i].text;
        break;
      }
    }
  }

  // All question/answer exchanges of the conversation, for full-chat exports,
  // plus the user question each assistant message replies to.
  const { conversationItems, queryByAssistantId } = useMemo(() => {
    const items: ExportItem[] = [];
    const queries = new Map<string, string>();
    let pendingQuery: string | null = null;
    for (const m of messages) {
      if (m.role === "user") {
        pendingQuery = m.text;
      } else if (m.response && pendingQuery !== null) {
        items.push({ query: pendingQuery, answer: m.response.answer });
        queries.set(m.id, pendingQuery);
        pendingQuery = null;
      }
    }
    return { conversationItems: items, queryByAssistantId: queries };
  }, [messages]);

  const sendDisabled = input.trim().length === 0 || wordCount > MAX_WORDS || busy;

  // -------------------------------------------------------------------------
  // Answer actions: copy, bookmark toggle + public share link
  // -------------------------------------------------------------------------

  // Copy the answer text to the clipboard (the main thing users want when
  // tapping an answer; details stay behind the "Détails" button).
  async function copyAnswer(text: string) {
    await Clipboard.setStringAsync(text);
  }

  async function toggleBookmark(msg: ChatMessage, response: ChatResponse) {
    if (bookmarkBusyId) return;
    const existingId = savedBookmarks[msg.id];
    setBookmarkBusyId(msg.id);
    try {
      if (existingId) {
        await deleteBookmark(existingId);
        setSavedBookmarks((prev) => {
          const next = { ...prev };
          delete next[msg.id];
          return next;
        });
      } else {
        const bookmark = await addBookmark({
          query: queryByAssistantId.get(msg.id) ?? "",
          answer: response.answer.answer,
          confidence: response.answer.confidence,
          session_id: response.session_id,
        });
        setSavedBookmarks((prev) => ({ ...prev, [msg.id]: bookmark.id }));
      }
    } catch (err) {
      Alert.alert(
        "Marque-page",
        err instanceof Error ? err.message : "Une erreur est survenue.",
      );
    } finally {
      setBookmarkBusyId(null);
    }
  }

  async function handleShare(msg: ChatMessage, response: ChatResponse) {
    if (sharingId) return;
    setSharingId(msg.id);
    try {
      const link = await createShare({
        query: queryByAssistantId.get(msg.id) ?? "",
        answer: response.answer.answer,
        citations: response.answer.citations,
        confidence: response.answer.confidence,
      });
      await Share.share({ message: `${PUBLIC_WEB_ORIGIN}${link.url_path}` });
    } catch (err) {
      Alert.alert(
        "Partage",
        err instanceof Error ? err.message : "Une erreur est survenue.",
      );
    } finally {
      setSharingId(null);
    }
  }

  function renderMessage(msg: ChatMessage) {
    if (msg.role === "user") {
      return (
        <View style={styles.userRow}>
          <View style={styles.userBubble}>
            <Text style={styles.userText} selectable>{msg.text}</Text>
            {msg.ts ? <Text style={styles.userTime}>{formatMessageTime(msg.ts)}</Text> : null}
          </View>
        </View>
      );
    }

    const response: ChatResponse | undefined = msg.response;
    return (
      <View style={styles.assistantRow}>
        <View style={styles.botAvatar}>
          <Ionicons name="scale" size={15} color="#fff" />
        </View>
        <Pressable
          onPress={() => chatEngine.selectMessage(msg.id)}
          style={[
            styles.assistantBubble,
            msg.error && styles.errorBubble,
            msg.quota && styles.quotaBubble,
            selectedId === msg.id && !msg.error && !msg.quota && styles.selectedBubble,
          ]}
        >
          {msg.error ? (
            <Text style={styles.errorText}>{msg.text}</Text>
          ) : msg.quota ? (
            <View style={{ gap: 4 }}>
              <View style={styles.quotaHeader}>
                <Ionicons name="alert-circle" size={15} color={colors.warnText} />
                <Text style={styles.quotaTitle}>Quota journalier atteint</Text>
              </View>
              <Text style={styles.quotaText}>{msg.text}</Text>
            </View>
          ) : response ? (
            <View>
              {msg.thinking ? (
                <ThinkingBlock text={msg.thinking} colors={colors} styles={styles} />
              ) : null}
              <AnswerView answer={response.answer} />
            </View>
          ) : msg.streaming ? (
            <View>
              {msg.thinking ? (
                <ThinkingBlock
                  text={msg.thinking}
                  live={msg.text.length === 0}
                  colors={colors}
                  styles={styles}
                />
              ) : null}
              <Text style={styles.streamingText} selectable>{msg.text}</Text>
            </View>
          ) : (
            <Markdown>{msg.text}</Markdown>
          )}
          {msg.streaming && busy ? (
            // While streaming, the workflow status takes the timestamp's seat:
            // pinned to the bubble's bottom edge, it stays on screen as the
            // text grows — and the time is meaningless until the answer lands.
            <View style={[styles.busyRow, styles.streamStatus]}>
              <ActivityIndicator size={12} color={colors.accent} />
              <View style={styles.busyTextBlock}>
                <Text style={styles.busyLabel} numberOfLines={1}>
                  {currentStepLabel(statuses)}
                </Text>
                <Text style={styles.busySub}>
                  Traitement en cours par les agents… ({elapsed}s)
                </Text>
              </View>
            </View>
          ) : msg.ts ? (
            <Text style={styles.assistantTime}>{formatMessageTime(msg.ts)}</Text>
          ) : null}
          {response ? (
            <View style={styles.actionsRow}>
              <Pressable
                onPress={() => void copyAnswer(response.answer.answer)}
                style={styles.actionButton}
                accessibilityLabel="Copier la réponse"
              >
                <Ionicons name="copy-outline" size={14} color={colors.muted} />
                <Text style={styles.actionText}>Copier</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  chatEngine.selectMessage(msg.id);
                  setTab("citations");
                  setPanelOpen(true);
                }}
                style={styles.actionButton}
              >
                <Ionicons name="layers-outline" size={14} color={colors.muted} />
                <Text style={styles.actionText}>Détails</Text>
              </Pressable>
              <ExportMenu
                response={response}
                query={queryByAssistantId.get(msg.id) ?? ""}
                conversation={conversationItems}
              />
              <Pressable
                disabled={bookmarkBusyId === msg.id}
                onPress={() => void toggleBookmark(msg, response)}
                style={styles.actionButton}
                accessibilityLabel={
                  savedBookmarks[msg.id] ? "Retirer le marque-page" : "Enregistrer la réponse"
                }
              >
                {bookmarkBusyId === msg.id ? (
                  <ActivityIndicator size={14} color={colors.muted} />
                ) : (
                  <Ionicons
                    name={savedBookmarks[msg.id] ? "bookmark" : "bookmark-outline"}
                    size={14}
                    color={savedBookmarks[msg.id] ? colors.accent : colors.muted}
                  />
                )}
              </Pressable>
              <Pressable
                disabled={sharingId === msg.id}
                onPress={() => void handleShare(msg, response)}
                style={styles.actionButton}
                accessibilityLabel="Partager la réponse"
              >
                {sharingId === msg.id ? (
                  <ActivityIndicator size={14} color={colors.muted} />
                ) : (
                  <Ionicons name="share-social-outline" size={14} color={colors.muted} />
                )}
              </Pressable>
              {response.trace_id ? (
                <>
                  <Pressable
                    disabled={msg.feedbackPending}
                    onPress={() => void chatEngine.sendFeedback(msg.id, response, "thumbs-up")}
                    style={styles.actionButton}
                    accessibilityLabel="Utile"
                  >
                    <Ionicons
                      name={msg.feedback === "thumbs-up" ? "thumbs-up" : "thumbs-up-outline"}
                      size={14}
                      color={msg.feedback === "thumbs-up" ? colors.accent : colors.muted}
                    />
                  </Pressable>
                  <Pressable
                    disabled={msg.feedbackPending}
                    onPress={() => void chatEngine.sendFeedback(msg.id, response, "thumbs-down")}
                    style={styles.actionButton}
                    accessibilityLabel="Pas utile"
                  >
                    <Ionicons
                      name={msg.feedback === "thumbs-down" ? "thumbs-down" : "thumbs-down-outline"}
                      size={14}
                      color={msg.feedback === "thumbs-down" ? colors.danger : colors.muted}
                    />
                  </Pressable>
                </>
              ) : null}
            </View>
          ) : null}
        </Pressable>
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.brandRow}>
          <View style={styles.brandTile}>
            <Ionicons name="scale" size={15} color={colors.gold} />
          </View>
          <Text style={styles.headerTitle}>Yawoto</Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable
            onPress={() => router.push("/history")}
            style={styles.headerButton}
            accessibilityLabel="Ouvrir l'historique"
          >
            <Ionicons name="time-outline" size={18} color={colors.inkSoft} />
          </Pressable>
          <Pressable
            onPress={() => chatEngine.newConversation()}
            style={styles.newChatButton}
            accessibilityRole="button"
            accessibilityLabel="Démarrer une nouvelle conversation"
          >
            <Ionicons name="chatbubble-outline" size={14} color="#fff" />
            <Text style={styles.newChatText}>Nouvelle</Text>
          </Pressable>
          <Pressable
            onPress={toggleTheme}
            style={styles.headerButton}
            accessibilityLabel={isDark ? "Passer au thème clair" : "Passer au thème sombre"}
          >
            <Ionicons name={isDark ? "sunny" : "moon"} size={18} color={colors.inkSoft} />
          </Pressable>
        </View>
      </View>

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        {historyLoading ? (
          <View style={styles.centerFill}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.loadingText}>Chargement de la conversation…</Text>
          </View>
        ) : messages.length === 0 ? (
          // ScrollView, not a plain View: when the keyboard shrinks the area,
          // a centered fixed View overflows upward and paints over the header.
          // A ScrollView clips its content to below the header and stays
          // scrollable, so every suggestion remains reachable while typing.
          <ScrollView
            style={styles.flex}
            contentContainerStyle={styles.emptyState}
            keyboardShouldPersistTaps="handled"
          >
            <View style={styles.emptyLogo}>
              <Ionicons name="scale" size={34} color={colors.gold} />
            </View>
            <Text style={styles.emptyBrand}>Yawoto</Text>
            <Text style={styles.emptyTagline}>Le droit, cité à la source.</Text>
            <Text style={styles.emptyBody}>
              Posez vos questions en français. Yawoto répond à partir des textes officiels du
              Burkina Faso et de l'OHADA, avec citations vérifiables et traçabilité complète.
            </Text>
            <View style={styles.suggestions}>
              {suggestions.map((s) => (
                <Pressable key={s} onPress={() => setInput(s)} style={styles.suggestion}>
                  <Text style={styles.suggestionText}>{s}</Text>
                </Pressable>
              ))}
            </View>
            <Pressable
              onPress={() => router.push("/draft")}
              style={styles.draftCard}
              accessibilityRole="button"
              accessibilityLabel="Ouvrir la rédaction juridique"
            >
              <Ionicons name="create-outline" size={20} color={colors.gold} />
              <View style={styles.draftTextBlock}>
                <Text style={styles.draftTitle}>Rédaction juridique</Text>
                <Text style={styles.draftBody}>
                  Contrats et actes de procédure générés à partir de modèles.
                </Text>
              </View>
              <Ionicons name="chevron-forward" size={14} color={colors.faint} />
            </Pressable>
          </ScrollView>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(m) => m.id}
            renderItem={({ item }) => renderMessage(item)}
            contentContainerStyle={styles.messageList}
            onScroll={(e) => {
              // Ignore programmatic scrolls (see followRef note): only the
              // user's own drag decides whether the stream keeps being glued.
              if (!draggingRef.current) return;
              const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
              followRef.current =
                contentOffset.y + layoutMeasurement.height >= contentSize.height - 100;
            }}
            onScrollBeginDrag={() => {
              draggingRef.current = true;
            }}
            onScrollEndDrag={() => {
              draggingRef.current = false;
            }}
            scrollEventThrottle={100}
            onScrollToIndexFailed={({ index }) => {
              // Layout not ready yet: retry once after the next frames.
              setTimeout(
                () => listRef.current?.scrollToIndex({ index, viewPosition: 0, animated: true }),
                300,
              );
            }}
            onContentSizeChange={() => {
              // Scroll AFTER layout: fired from the messages-update effect,
              // scrollToEnd computes against the pre-layout content size and
              // lands a frame short — the status footer ends up clipped under
              // the composer. Here the size is exact, so the footer's bottom
              // stays glued to the screen until the stream finishes.
              if (busy && followRef.current) {
                listRef.current?.scrollToEnd({ animated: false });
              }
            }}
            ListFooterComponent={
              // Covers only the gap before the first stream frame (and the
              // reconnect-recovery path, which has no provisional bubble):
              // once the streaming message exists, the status lives inside it.
              busy && !messages[messages.length - 1]?.streaming ? (
                <View style={styles.busyRow}>
                  <ActivityIndicator size={14} color={colors.accent} />
                  <View style={styles.busyTextBlock}>
                    <Text style={styles.busyLabel} numberOfLines={1}>
                      {currentStepLabel(statuses)}
                    </Text>
                    <Text style={styles.busySub}>
                      Traitement en cours par les agents… ({elapsed}s)
                    </Text>
                  </View>
                </View>
              ) : null
            }
          />
        )}

        {/* Composer */}
        <View style={styles.composer}>
          <View style={styles.composerRow}>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder="Posez votre question."
              placeholderTextColor={colors.faint}
              multiline
              editable={!busy}
              style={styles.input}
            />
            {busy ? (
              <Pressable
                onPress={() => chatEngine.stop()}
                style={[styles.roundButton, styles.roundButtonDanger]}
                accessibilityLabel="Arrêter la génération"
              >
                <Ionicons name="stop" size={16} color="#fff" />
              </Pressable>
            ) : (
              <Pressable
                onPress={() => {
                  const q = input;
                  setInput("");
                  followRef.current = true; // a new question re-arms the follow-the-stream scroll
                  void chatEngine.send(q);
                }}
                disabled={sendDisabled}
                style={[
                  styles.roundButton,
                  sendDisabled ? styles.roundButtonDisabled : styles.roundButtonAccent,
                ]}
                accessibilityLabel={wordCount > MAX_WORDS ? `Limite de ${MAX_WORDS} mots dépassée` : "Envoyer"}
                >
                  <Ionicons name="arrow-up" size={17} color={sendDisabled ? colors.faint : "#fff"} />
                </Pressable>
            )}
          </View>
          {wordCount > 0 && (
            <Text style={[styles.wordCount, wordCount > MAX_WORDS && styles.wordCountOver]}>
              {wordCount}/{MAX_WORDS} mots
              {wordCount > MAX_WORDS ? " — raccourcissez votre question" : ""}
            </Text>
          )}
        </View>
      </KeyboardAvoidingView>

      {/* Details sheet: agents timeline / citations / evidence */}
      <Modal
        visible={panelOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setPanelOpen(false)}
      >
        <View style={styles.modalContainer}>
          <Pressable style={styles.modalBackdrop} onPress={() => setPanelOpen(false)} />
          <View style={styles.sheet}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>Détails de la réponse</Text>
            <Pressable onPress={() => setPanelOpen(false)} style={styles.headerButton}>
              <Ionicons name="close" size={20} color={colors.inkSoft} />
            </Pressable>
          </View>
          <View style={styles.sheetTabs}>
            {(
              [
                { id: "citations", label: "Citations" },
                { id: "preuves", label: "Preuves" },
              ] as { id: PanelTab; label: string }[]
            ).map((t) => (
              <Pressable
                key={t.id}
                onPress={() => setTab(t.id)}
                style={[styles.sheetTab, tab === t.id && styles.sheetTabActive]}
              >
                <Text style={[styles.sheetTabText, tab === t.id && styles.sheetTabTextActive]}>
                  {t.label}
                </Text>
              </Pressable>
            ))}
          </View>
          <FlatList
            data={[null]}
            renderItem={() => (
              <View>
                {tab === "citations" && <CitationPanel citations={selectedAnswer?.citations ?? []} />}
                {tab === "preuves" && <EvidenceViewer evidence={selectedAnswer?.evidence ?? []} />}
                {selectedMessage?.response && conversationItems.length > 0 && (
                  <View style={styles.sheetExport}>
                    <ExportMenu
                      response={selectedMessage.response}
                      query={selectedQuery}
                      conversation={conversationItems}
                    />
                  </View>
                )}
              </View>
            )}
            keyExtractor={() => "panel"}
          />
          </View>
        </View>
      </Modal>

    </SafeAreaView>
  );
}

const makeStyles = (colors: ThemeColors) => StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.surface },
  flex: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    // Opaque + painted above the body: scrolling content must slide UNDER
    // the appbar, never show through it (theme colors carry no alpha).
    backgroundColor: colors.surfaceElevated,
    zIndex: 10,
  },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  brandTile: {
    width: 28,
    height: 28,
    borderRadius: 8,
    backgroundColor: colors.goldLight,
    alignItems: "center",
    justifyContent: "center",
  },
  headerTitle: { fontFamily: serifFont, fontSize: 20, fontWeight: "700", color: colors.ink },
  headerActions: { flexDirection: "row", alignItems: "center", gap: 4 },
  headerButton: { padding: 8, borderRadius: 8 },
  newChatButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: colors.accent,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 7,
    marginHorizontal: 4,
  },
  newChatText: { fontSize: 13, fontWeight: "600", color: "#fff" },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  loadingText: { fontSize: 13, color: colors.muted },
  emptyState: { flexGrow: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  emptyLogo: {
    width: 72,
    height: 72,
    borderRadius: 20,
    backgroundColor: colors.goldLight,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 16,
  },
  emptyBrand: { fontFamily: serifFont, fontSize: 30, fontWeight: "700", color: colors.ink },
  emptyTagline: {
    fontFamily: serifFont,
    fontSize: 17,
    fontWeight: "600",
    color: colors.gold,
    textAlign: "center",
    marginTop: 4,
  },
  emptyBody: {
    fontSize: 14,
    lineHeight: 21,
    color: colors.muted,
    textAlign: "center",
    marginTop: 8,
    marginBottom: 20,
  },
  suggestions: { gap: 10, alignSelf: "stretch" },
  suggestion: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 12,
    padding: 14,
  },
  suggestionText: { fontSize: 13, color: colors.inkSoft },
  draftCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    alignSelf: "stretch",
    marginTop: 16,
    borderWidth: 1,
    borderColor: colors.gold,
    backgroundColor: colors.goldLight,
    borderRadius: 12,
    padding: 14,
  },
  draftTextBlock: { flex: 1 },
  draftTitle: { fontFamily: serifFont, fontSize: 15, fontWeight: "700", color: colors.ink },
  draftBody: { fontSize: 12, lineHeight: 17, color: colors.muted, marginTop: 2 },
  messageList: { padding: 16, gap: 14, paddingBottom: 8 },
  userRow: { flexDirection: "row", justifyContent: "flex-end" },
  userBubble: {
    maxWidth: "85%",
    borderWidth: 1,
    borderColor: colors.accentLight,
    backgroundColor: colors.accentLight,
    borderRadius: 16,
    borderBottomRightRadius: 4,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  userText: { fontSize: 14, color: colors.ink },
  userTime: { fontSize: 10, color: colors.faint, textAlign: "right", marginTop: 4 },
  assistantRow: { flexDirection: "row", gap: 8, alignItems: "flex-start" },
  botAvatar: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 4,
  },
  assistantBubble: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceElevated,
    borderRadius: 16,
    borderBottomLeftRadius: 4,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  selectedBubble: { borderColor: colors.accent },
  errorBubble: { borderColor: colors.dangerBorder, backgroundColor: colors.dangerBg },
  errorText: { fontSize: 14, color: colors.danger },
  quotaBubble: { borderColor: colors.warnBorder, backgroundColor: colors.warnBg },
  quotaHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  quotaTitle: { fontSize: 13, fontWeight: "600", color: colors.warnText },
  quotaText: { fontSize: 13, color: colors.warnText },
  quotaHint: { fontSize: 11, color: colors.warnText, opacity: 0.8 },
  streamingText: { fontSize: 14, color: colors.inkSoft, lineHeight: 21 },
  thinkingBox: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.faint,
    borderRadius: 8,
    marginBottom: 6,
    overflow: "hidden",
  },
  thinkingHeader: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 5 },
  thinkingTitle: { fontSize: 11, fontWeight: "600", color: colors.muted },
  thinkingText: { fontSize: 11, fontStyle: "italic", color: colors.muted, lineHeight: 16, paddingHorizontal: 8, paddingBottom: 6 },
  assistantTime: { fontSize: 10, color: colors.faint, marginTop: 6 },
  actionsRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 4,
    marginTop: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 6,
  },
  actionButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  actionText: { fontSize: 12, color: colors.muted },
  busyRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 6 },
  streamStatus: { marginTop: 4 },
  busyTextBlock: { flex: 1 },
  busyLabel: { fontSize: 13, color: colors.muted },
  busySub: { fontSize: 11, color: colors.faint, marginTop: 1 },
  composer: {
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surfaceElevated,
  },
  composerRow: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  input: {
    flex: 1,
    minHeight: 42,
    maxHeight: 140,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 21,
    backgroundColor: colors.surface,
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 10,
    fontSize: 14,
    color: colors.ink,
  },
  roundButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
  },
  roundButtonMuted: { backgroundColor: colors.border },
  roundButtonDanger: { backgroundColor: colors.danger },
  roundButtonAccent: { backgroundColor: colors.accent },
  roundButtonDisabled: { backgroundColor: colors.border },
  wordCount: { fontSize: 11, color: colors.faint, textAlign: "right", marginTop: 4 },
  wordCountOver: { color: colors.danger, fontWeight: "600" },
  modalContainer: { flex: 1, justifyContent: "flex-end" },
  modalBackdrop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0,0,0,0.5)",
  },
  sheet: {
    height: "72%",
    backgroundColor: colors.surfaceElevated,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    overflow: "hidden",
  },
  sheetHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  sheetTitle: { fontSize: 15, fontWeight: "600", color: colors.ink },
  sheetTabs: {
    flexDirection: "row",
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
  },
  sheetTab: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: 2,
    borderBottomColor: "transparent",
  },
  sheetTabActive: { borderBottomColor: colors.accent },
  sheetTabText: { fontSize: 13, fontWeight: "500", color: colors.muted },
  sheetTabTextActive: { color: colors.accent },
  sheetExport: { padding: 16, borderTopWidth: 1, borderTopColor: colors.border, alignItems: "flex-end" },
});
