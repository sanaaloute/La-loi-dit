"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  AlertTriangle,
  ArrowUp,
  Bot,
  History,
  Loader2,
  Menu,
  MessageSquarePlus,
  Mic,
  PanelRight,
  Square,
  ThumbsDown,
  ThumbsUp,
  User,
  X,
} from "lucide-react";
import AgentTimeline, { type NodeStatus } from "@/components/AgentTimeline";
import AnswerView from "@/components/AnswerView";
import AppHeader from "@/components/AppHeader";
import CitationPanel from "@/components/CitationPanel";
import CopyButton from "@/components/CopyButton";
import EvidenceViewer from "@/components/EvidenceViewer";
import ExportMenu from "@/components/ExportMenu";
import HistoryPanel from "@/components/HistoryPanel";
import ModelPicker from "@/components/ModelPicker";
import { useAuthToken } from "@/lib/useAuth";
import {
  ApiError,
  cancelChat,
  chat,
  getModel,
  getSession,
  getSessionId,
  PIPELINE_NODES,
  setSessionId,
  streamChat,
  submitFeedback,
  transcribeAudio,
  type ChatResponse,
  type ExportItem,
  type StreamEvent,
} from "@/lib/api";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
  error?: boolean;
  quota?: boolean;
  feedback?: "thumbs-up" | "thumbs-down";
  feedbackPending?: boolean;
}

type PanelTab = "agents" | "citations" | "preuves";

/** Voice notes are capped: short clips transcribe faster and cost less. */
const MAX_REC_SECONDS = 30;

/** Questions are capped in words (matches backend input_max_words). */
const MAX_WORDS = 200;

function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

let msgCounter = 0;
function nextId(): string {
  msgCounter += 1;
  return `m-${Date.now()}-${msgCounter}`;
}

function emptyStatuses(): Record<string, NodeStatus> {
  const statuses: Record<string, NodeStatus> = {};
  for (const node of PIPELINE_NODES) statuses[node.id] = "pending";
  return statuses;
}

