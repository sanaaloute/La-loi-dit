import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  AudioModule,
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  type AudioRecorder,
} from "expo-audio";
import AgentTimeline from "../../src/components/AgentTimeline";
import AnswerView from "../../src/components/AnswerView";
import CitationPanel from "../../src/components/CitationPanel";
import EvidenceViewer from "../../src/components/EvidenceViewer";
import ExportMenu from "../../src/components/ExportMenu";
import Markdown from "../../src/components/Markdown";
import { transcribeAudio, type ChatResponse, type ExportItem } from "../../src/lib/api";
import { useAuth } from "../../src/lib/auth";
import {
  chatEngine,
  countWords,
  currentStepLabel,
  MAX_WORDS,
  type ChatMessage,
} from "../../src/lib/chat";
import { formatMessageTime } from "../../src/lib/format";
import { colors } from "../../src/theme";

/** Voice notes are capped: short clips transcribe faster and cost less. */
const MAX_REC_SECONDS = 30;

const SUGGESTIONS = [
  "Quels sont les droits d'un salarié licencié au Burkina Faso ?",
  "Quelle est la procédure de divorce selon le Code des personnes et de la famille ?",
  "Quelles sont les règles OHADA applicables à la création d'une SARL ?",
];

const AUDIO_MIME: Record<string, string> = {
  m4a: "audio/m4a",
  mp4: "audio/mp4",
  aac: "audio/aac",
  caf: "audio/x-caf",
  webm: "audio/webm",
  ogg: "audio/ogg",
  "3gp": "audio/3gpp",
};

type PanelTab = "agents" | "citations" | "preuves";