export default function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const wordCount = countWords(input);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionIdState] = useState<string | null>(null);
  const [token] = useAuthToken();
  const [statuses, setStatuses] = useState<Record<string, NodeStatus>>(emptyStatuses);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<PanelTab>("agents");
  const [panelOpen, setPanelOpen] = useState(false);
  const [model, setModelState] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Voice input: MediaRecorder -> POST /chat/transcribe -> text inserted into
  // the composer (never auto-sent; the user reviews it first).
  const [micSupported] = useState(
    () =>
      typeof navigator !== "undefined" &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== "undefined",
  );
  const [recording, setRecording] = useState(false);
  const [recElapsed, setRecElapsed] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const [recError, setRecError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const discardRef = useRef(false);
  const mountedRef = useRef(true);

  // Release the microphone when the component unmounts mid-recording, and
  // silence any late recorder callbacks (no setState/transcribe after
  // unmount).
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      discardRef.current = true;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // Already stopped — nothing to release.
        }
      }
      recorderRef.current = null;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  useEffect(() => {
    setSessionIdState(getSessionId());
    setModelState(getModel());
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

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

  // Elapsed seconds while recording audio.
  useEffect(() => {
    if (!recording) {
      setRecElapsed(0);
      return;
    }
    const t0 = Date.now();
    const id = setInterval(() => setRecElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [recording]);

  const markNode = useCallback((nodeId: string) => {
    setStatuses((prev) => {
      const index = PIPELINE_NODES.findIndex((n) => n.id === nodeId);
      if (index === -1) return prev;
      const next = { ...prev };
      PIPELINE_NODES.forEach((n, i) => {
        if (i < index) next[n.id] = "done";
        else if (i === index) next[n.id] = "running";
      });
      return next;
    });
  }, []);

  const markAllDone = useCallback(() => {
    setStatuses(() => {
      const next: Record<string, NodeStatus> = {};
      for (const node of PIPELINE_NODES) next[node.id] = "done";
      return next;
    });
  }, []);

  const acceptResponse = useCallback((response: ChatResponse) => {
    markAllDone();
    setSessionId(response.session_id);
    setSessionIdState(response.session_id);
    const msg: Message = {
      id: nextId(),
      role: "assistant",
      text: response.answer.answer,
      response,
    };
    setMessages((prev) => [...prev, msg]);
    setSelectedId(msg.id);
    // Refresh the history list so the new/updated conversation appears.
    setHistoryRefresh((k) => k + 1);
  }, [markAllDone]);

  const failWith = useCallback((detail: string) => {
    setMessages((prev) => [...prev, { id: nextId(), role: "assistant", text: detail, error: true }]);
  }, []);

  const quotaReached = useCallback((detail: string) => {
    setMessages((prev) => [...prev, { id: nextId(), role: "assistant", text: detail, quota: true }]);
  }, []);

  const loadSession = useCallback(
    async (id: string) => {
      if (!token || busy) return;
      setHistoryOpen(false);
      setHistoryLoading(true);
      setMessages([]);
      setSelectedId(null);
      setStatuses(emptyStatuses());
      try {
        const detail = await getSession(id, token);
        const loaded: Message[] = detail.messages.map((m) => ({
          id: nextId(),
          role: m.role,
          text: m.role === "assistant" && m.answer ? m.answer.answer : m.content,
          response:
            m.role === "assistant" && m.answer
              ? {
                  session_id: detail.session_id,
                  answer: m.answer,
                  trace: [],
                  latency_ms: 0,
                  trace_id: "",
                }
              : undefined,
        }));
        setMessages(loaded);
        setSessionIdState(detail.session_id);
        setSessionId(detail.session_id);
      } catch (err) {
        failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
      } finally {
        setHistoryLoading(false);
      }
    },
    [token, busy, failWith],
  );

  const sendFeedback = useCallback(
    async (messageId: string, response: ChatResponse, score: "thumbs-up" | "thumbs-down") => {
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, feedbackPending: true } : m)),
      );
      try {
        await submitFeedback({ trace_id: response.trace_id, score }, token);
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, feedback: score, feedbackPending: false } : m)),
        );
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, feedbackPending: false } : m)),
        );
        // Non-blocking: users can retry if they notice the failure.
        console.error("Feedback failed", err);
      }
    },
    [token],
  );

  const interrupted = useCallback(() => {
    setMessages((prev) => [...prev, { id: nextId(), role: "assistant", text: "Génération interrompue par l'utilisateur." }]);
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    if (sessionId) void cancelChat(sessionId, token);
  }, [sessionId, token]);

  // Send a recorded blob for transcription and insert the text into the
  // composer (the user reviews and sends it through the normal chat flow).
  const sendForTranscription = useCallback(
    async (blob: Blob) => {
      setTranscribing(true);
      setRecError(null);
      try {
        const { text } = await transcribeAudio(blob, token);
        if (text) setInput((prev) => (prev.trim() ? `${prev.trimEnd()} ${text}` : text));
      } catch (err) {
        setRecError(err instanceof Error ? err.message : "La transcription a échoué.");
      } finally {
        setTranscribing(false);
      }
    },
    [token],
  );

  const startRecording = useCallback(async () => {
    setRecError(null);
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      streamRef.current = stream;
      chunksRef.current = [];
      discardRef.current = false;
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        // Always release the microphone when recording ends.
        recorder.stream.getTracks().forEach((t) => t.stop());
        if (streamRef.current === stream) streamRef.current = null;
        // Unmounted (or cancelled) mid-recording: no setState, no transcribe.
        if (discardRef.current || !mountedRef.current) return;
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        void sendForTranscription(blob);
      };
      recorder.start();
      setRecording(true);
    } catch {
      // getUserMedia succeeded but a later step threw: release the mic.
      stream?.getTracks().forEach((t) => t.stop());
      if (streamRef.current === stream) streamRef.current = null;
      setRecError("Micro inaccessible. Vérifiez les autorisations du navigateur.");
    }
  }, [sendForTranscription]);

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  }, []);

  const cancelRecording = useCallback(() => {
    discardRef.current = true;
    stopRecording();
  }, [stopRecording]);

  // Hard cap on recording length: auto-stop (and transcribe) at the limit.
  useEffect(() => {
    if (recording && recElapsed >= MAX_REC_SECONDS) stopRecording();
  }, [recording, recElapsed, stopRecording]);

  const send = useCallback(async () => {
    const query = input.trim();
    // A prompt can never be submitted without an authenticated session, nor
    // beyond the word limit.
    if (!query || busy || !token || countWords(query) > MAX_WORDS) return;

    // Ensure a session id exists up front so the run can be cancelled
    // server-side (the backend keys in-flight runs by session_id).
    let sid = sessionId;
    if (!sid) {
      sid = crypto.randomUUID();
      setSessionId(sid);
      setSessionIdState(sid);
    }

    const controller = new AbortController();
    abortRef.current = controller;

    setInput("");
    setBusy(true);
    setStatuses(emptyStatuses());
    setTab("agents");
    setMessages((prev) => [...prev, { id: nextId(), role: "user", text: query }]);

    const request = { query, session_id: sid, language: "fr", model: model ?? undefined };
    let streamed = false;
    let cancelled = false;

    try {
      await streamChat(
        request,
        (event: StreamEvent) => {
          if (event.type === "update") {
            streamed = true;
            markNode(event.node);
          } else if (event.type === "node_start") {
            // Node started executing: show it as running in real time.
            streamed = true;
            markNode(event.node);
          } else if (event.type === "final") {
            streamed = true;
            acceptResponse(event.response);
          } else if (event.type === "cancelled") {
            streamed = true;
            cancelled = true;
          } else if (event.type === "error") {
            throw new Error(event.detail || "Erreur pendant le traitement");
          }
        },
        token,
        controller.signal,
      );
      if (cancelled) interrupted();
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        interrupted();
      } else if (err instanceof ApiError && err.status === 409) {
        interrupted();
      } else if (err instanceof ApiError && err.status === 429) {
        quotaReached(err.message);
      } else if (!streamed) {
        try {
          const response = await chat(request, token);
          acceptResponse(response);
        } catch (postErr) {
          if (postErr instanceof ApiError && postErr.status === 409) {
            interrupted();
          } else if (postErr instanceof ApiError && postErr.status === 429) {
            quotaReached(postErr.message);
          } else {
            failWith(
              postErr instanceof Error ? `Erreur : ${postErr.message}` : "Une erreur est survenue.",
            );
          }
        }
      } else {
        failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }, [input, busy, sessionId, token, model, markNode, acceptResponse, failWith, quotaReached, interrupted]);

  function newConversation() {
    setMessages([]);
    setSessionId(null);
    setSessionIdState(null);
    setSelectedId(null);
    setStatuses(emptyStatuses());
    setPanelOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  const selectedIndex = messages.findIndex((m) => m.id === selectedId);
  const selectedMessage = selectedIndex >= 0 ? messages[selectedIndex] : undefined;
  const selectedAnswer = selectedMessage?.response?.answer;

  // The user question the selected answer replies to (not the answer text).
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
  // plus the user question each assistant message replies to (per-answer copy
  // and export actions).
  const conversationItems: ExportItem[] = [];
  const queryByAssistantId = new Map<string, string>();
  let pendingQuery: string | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      pendingQuery = m.text;
    } else if (m.response && pendingQuery !== null) {
      conversationItems.push({ query: pendingQuery, answer: m.response.answer });
      queryByAssistantId.set(m.id, pendingQuery);
      pendingQuery = null;
    }
  }

  const suggestions = [
    "Quels sont les droits d'un salarié licencié au Burkina Faso ?",
    "Quelle est la procédure de divorce selon le Code des personnes et de la famille ?",
    "Quelles sont les règles OHADA applicables à la création d'une SARL ?",
  ];

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* Header */}
      <AppHeader
        token={token}
        leftSlot={
          token ? (
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900 md:hidden"
              title="Historique des conversations"
            >
              <History className="h-5 w-5" />
            </button>
          ) : undefined
        }
      >
        <button
          type="button"
          onClick={() => setPanelOpen((v) => !v)}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900 md:hidden"
          title="Panneau latéral"
        >
          <PanelRight className="h-5 w-5" />
        </button>
        <ModelPicker token={token} value={model} onChange={setModelState} />
        <button
          type="button"
          onClick={newConversation}
          className="hidden items-center gap-1.5 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700 backdrop-blur-sm transition-colors hover:border-gray-400 hover:bg-gray-100 sm:flex"
        >
          <MessageSquarePlus className="h-4 w-4" />
          Nouvelle conversation
        </button>
      </AppHeader>

      {/* Main area */}
      <div className="relative flex min-h-0 flex-1">
        {/* History panel (desktop sidebar + mobile drawer) */}
        <HistoryPanel
          token={token}
          activeSessionId={sessionId}
          onSelect={(id) => void loadSession(id)}
          onDeleted={(id) => {
            // If the active conversation was deleted, reset to a fresh chat.
            if (id === sessionId) newConversation();
          }}
          refreshKey={historyRefresh}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          collapsed={historyCollapsed}
          onToggleCollapsed={() => setHistoryCollapsed((v) => !v)}
        />

        {/* Chat column */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            {historyLoading ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
                Chargement de la conversation…
              </div>
            ) : messages.length === 0 ? (
              <div className="mx-auto mt-8 flex max-w-2xl flex-col items-center text-center sm:mt-16">
                <div className="mb-6 flex h-20 w-20 animate-float items-center justify-center rounded-3xl bg-accent shadow-panel">
                  <Bot className="h-10 w-10 text-white" />
                </div>
                <h2 className="mb-3 text-2xl font-semibold text-gray-900 sm:text-3xl">
                  Le droit, cité à la source.
                </h2>
                <p className="mb-8 max-w-lg text-sm leading-relaxed text-gray-500 sm:text-base">
                  Posez vos questions en français. Yawoto répond à partir des textes officiels du
                  Burkina Faso et de l&apos;OHADA, avec citations vérifiables et traçabilité
                  complète.
                </p>
                <div className="grid w-full gap-3 sm:grid-cols-3">
                  {suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => {
                        setInput(s);
                      }}
                      className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-left text-sm text-gray-600 backdrop-blur-sm transition-all hover:border-accent/50 hover:bg-gray-100 hover:text-gray-900"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((msg) =>
                  msg.role === "user" ? (
                    <div key={msg.id} className="flex justify-end gap-3">
                      <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-accent/20 bg-accent/5 px-4 py-3 text-sm text-gray-900 shadow-panel sm:max-w-[75%]">
                        {msg.text}
                      </div>
                      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-600">
                        <User className="h-4 w-4" />
                      </div>
                    </div>
                  ) : (
                    <div key={msg.id} className="flex justify-start gap-3">
                      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-white">
                        <Bot className="h-4 w-4" />
                      </div>
                      <button
                        type="button"
                        onClick={() => setSelectedId(msg.id)}
                        className={`relative max-w-[90%] rounded-2xl rounded-bl-sm border px-4 py-3 text-left transition-all sm:max-w-[82%] ${
                          msg.error
                            ? "border-red-700/30 bg-red-700/10"
                            : msg.quota
                              ? "border-warn-border/60 bg-warn-bg"
                              : selectedId === msg.id
                                ? "border-accent/40 bg-surface-elevated"
                                : "border-gray-200 bg-surface/80 hover:border-gray-400 hover:bg-surface-elevated"
                        }`}
                        title="Sélectionner pour voir citations et preuves"
                      >
                        {msg.response && (
                          <div
                            className="absolute right-2 top-2 flex items-center gap-1.5"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <CopyButton text={msg.response.answer.answer} />
                            <ExportMenu
                              response={msg.response}
                              query={queryByAssistantId.get(msg.id) ?? ""}
                              scope="response"
                              iconOnly
                            />
                          </div>
                        )}
                        <div className={msg.response ? "pt-7" : undefined}>
                        {msg.error ? (
                          <p className="text-sm text-red-700">{msg.text}</p>
                        ) : msg.quota ? (
                          <div className="text-sm">
                            <p className="mb-1 flex items-center gap-2 font-semibold text-warn-text">
                              <AlertTriangle className="h-4 w-4" />
                              Quota journalier atteint
                            </p>
                            <p className="text-warn-text">{msg.text}</p>
                            <p className="mt-1 text-xs text-warn-text/80">
                              Passez à l&apos;offre supérieure pour continuer.
                            </p>
                          </div>
                        ) : msg.response ? (
                          <AnswerView answer={msg.response.answer} />
                        ) : (
                          <div className="markdown-body text-sm text-gray-700">
                            <ReactMarkdown>{msg.text}</ReactMarkdown>
                          </div>
                        )}
                        {msg.response && (
                          <div className="mt-2 flex items-center justify-end gap-2">
                            <CopyButton text={msg.response.answer.answer} />
                            <ExportMenu
                              response={msg.response}
                              query={queryByAssistantId.get(msg.id) ?? ""}
                              scope="response"
                              iconOnly
                            />
                            {msg.response.trace_id && (
                              <>
                              <button
                                type="button"
                                disabled={msg.feedbackPending}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void sendFeedback(msg.id, msg.response!, "thumbs-up");
                                }}
                                className={`rounded p-1 transition-colors ${
                                  msg.feedback === "thumbs-up"
                                    ? "text-accent"
                                    : "text-gray-500 hover:text-gray-600"
                                }`}
                                aria-label="Utile"
                                title="Utile"
                              >
                                <ThumbsUp className={`h-3.5 w-3.5 ${msg.feedbackPending ? "opacity-50" : ""}`} />
                              </button>
                              <button
                                type="button"
                                disabled={msg.feedbackPending}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void sendFeedback(msg.id, msg.response!, "thumbs-down");
                                }}
                                className={`rounded p-1 transition-colors ${
                                  msg.feedback === "thumbs-down"
                                    ? "text-red-700"
                                    : "text-gray-500 hover:text-gray-600"
                                }`}
                                aria-label="Pas utile"
                                title="Pas utile"
                              >
                                <ThumbsDown className={`h-3.5 w-3.5 ${msg.feedbackPending ? "opacity-50" : ""}`} />
                              </button>
                              </>
                            )}
                          </div>
                        )}
                        </div>
                      </button>
                    </div>
                  ),
                )}
                {busy && (
                  <div className="flex items-center gap-3 text-sm text-gray-500">
                    <span className="relative flex h-3 w-3">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
                      <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
                    </span>
                    Traitement en cours par les agents… ({elapsed}s)
                  </div>
                )}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="glass z-10 px-4 py-3 sm:px-6">
            <div className="mx-auto max-w-3xl">
              {recording && (
                <div className="mb-2 flex items-center gap-2 text-sm text-red-700">
                  <span className="relative flex h-3 w-3">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-600 opacity-75" />
                    <span className="relative inline-flex h-3 w-3 rounded-full bg-red-600" />
                  </span>
                  Enregistrement… ({recElapsed}s / {MAX_REC_SECONDS}s max)
                </div>
              )}
              {transcribing && (
                <div className="mb-2 flex items-center gap-2 text-sm text-gray-500">
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  Transcription en cours…
                </div>
              )}
              {recError && <p className="mb-2 text-sm text-red-700">{recError}</p>}
              <div className="relative">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={2}
                placeholder="Votre question juridique… (Entrée pour envoyer, Maj+Entrée pour sauter une ligne)"
                className={`w-full resize-none rounded-xl border border-gray-200 bg-white py-3 pl-4 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:bg-white focus:outline-none disabled:opacity-60 ${
                  recording || (micSupported && !busy) ? "pr-24" : "pr-14"
                }`}
                disabled={busy}
              />
              {recording ? (
                <>
                  <button
                    type="button"
                    onClick={cancelRecording}
                    title="Annuler l'enregistrement"
                    aria-label="Annuler l'enregistrement"
                    className="absolute right-14 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-gray-200 text-gray-600 transition-colors hover:bg-gray-300"
                  >
                    <X className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={stopRecording}
                    title="Arrêter et transcrire"
                    aria-label="Arrêter et transcrire"
                    className="absolute right-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-red-700 text-white transition-colors hover:bg-red-800"
                  >
                    <Square className="h-4 w-4" />
                  </button>
                </>
              ) : busy ? (
                <button
                  type="button"
                  onClick={stop}
                  title="Arrêter la génération"
                  aria-label="Arrêter la génération"
                  className="absolute right-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-red-700 text-white transition-colors hover:bg-red-800"
                >
                  <Square className="h-4 w-4" />
                </button>
              ) : (
                <>
                  {micSupported && (
                    <button
                      type="button"
                      onClick={() => void startRecording()}
                      disabled={transcribing}
                      title="Dicter votre question"
                      aria-label="Dicter votre question"
                      className="absolute right-14 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-gray-200 text-gray-600 transition-colors hover:bg-gray-300 disabled:opacity-50"
                    >
                      <Mic className="h-4 w-4" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void send()}
                    disabled={input.trim().length === 0 || wordCount > MAX_WORDS}
                    title={wordCount > MAX_WORDS ? `Limite de ${MAX_WORDS} mots dépassée` : "Envoyer"}
                    aria-label="Envoyer"
                    className={`absolute right-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full transition-colors ${
                      input.trim().length === 0 || wordCount > MAX_WORDS
                        ? "cursor-not-allowed bg-gray-200 text-gray-400"
                        : "bg-accent text-white hover:bg-accent-hover"
                    }`}
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                </>
              )}
              </div>
              {wordCount > 0 && (
                <p
                  className={`mt-1 text-right text-xs ${
                    wordCount > MAX_WORDS ? "font-semibold text-red-700" : "text-gray-400"
                  }`}
                >
                  {wordCount}/{MAX_WORDS} mots
                  {wordCount > MAX_WORDS ? " — raccourcissez votre question" : ""}
                </p>
              )}
            </div>
          </div>
        </main>

        {/* Side panel (desktop + mobile drawer) */}
        <aside
          className={`absolute inset-y-0 right-0 z-30 flex w-[min(86vw,22rem)] flex-col border-l border-gray-200 bg-white shadow-2xl backdrop-blur-xl transition-transform duration-300 md:static md:w-80 md:translate-x-0 md:bg-surface/70 md:shadow-none lg:w-96 ${
            panelOpen ? "translate-x-0" : "translate-x-full md:translate-x-0"
          }`}
        >
          <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 md:hidden">
            <span className="text-sm font-medium text-gray-900">Détails de la réponse</span>
            <button
              type="button"
              onClick={() => setPanelOpen(false)}
              className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-900"
            >
              <Menu className="h-5 w-5" />
            </button>
          </div>
          <div className="flex border-b border-gray-200 bg-gray-50">
            {(
              [
                { id: "agents", label: "Agents" },
                { id: "citations", label: "Citations" },
                { id: "preuves", label: "Preuves" },
              ] as { id: PanelTab; label: string }[]
            ).map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`flex-1 px-2 py-3 text-xs font-medium transition-colors sm:text-sm ${
                  tab === t.id
                    ? "border-b-2 border-accent text-accent"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto">
            {tab === "agents" && <AgentTimeline statuses={statuses} active={busy} />}
            {tab === "citations" && <CitationPanel citations={selectedAnswer?.citations ?? []} />}
            {tab === "preuves" && <EvidenceViewer evidence={selectedAnswer?.evidence ?? []} />}
          </div>
          {selectedMessage?.response && conversationItems.length > 0 && (
            <div className="border-t border-gray-200 p-3">
              <ExportMenu
                response={selectedMessage.response}
                query={selectedQuery}
                conversation={conversationItems}
                scope="conversation"
              />
            </div>
          )}
        </aside>

        {/* Mobile overlay */}
        {panelOpen && (
          <button
            type="button"
            onClick={() => setPanelOpen(false)}
            className="absolute inset-0 z-20 bg-black/60 backdrop-blur-sm md:hidden"
            aria-label="Fermer le panneau"
          />
        )}
        {historyOpen && (
          <button
            type="button"
            onClick={() => setHistoryOpen(false)}
            className="absolute inset-0 z-20 bg-black/60 backdrop-blur-sm md:hidden"
            aria-label="Fermer l'historique"
          />
        )}
      </div>
    </div>
  );
}