export default function ChatScreen() {
  const { token } = useAuth();
  const snapshot = useSyncExternalStore(chatEngine.subscribe, chatEngine.getSnapshot);
  const { messages, busy, historyLoading, statuses, selectedId } = snapshot;

  const [input, setInput] = useState("");
  const wordCount = countWords(input);
  const [panelOpen, setPanelOpen] = useState(false);
  const [tab, setTab] = useState<PanelTab>("agents");
  const listRef = useRef<FlatList<ChatMessage>>(null);

  // Restore the persisted conversation once the token is available.
  useEffect(() => {
    if (token) void chatEngine.restoreSession();
  }, [token]);

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

  useEffect(() => {
    if (messages.length > 0) {
      const t = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
      return () => clearTimeout(t);
    }
  }, [messages, busy]);

  // -------------------------------------------------------------------------
  // Voice input: expo-audio recorder -> POST /chat/transcribe -> text into
  // the composer (never auto-sent; the user reviews it first).
  //
  // The recorder is created ON DEMAND and released right after use. We
  // deliberately avoid useAudioRecorder/useAudioRecorderState: those hooks
  // keep a native shared object alive for the whole mount and call native
  // code during render, which crashes hard ("shared object already
  // released") when React remounts the screen with preserved state
  // (expo-router's Suspense does exactly that).
  // -------------------------------------------------------------------------
  const recorderRef = useRef<AudioRecorder | null>(null);
  const recStartTsRef = useRef(0);
  const [recording, setRecording] = useState(false);
  const [recElapsed, setRecElapsed] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const [recError, setRecError] = useState<string | null>(null);
  const discardRef = useRef(false);
  const mountedRef = useRef(true);

  const stopRecording = useCallback(async () => {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    setRecording(false);
    if (!recorder) return;
    let uri: string | null = null;
    try {
      await recorder.stop();
      uri = recorder.uri;
    } catch {
      // Not recording anymore.
    }
    try {
      recorder.release();
    } catch {
      // Already released.
    }
    void setAudioModeAsync({ allowsRecording: false }).catch(() => {});
    if (discardRef.current || !uri || !mountedRef.current) return;
    setTranscribing(true);
    setRecError(null);
    try {
      const ext = (uri.split(".").pop() ?? "m4a").toLowerCase();
      const { text } = await transcribeAudio({
        uri,
        ext,
        mimeType: AUDIO_MIME[ext] ?? "audio/m4a",
      });
      if (text && mountedRef.current) {
        setInput((prev) => (prev.trim() ? `${prev.trimEnd()} ${text}` : text));
      }
    } catch (err) {
      if (mountedRef.current) {
        setRecError(err instanceof Error ? err.message : "La transcription a échoué.");
      }
    } finally {
      if (mountedRef.current) setTranscribing(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      discardRef.current = true;
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (recorder) {
        try {
          if (recorder.isRecording) recorder.stop();
        } catch {
          // Already stopped.
        }
        try {
          recorder.release();
        } catch {
          // Already released.
        }
      }
    };
  }, []);

  const startRecording = useCallback(async () => {
    setRecError(null);
    try {
      const permission = await requestRecordingPermissionsAsync();
      if (!permission.granted) {
        setRecError("Micro inaccessible. Autorisez le micro dans les réglages de l'appareil.");
        return;
      }
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
      // Constructed empty on purpose: the (user-facing) preset goes through
      // prepareToRecordAsync, whose shim converts it to the native format.
      const recorder = new AudioModule.AudioRecorder({});
      recorderRef.current = recorder;
      await recorder.prepareToRecordAsync(RecordingPresets.HIGH_QUALITY);
      discardRef.current = false;
      recorder.record();
      recStartTsRef.current = Date.now();
      setRecElapsed(0);
      setRecording(true);
    } catch {
      setRecError("Micro inaccessible. Vérifiez les autorisations de l'appareil.");
    }
  }, []);

  const cancelRecording = useCallback(() => {
    discardRef.current = true;
    void stopRecording();
  }, [stopRecording]);

  // Elapsed-time ticker + hard cap: auto-stop (and transcribe) at the limit.
  useEffect(() => {
    if (!recording) return;
    const tick = setInterval(() => {
      const elapsed = Math.floor((Date.now() - recStartTsRef.current) / 1000);
      setRecElapsed(elapsed);
      if (elapsed >= MAX_REC_SECONDS) void stopRecording();
    }, 1000);
    return () => clearInterval(tick);
  }, [recording, stopRecording]);

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

  function renderMessage(msg: ChatMessage) {
    if (msg.role === "user") {
      return (
        <View style={styles.userRow}>
          <View style={styles.userBubble}>
            <Text style={styles.userText}>{msg.text}</Text>
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
          onPress={() => {
            chatEngine.selectMessage(msg.id);
            if (msg.response) {
              setTab("citations");
              setPanelOpen(true);
            }
          }}
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
              <Text style={styles.quotaHint}>Passez à l'offre supérieure pour continuer.</Text>
            </View>
          ) : response ? (
            <AnswerView answer={response.answer} />
          ) : msg.streaming ? (
            <View style={styles.streamingWrap}>
              <Text style={styles.streamingText}>{msg.text}</Text>
              <View style={styles.cursor} />
            </View>
          ) : (
            <Markdown>{msg.text}</Markdown>
          )}
          {msg.ts ? <Text style={styles.assistantTime}>{formatMessageTime(msg.ts)}</Text> : null}
          {response ? (
            <View style={styles.actionsRow}>
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
        <Text style={styles.headerTitle}>Yawoto</Text>
        <View style={styles.headerActions}>
          <Pressable
            onPress={() => {
              setTab("agents");
              setPanelOpen(true);
            }}
            style={styles.headerButton}
            accessibilityLabel="Détails de la réponse"
          >
            <Ionicons name="analytics-outline" size={18} color={colors.inkSoft} />
          </Pressable>
          <Pressable
            onPress={() => chatEngine.newConversation()}
            style={styles.headerButton}
            accessibilityLabel="Nouvelle conversation"
          >
            <Ionicons name="create-outline" size={18} color={colors.inkSoft} />
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
          <View style={styles.emptyState}>
            <View style={styles.emptyLogo}>
              <Ionicons name="scale" size={34} color="#fff" />
            </View>
            <Text style={styles.emptyTitle}>Le droit, cité à la source.</Text>
            <Text style={styles.emptyBody}>
              Posez vos questions en français. Yawoto répond à partir des textes officiels du
              Burkina Faso et de l'OHADA, avec citations vérifiables et traçabilité complète.
            </Text>
            <View style={styles.suggestions}>
              {SUGGESTIONS.map((s) => (
                <Pressable key={s} onPress={() => setInput(s)} style={styles.suggestion}>
                  <Text style={styles.suggestionText}>{s}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(m) => m.id}
            renderItem={({ item }) => renderMessage(item)}
            contentContainerStyle={styles.messageList}
            ListFooterComponent={
              busy ? (
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
          {recording && (
            <View style={styles.recRow}>
              <View style={styles.recDot} />
              <Text style={styles.recText}>
                Enregistrement… ({recElapsed}s / {MAX_REC_SECONDS}s max)
              </Text>
            </View>
          )}
          {transcribing && (
            <View style={styles.recRow}>
              <ActivityIndicator size={14} color={colors.accent} />
              <Text style={styles.transcribingText}>Transcription en cours…</Text>
            </View>
          )}
          {recError ? <Text style={styles.recError}>{recError}</Text> : null}
          <View style={styles.composerRow}>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder="Posez votre question."
              placeholderTextColor={colors.faint}
              multiline
              editable={!busy && !recording}
              style={styles.input}
            />
            {recording ? (
              <>
                <Pressable
                  onPress={cancelRecording}
                  style={[styles.roundButton, styles.roundButtonMuted]}
                  accessibilityLabel="Annuler l'enregistrement"
                >
                  <Ionicons name="close" size={18} color={colors.inkSoft} />
                </Pressable>
                <Pressable
                  onPress={() => void stopRecording()}
                  style={[styles.roundButton, styles.roundButtonDanger]}
                  accessibilityLabel="Arrêter et transcrire"
                >
                  <Ionicons name="stop" size={16} color="#fff" />
                </Pressable>
              </>
            ) : busy ? (
              <Pressable
                onPress={() => chatEngine.stop()}
                style={[styles.roundButton, styles.roundButtonDanger]}
                accessibilityLabel="Arrêter la génération"
              >
                <Ionicons name="stop" size={16} color="#fff" />
              </Pressable>
            ) : (
              <>
                <Pressable
                  onPress={() => void startRecording()}
                  disabled={transcribing}
                  style={[styles.roundButton, styles.roundButtonMuted, transcribing && { opacity: 0.5 }]}
                  accessibilityLabel="Dicter votre question"
                >
                  <Ionicons name="mic" size={17} color={colors.inkSoft} />
                </Pressable>
                <Pressable
                  onPress={() => {
                    const q = input;
                    setInput("");
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
              </>
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
                { id: "agents", label: "Agents" },
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
                {tab === "agents" && <AgentTimeline statuses={statuses} active={busy} />}
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

const styles = StyleSheet.create({
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
    backgroundColor: colors.surfaceElevated,
  },
  headerTitle: { fontSize: 17, fontWeight: "700", color: colors.accent },
  headerActions: { flexDirection: "row", gap: 4 },
  headerButton: { padding: 8, borderRadius: 8 },
  centerFill: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  loadingText: { fontSize: 13, color: colors.muted },
  emptyState: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  emptyLogo: {
    width: 72,
    height: 72,
    borderRadius: 20,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 16,
  },
  emptyTitle: { fontSize: 22, fontWeight: "600", color: colors.ink, textAlign: "center" },
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
  streamingWrap: { flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end" },
  streamingText: { fontSize: 14, color: colors.inkSoft, lineHeight: 21 },
  cursor: { width: 2, height: 16, backgroundColor: colors.faint, marginLeft: 2 },
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
  recRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 },
  recDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.danger },
  recText: { fontSize: 13, color: colors.danger },
  transcribingText: { fontSize: 13, color: colors.muted },
  recError: { fontSize: 13, color: colors.danger, marginBottom: 6 },
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
