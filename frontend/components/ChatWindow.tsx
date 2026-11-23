"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  AlertTriangle,
  ArrowUp,
  Brain,
  ChevronRight,
  FileSearch,
  History,
  Loader2,
  Quote,
  Square,
  ThumbsDown,
  ThumbsUp,
  User,
} from "lucide-react";
import { type NodeStatus } from "@/components/AgentTimeline";
import AnswerDetailsModal, { type AnswerDetailsTab } from "@/components/AnswerDetailsModal";
import AnswerView from "@/components/AnswerView";
import BookmarkButton from "@/components/BookmarkButton";
import CopyButton from "@/components/CopyButton";
import ExportMenu from "@/components/ExportMenu";
import HistoryPanel from "@/components/HistoryPanel";
import Logo from "@/components/Logo";
import ShareAnswerButton from "@/components/ShareAnswerButton";
import { useAuthToken } from "@/lib/useAuth";
import {
  ApiError,
  cancelChat,
  chat,
  getPreferences,
  getRunStatus,
  getSession,
  getSessionId,
  PIPELINE_NODES,
  setSessionId,
  streamChat,
  submitFeedback,
  type ChatResponse,
  type ChatSessionDetail,
  type StreamEvent,
} from "@/lib/api";
import { PERSONA_CHANGED_EVENT, readPersona, type PersonaKey } from "@/lib/persona";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts?: string;
  response?: ChatResponse;
  streaming?: boolean;
  /** Live chain-of-thought accumulated from reasoning_delta frames. */
  thinking?: string;
  error?: boolean;
  quota?: boolean;
  feedback?: "thumbs-up" | "thumbs-down";
  feedbackPending?: boolean;
}

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

/** Collapsible chain-of-thought panel (DeepSeek/ChatGPT pattern): open while
 * the reasoning streams in live, auto-collapses once the answer starts, and
 * stays available — collapsed behind a tap — above the final answer. */
function ThinkingBlock({ text, live = false }: { text: string; live?: boolean }) {
  const [open, setOpen] = useState(live);
  useEffect(() => {
    if (!live) setOpen(false);
  }, [live]);
  return (
    <div className="mb-2 rounded-lg border border-gray-200 bg-gray-50">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-500"
      >
        <Brain className="h-3.5 w-3.5 text-gray-400" />
        Réflexion
        <ChevronRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <div className="max-h-48 overflow-y-auto whitespace-pre-wrap px-3 pb-2 text-xs italic leading-relaxed text-gray-500">
          {text}
        </div>
      )}
    </div>
  );
}

/** Friendly one-line description of the step currently shown to the user. */
const STEP_LABELS: Record<string, string> = {
  input_guardrail: "Vérification des règles de sécurité…",
  refusal: "Vérification des limites…",
  language_gate: "Détection de la langue…",
  query_router: "Analyse de votre requête…",
  planner: "Planification de la recherche…",
  context_agent: "Analyse du contexte…",
  memory_agent: "Consultation de la mémoire…",
  retrieval_branch: "Recherche documentaire…",
  retrieval_merge: "Fusion des résultats…",
  parent_expansion: "Enrichissement des passages…",
  conflict_resolver: "Résolution des conflits…",
  evidence_ranking: "Classement des preuves…",
  coverage_auditor: "Vérification de la couverture…",
  reasoning_agent: "Raisonnement juridique…",
  reflection_agent: "Vérification interne…",
  response_generator: "Rédaction de la réponse…",
  claim_verification: "Vérification des affirmations…",
  citation_verification: "Vérification des citations…",
  output_guardrail: "Contrôle final de conformité…",
};

/** Starter questions on the empty chat screen; the persona (chosen at
 * onboarding) picks the set, "autre"/unknown falls back to the default. */
const DEFAULT_SUGGESTIONS = [
  "Quels sont les droits d'un salarié licencié au Burkina Faso ?",
  "Quelle est la procédure de divorce selon le Code des personnes et de la famille ?",
  "Quelles sont les règles OHADA applicables à la création d'une SARL ?",
];

const PERSONA_SUGGESTIONS: Record<Exclude<PersonaKey, "autre">, string[]> = {
  etudiant: [
    "Explique-moi la différence entre une loi et un décret au Burkina Faso.",
    "Quels sont les éléments constitutifs d'un contrat de travail valable ?",
    "Qu'est-ce que l'OHADA et quel est son rôle ?",
  ],
  juriste: [
    "Quel est le délai de recours contre un licenciement au Burkina Faso ?",
    "Quelle est la procédure de saisine de la CCJA en matière OHADA ?",
    "Quels sont les délais de prescription en matière civile ?",
  ],
  citoyen: [
    "Quels sont mes droits si mon employeur me licencie ?",
    "Comment contester un licenciement que je juge abusif ?",
    "Quels sont les droits des époux en cas de divorce ?",
  ],
};

/** Map the persisted session history to UI messages (shared by loadSession
 * and the dropped-connection recovery). The thinking persisted in the answer
 * metadata is restored into the collapsible Réflexion block. */function mapHistoryMessages(detail: ChatSessionDetail): Message[] {
  return detail.messages.map((m) => {
    const thinking = m.answer?.metadata?.thinking;
    return {
      id: nextId(),
      role: m.role,
      text: m.role === "assistant" && m.answer ? m.answer.answer : m.content,
      ts: m.created_at,
      thinking: typeof thinking === "string" && thinking ? thinking : undefined,
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
    };
  });
}

/** Chat timeline timestamp: HH:MM today, DD/MM HH:MM for older messages. */
function formatMessageTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return time;
  return `${d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} ${time}`;
}

interface ChatWindowProps {
  /** Deep links (from the dashboard): q auto-sends, suggestion only prefills,
   * session loads a past conversation, openHistory opens the drawer. */
  initialQuery?: string | null;
  suggestion?: string | null;
  sessionParam?: string | null;
  openHistory?: boolean;
}

export default function ChatWindow({
  initialQuery = null,
  suggestion = null,
  sessionParam = null,
  openHistory = false,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const wordCount = countWords(input);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionIdState] = useState<string | null>(null);
  const [token] = useAuthToken();
  const [statuses, setStatuses] = useState<Record<string, NodeStatus>>(emptyStatuses);
  // Citations/preuves dialog: which assistant message + which tab is open.
  const [details, setDetails] = useState<{ msgId: string; tab: AnswerDetailsTab } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  // Collapsed by default: the app sidebar already takes horizontal space, so
  // the conversation list starts as an icon rail the user can expand.
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  // Onboarding persona: picks the starter questions of the empty screen.
  const [persona, setPersona] = useState<PersonaKey | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Auto-scroll pinning: the stream is followed only while the user stays near
  // the bottom; scrolling up releases the follow so they can read undisturbed.
  const pinnedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  // The in-flight run (set while a question is being processed): lets the
  // page-resume handler know a stream is open and worth recovering.
  const inFlightRef = useRef<{ sid: string; sendStart: number; query: string } | null>(null);
  // Marks an abort triggered by the page-resume handler (as opposed to the
  // user pressing stop): the catch path then recovers instead of interrupting.
  const resumeRecoveryRef = useRef(false);
  // Set by the stop button: the history-recovery polling loop checks it and
  // exits at once — otherwise a stop pressed during recovery kept the UI busy
  // for minutes (the fetch it aborts is already dead by then).
  const stopRequestedRef = useRef(false);
  // Session id found in localStorage at mount (undefined = not read yet).
  const mountSessionIdRef = useRef<string | null | undefined>(undefined);
  // Id of the provisional assistant message receiving `delta` frames (the
  // verified-answer playback), replaced by the full message on `final`.
  const streamMsgIdRef = useRef<string | null>(null);
  // Chain-of-thought accumulated from reasoning_delta frames for the in-flight
  // run; carried onto the final message so it stays readable above the answer.
  const streamThinkingRef = useRef("");

  useEffect(() => {
    const stored = getSessionId();
    // Snapshot for the refresh-restore effect below: only the conversation
    // found in localStorage at mount may be reloaded — never a session
    // created later by the user (a new run must not be clobbered).
    mountSessionIdRef.current = stored;
    setSessionIdState(stored);
  }, []);

  // Persona for the starter questions: loaded once, then kept in sync with
  // the onboarding modal / account page through PERSONA_CHANGED_EVENT.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getPreferences(token)
      .then((res) => {
        if (!cancelled) setPersona(readPersona(res.preferences));
      })
      .catch(() => {
        // Preferences unavailable: the default suggestions stay.
      });
    function onPersonaChanged(e: Event) {
      setPersona((e as CustomEvent<{ persona?: PersonaKey }>).detail?.persona ?? null);
    }
    window.addEventListener(PERSONA_CHANGED_EVENT, onPersonaChanged);
    return () => {
      cancelled = true;
      window.removeEventListener(PERSONA_CHANGED_EVENT, onPersonaChanged);
    };
  }, [token]);

  const prevBusyRef = useRef(false);
  useEffect(() => {
    // Follow the streaming text instantly while pinned to the bottom; when the
    // run finishes, bring the START of the final answer into view instead of
    // leaving the user at the tail of the stream.
    const justFinished = prevBusyRef.current && !busy;
    prevBusyRef.current = busy;
    if (busy) {
      if (pinnedRef.current) {
        bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
      }
      return;
    }
    if (justFinished) {
      const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
      if (lastAssistant) {
        const target = `msg-${lastAssistant.id}`;
        // Wait for the final answer layout to settle before scrolling.
        setTimeout(() => {
          document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 100);
      }
      return;
    }
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const handleChatScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);

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

  const markNodeRunning = useCallback((nodeId: string) => {
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

  const markNodeDone = useCallback((nodeId: string) => {
    setStatuses((prev) => {
      const index = PIPELINE_NODES.findIndex((n) => n.id === nodeId);
      if (index === -1) return prev;
      const next = { ...prev };
      PIPELINE_NODES.forEach((n, i) => {
        if (i <= index) next[n.id] = "done";
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

  // Progress poll: even when SSE frames are buffered by a proxy, the backend
  // exposes the currently executing node; we poll it to keep the timeline and
  // the mobile progress line moving in real time.
  useEffect(() => {
    if (!busy || !sessionId) return;
    const id = setInterval(() => {
      void getRunStatus(sessionId, token).then((status) => {
        if (status?.node) markNodeRunning(status.node);
      });
    }, 1200);
    return () => clearInterval(id);
  }, [busy, sessionId, token, markNodeRunning]);

  const acceptResponse = useCallback((response: ChatResponse, replaceId?: string | null) => {
    markAllDone();
    setSessionId(response.session_id);
    setSessionIdState(response.session_id);
    // The accumulated chain-of-thought carries over to the final message so it
    // stays readable (collapsed) above the authoritative answer.
    const thinking = streamThinkingRef.current;
    streamThinkingRef.current = "";
    const msg: Message = {
      id: nextId(),
      role: "assistant",
      text: response.answer.answer,
      ts: new Date().toISOString(),
      response,
      ...(thinking ? { thinking } : {}),
    };
    // Delta playback: the provisional streaming message is REPLACED by the
    // authoritative one (no duplicate bubble).
    setMessages((prev) =>
      replaceId && prev.some((m) => m.id === replaceId)
        ? prev.map((m) => (m.id === replaceId ? msg : m))
        : [...prev, msg],
    );
    // Refresh the history list so the new/updated conversation appears.
    setHistoryRefresh((k) => k + 1);
  }, [markAllDone]);

  // Verified-answer playback: `delta` frames type the final text out in a
  // provisional plain-text message; the `final` frame swaps in the full one.
  const appendDelta = useCallback((text: string) => {
    let id = streamMsgIdRef.current;
    if (!id) {
      id = nextId();
      streamMsgIdRef.current = id;
    }
    const msgId = id;
    setMessages((prev) =>
      prev.some((m) => m.id === msgId)
        ? prev.map((m) => (m.id === msgId ? { ...m, text: m.text + text } : m))
        : [...prev, { id: msgId, role: "assistant", text, ts: new Date().toISOString(), streaming: true }],
    );
  }, []);

  // Live chain-of-thought: reasoning_delta frames accumulate in the same
  // provisional bubble (ThinkingBlock above the answer text) and in the ref
  // that carries them onto the final message.
  const appendReasoning = useCallback((text: string) => {
    streamThinkingRef.current += text;
    let id = streamMsgIdRef.current;
    if (!id) {
      id = nextId();
      streamMsgIdRef.current = id;
    }
    const msgId = id;
    setMessages((prev) =>
      prev.some((m) => m.id === msgId)
        ? prev.map((m) => (m.id === msgId ? { ...m, thinking: (m.thinking ?? "") + text } : m))
        : [...prev, { id: msgId, role: "assistant", text: "", thinking: text, ts: new Date().toISOString(), streaming: true }],
    );
  }, []);

  // A corrective pass rewrote the streamed draft: clear the provisional
  // answer text (the accumulated thinking stays) so the rewrite re-streams.
  const resetDelta = useCallback(() => {
    const id = streamMsgIdRef.current;
    if (!id) return;
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, text: "" } : m)));
  }, []);

  const discardStreamingMessage = useCallback(() => {
    const id = streamMsgIdRef.current;
    streamThinkingRef.current = "";
    if (!id) return;
    streamMsgIdRef.current = null;
    setMessages((prev) => prev.filter((m) => m.id !== id));
  }, []);

  const failWith = useCallback((detail: string) => {
    setMessages((prev) => [...prev, { id: nextId(), role: "assistant", text: detail, ts: new Date().toISOString(), error: true }]);
  }, []);

  const quotaReached = useCallback((detail: string) => {
    setMessages((prev) => [...prev, { id: nextId(), role: "assistant", text: detail, ts: new Date().toISOString(), quota: true }]);
  }, []);

  const loadSession = useCallback(
    async (id: string) => {
      if (!token || busy) return;
      setHistoryOpen(false);
      setHistoryLoading(true);
      setMessages([]);
      setStatuses(emptyStatuses());
      try {
        const detail = await getSession(id, token);
        setMessages(mapHistoryMessages(detail));
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

  // Restore the active conversation after a page refresh: the session id
  // survives in localStorage but the messages live only in the backend —
  // reload them once the token is available. A session that cannot be read
  // (deleted elsewhere, expired run…) is dropped silently for a fresh chat;
  // it remains listed in the history panel when it exists server-side.
  const restoredRef = useRef(false);
  useEffect(() => {
    const sid = mountSessionIdRef.current;
    if (restoredRef.current || !token || !sid || sessionId !== sid) return;
    restoredRef.current = true;
    setHistoryLoading(true);
    getSession(sid, token)
      .then((detail) => setMessages(mapHistoryMessages(detail)))
      .catch(() => {
        setSessionId(null);
        setSessionIdState(null);
      })
      .finally(() => setHistoryLoading(false));
  }, [token, sessionId]);

  // Mobile OSes (screen lock, app switch, network loss/roaming) kill the SSE
  // socket while the page is suspended or offline, without firing any error:
  // on resume/reconnect the frozen reader would hang until the silence
  // watchdog fires (~15 s). Abort it immediately so the silent
  // history-recovery path (catch block in `send`) starts at once; the backend
  // keeps running and persists the answer meanwhile.
  useEffect(() => {
    const onResume = () => {
      if (document.visibilityState !== "visible") return;
      if (!inFlightRef.current) return;
      resumeRecoveryRef.current = true;
      abortRef.current?.abort();
    };
    document.addEventListener("visibilitychange", onResume);
    window.addEventListener("pageshow", onResume);
    window.addEventListener("online", onResume);
    return () => {
      document.removeEventListener("visibilitychange", onResume);
      window.removeEventListener("pageshow", onResume);
      window.removeEventListener("online", onResume);
    };
  }, []);

  // Mobile connections die mid-run while the backend keeps working and
  // persists the completed answer in the session history. Poll that history
  // (backend runs are capped at ~10 min) instead of failing outright. Three
  // consecutive "not running" readings normally mean the turn is dead, but
  // not when the run is known active: another API worker cannot see its task.
  const recoverAnswer = useCallback(
    async (sid: string, sendStart: number, query: string, runKnownActive = false): Promise<boolean> => {
      let deadStreak = 0;
      // 5 s cadence × 130 attempts ≈ 11 min: fast answer pickup on phones,
      // still covering the backend run cap (~10 min). Polls are cheap GETs,
      // far below the nginx per-IP rate limits. The stream silence watchdog
      // deliberately stays ~15 s — the backend heartbeat ticks every 10 s,
      // so a shorter watchdog would kill healthy streams mid-run.
      for (let attempt = 0; attempt < 130; attempt += 1) {
        // Interruptible 5 s cadence: the stop button breaks the recovery wait
        // at once instead of polling on for minutes after the stream died.
        for (let waited = 0; waited < 5_000; waited += 200) {
          if (stopRequestedRef.current) return false;
          await new Promise((resolve) => setTimeout(resolve, Math.min(200, 5_000 - waited)));
        }
        if (stopRequestedRef.current) return false;
        try {
          const detail = await getSession(sid, token);
          const msgs = detail.messages;
          const last = msgs[msgs.length - 1];
          const preceding = msgs[msgs.length - 2];
          if (
            last?.role === "assistant" &&
            // A structured FinalAnswer, or a plain-text marker (the backend
            // persists an honest timeout/failure note when a run cannot
            // complete after the client disconnected).
            (last.answer || last.content) &&
            preceding?.role === "user" &&
            // Clock-skew-proof match: the persisted user turn IS the question
            // we sent, or the answer is fresh enough (server timestamps can
            // differ from the client clock, so the text match wins).
            (preceding.content === query || Date.parse(last.created_at) >= sendStart - 5000)
          ) {
            // The run completed server-side while we were disconnected:
            // adopt the server history wholesale.
            setMessages(mapHistoryMessages(detail));
            markAllDone();
            setHistoryRefresh((k) => k + 1);
            return true;
          }
        } catch {
          // History not readable yet — keep polling.
        }
        if (!runKnownActive) {
          // Null = status unreadable (network blip): never counts as dead.
          const runStatus = await getRunStatus(sid, token);
          deadStreak = runStatus?.running === false ? deadStreak + 1 : 0;
          if (deadStreak >= 3) return false;
        }
      }
      return false;
    },
    [token, markAllDone],
  );

  /** Recovery after a dropped connection: poll the session history silently —
   * the "Traitement en cours…" indicator keeps running, no error bubble —
   * until the backend finishes and the persisted answer appears. */
  const attemptRecovery = useCallback(
    async (sid: string, sendStart: number, query: string, runKnownActive = false): Promise<boolean> => {
      return recoverAnswer(sid, sendStart, query, runKnownActive);
    },
    [recoverAnswer],
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
    // Drop any half-streamed provisional bubble before the interrupt notice.
    const streamId = streamMsgIdRef.current;
    if (streamId) {
      streamMsgIdRef.current = null;
      setMessages((prev) => prev.filter((m) => m.id !== streamId));
    }
    setMessages((prev) => [...prev, { id: nextId(), role: "assistant", text: "Génération interrompue par l'utilisateur.", ts: new Date().toISOString() }]);
  }, []);

  const stop = useCallback(() => {
    // Flag first: an in-flight history-recovery loop checks it and exits.
    stopRequestedRef.current = true;
    // Kill the server-side run BEFORE dropping the local stream, so the
    // cancel lands while the run is still registered (a bare disconnect only
    // makes the backend drain the run silently to completion).
    if (sessionId) void cancelChat(sessionId, token);
    abortRef.current?.abort();
  }, [sessionId, token]);

  const send = useCallback(async (override?: string) => {
    const query = (override ?? input).trim();
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
    streamMsgIdRef.current = null;
    streamThinkingRef.current = "";
    resumeRecoveryRef.current = false;
    stopRequestedRef.current = false;

    setInput("");
    setBusy(true);
    setStatuses(emptyStatuses());
    pinnedRef.current = true; // a new question re-pins the follow-the-stream scroll
    setMessages((prev) => [...prev, { id: nextId(), role: "user", text: query, ts: new Date().toISOString() }]);

    const request = {
      query,
      session_id: sid,
      language: "fr",
    };
    const sendStart = Date.now();
    inFlightRef.current = { sid, sendStart, query };
    let streamed = false;
    let streamAccepted = false;
    let cancelled = false;
    // Distinguishes a backend-reported failure (nothing to recover) from a
    // dropped connection (the backend may still complete and persist).
    let serverFailed = false;

    try {
      // scenario_date rides the SSE stream like every other parameter
      // (the backend accepts it as a query param since the P1 work).
      await streamChat(
        request,
        (event: StreamEvent) => {
          if (event.type === "update") {
            // The node has just finished: mark it done immediately.
            streamed = true;
            markNodeDone(event.node);
          } else if (event.type === "node_start") {
            // Node started executing: show it as running in real time.
            streamed = true;
            markNodeRunning(event.node);
          } else if (event.type === "reasoning_delta") {
            // Live chain-of-thought from the reasoning-capable agents.
            streamed = true;
            appendReasoning(event.text);
          } else if (event.type === "delta") {
            // Live answer tokens (or verified-answer playback on fallbacks).
            streamed = true;
            appendDelta(event.text);
          } else if (event.type === "delta_reset") {
            // A corrective pass rewrote the draft: clear and re-stream.
            resetDelta();
          } else if (event.type === "final") {
            streamed = true;
            acceptResponse(event.response, streamMsgIdRef.current);
            streamMsgIdRef.current = null;
          } else if (event.type === "cancelled") {
            streamed = true;
            cancelled = true;
            discardStreamingMessage();
          } else if (event.type === "error") {
            // The backend itself reported the failure — nothing to recover.
            serverFailed = true;
            throw new Error(event.detail || "Erreur pendant le traitement");
          }
        },
        token,
        controller.signal,
        () => {
          streamAccepted = true;
        },
      );
      if (cancelled) interrupted();
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        if (resumeRecoveryRef.current) {
          // Aborted by the page-resume handler: the OS killed the socket while
          // the page was suspended, but the backend drains and persists the
          // run — recover the answer from the session history.
          resumeRecoveryRef.current = false;
          const recovered = await attemptRecovery(sid, sendStart, query, streamAccepted);
          if (!recovered) {
            if (stopRequestedRef.current) {
              interrupted();
            } else {
              discardStreamingMessage();
              failWith("La connexion s'est interrompue. Réessayez.");
            }
          }
        } else {
          interrupted();
        }
      } else if (err instanceof ApiError && err.status === 409) {
        interrupted();
      } else if (err instanceof ApiError && err.status === 429) {
        quotaReached(err.message);
      } else if (serverFailed) {
        failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
      } else if (!streamed) {
        // An accepted stream already has a backend run that will persist its
        // answer or failure marker. Recover it instead of starting a duplicate.
        const runStatus = streamAccepted ? null : await getRunStatus(sid, token);
        if (streamAccepted || runStatus?.running) {
          const recovered = await attemptRecovery(sid, sendStart, query, true);
          if (!recovered) {
            if (stopRequestedRef.current) interrupted();
            else failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
          }
        } else {
          try {
            const response = await chat(request, token);
            acceptResponse(response);
          } catch (postErr) {
            if (postErr instanceof ApiError && postErr.status === 409) {
              interrupted();
            } else if (postErr instanceof ApiError && postErr.status === 429) {
              quotaReached(postErr.message);
            } else if (!(postErr instanceof ApiError) || postErr.status >= 500) {
              // Network failure OR a bare proxy 5xx (the Next.js proxy answers
              // a plain 500 when the mobile connection drops): the backend run
              // still completes and persists the answer — poll the history.
              const recovered = await attemptRecovery(sid, sendStart, query);
              if (!recovered) {
                if (stopRequestedRef.current) interrupted();
                else {
                  failWith(
                    postErr instanceof Error ? `Erreur : ${postErr.message}` : "Une erreur est survenue.",
                  );
                }
              }
            } else {
              failWith(
                postErr instanceof Error ? `Erreur : ${postErr.message}` : "Une erreur est survenue.",
              );
            }
          }
        }
      } else {
        // Dropped connection mid-stream: poll the session history for the
        // answer the backend may still be computing before declaring failure.
        const recovered = !serverFailed && (await attemptRecovery(sid, sendStart, query, true));
        if (!recovered) {
          if (stopRequestedRef.current) {
            interrupted();
          } else {
            discardStreamingMessage();
            failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
          }
        }
      }
    } finally {
      abortRef.current = null;
      inFlightRef.current = null;
      setBusy(false);
    }
  }, [input, busy, sessionId, token, markNodeDone, markNodeRunning, acceptResponse, appendDelta, appendReasoning, resetDelta, discardStreamingMessage, attemptRecovery, failWith, quotaReached, interrupted]);

  function newConversation() {
    setMessages([]);
    setSessionId(null);
    setSessionIdState(null);
    setStatuses(emptyStatuses());
    setDetails(null);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  // The assistant message whose citations/preuves dialog is open (null = closed).
  const detailsMessage = details ? messages.find((m) => m.id === details.msgId) : undefined;

  /** Realtime progress line shown while agents are running (especially on
   * small screens where the full timeline panel is hidden). */
  const currentStepLabel = useMemo(() => {
    const runningIndex = PIPELINE_NODES.findIndex((n) => statuses[n.id] === "running");
    if (runningIndex !== -1) {
      return STEP_LABELS[PIPELINE_NODES[runningIndex].id] ?? PIPELINE_NODES[runningIndex].label;
    }
    let lastDoneIndex = -1;
    for (let i = PIPELINE_NODES.length - 1; i >= 0; i--) {
      if (statuses[PIPELINE_NODES[i].id] === "done") {
        lastDoneIndex = i;
        break;
      }
    }
    if (lastDoneIndex !== -1) {
      return STEP_LABELS[PIPELINE_NODES[lastDoneIndex].id] ?? PIPELINE_NODES[lastDoneIndex].label;
    }
    return "Préparation du traitement…";
  }, [statuses]);

  // The user question each assistant message replies to (per-answer copy
  // and export actions).
  const queryByAssistantId = new Map<string, string>();
  let pendingQuery: string | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      pendingQuery = m.text;
    } else if (m.response && pendingQuery !== null) {
      queryByAssistantId.set(m.id, pendingQuery);
      pendingQuery = null;
    }
  }

  const suggestions = (persona && persona !== "autre" ? PERSONA_SUGGESTIONS[persona] : undefined) ?? DEFAULT_SUGGESTIONS;

  // Deep links from the dashboard/sidebar: consume them exactly once, after
  // the auth token resolves (send/loadSession both require it).
  const deepLinkHandledRef = useRef(false);
  useEffect(() => {
    if (deepLinkHandledRef.current || !token) return;
    if (!initialQuery && !suggestion && !sessionParam && !openHistory) return;
    deepLinkHandledRef.current = true;
    if (openHistory) {
      setHistoryOpen(true); // mobile drawer
      setHistoryCollapsed(false); // desktop rail
    }
    if (sessionParam) {
      void loadSession(sessionParam);
    } else if (initialQuery) {
      void send(initialQuery);
    } else if (suggestion) {
      setInput(suggestion);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // On a fresh chat the input sits directly under the suggestion cards;
  // once the conversation starts it docks at the bottom.
  const showInlineInput = !historyLoading && messages.length === 0;

  const inputForm = (
    <div className="mx-auto w-full max-w-3xl">
      <div className="relative">
      <textarea
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={2}
        placeholder="Posez votre question."
        className="ring-none-focus w-full resize-none rounded-xl border border-gray-200 bg-white py-3 pl-4 pr-14 text-sm text-gray-900 placeholder:text-gray-400 focus:border-accent/60 focus:bg-white focus:outline-none disabled:opacity-60"
        disabled={busy}
      />
      {busy ? (
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
  );

  return (
    <div className="relative flex h-full min-h-0 overflow-hidden">
      {/* History panel (desktop sidebar + mobile drawer) */}
        <HistoryPanel
          token={token}
          activeSessionId={sessionId}
          onSelect={(id) => void loadSession(id)}
          onDeleted={(id) => {
            // If the active conversation was deleted, reset to a fresh chat.
            if (id === sessionId) newConversation();
          }}
          onNewConversation={newConversation}
          refreshKey={historyRefresh}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          collapsed={historyCollapsed}
          onToggleCollapsed={() => setHistoryCollapsed((v) => !v)}
        />

        {/* Chat column */}
        <main className="flex min-w-0 flex-1 flex-col">
          {/* Mobile toolbar: history and details drawers (panels are static on md+) */}
          <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2 md:hidden">
            {token ? (
              <button
                type="button"
                onClick={() => setHistoryOpen(true)}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900"
                title="Historique des conversations"
              >
                <History className="h-5 w-5" />
              </button>
            ) : (
              <span className="h-9 w-9" />
            )}
            <span className="text-sm font-medium text-gray-900">Recherche juridique</span>
            <span className="h-9 w-9" />
          </div>
          <div ref={scrollRef} onScroll={handleChatScroll} className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            {historyLoading ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
                Chargement de la conversation…
              </div>
            ) : messages.length === 0 ? (
              <div className="mx-auto mt-8 flex max-w-2xl flex-col items-center text-center sm:mt-16">
                <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-3xl bg-accent shadow-panel">
                  <Logo className="h-11 w-11 text-white" />
                </div>
                <h2 className="mb-3 text-2xl font-semibold text-gray-900 sm:text-3xl">
                  Le droit, cité à la source.
                </h2>
                <p className="mb-8 max-w-lg text-sm leading-relaxed text-gray-500 sm:text-base">
                  Posez vos questions en français. Yawoto répond à partir des lois et textes
                  officiels du Burkina Faso, avec des citations vérifiables à chaque réponse.
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
                <div className="mt-6 w-full">{inputForm}</div>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((msg) =>
                  msg.role === "user" ? (
                    <div key={msg.id} id={`msg-${msg.id}`} className="flex justify-end gap-3">
                      <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-accent/20 bg-accent/5 px-4 py-3 text-sm text-gray-900 shadow-panel sm:max-w-[75%]">
                        {msg.text}
                        {msg.ts && (
                          <div className="mt-1 text-right text-[10px] text-gray-400">{formatMessageTime(msg.ts)}</div>
                        )}
                      </div>
                      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-600">
                        <User className="h-4 w-4" />
                      </div>
                    </div>
                  ) : (
                    <div key={msg.id} id={`msg-${msg.id}`} className="flex justify-start gap-3">
                      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-white">
                        <Logo className="h-4 w-4" />
                      </div>
                      <div
                        className={`relative max-w-[90%] rounded-2xl rounded-bl-sm border px-4 py-3 sm:max-w-[82%] ${
                          msg.error
                            ? "border-red-700/30 bg-red-700/10"
                            : msg.quota
                              ? "border-warn-border/60 bg-warn-bg"
                              : "border-gray-200 bg-surface/80"
                        }`}
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
                          </div>
                        ) : msg.response ? (
                          <div>
                            {msg.thinking && <ThinkingBlock text={msg.thinking} />}
                            <AnswerView answer={msg.response.answer} />
                          </div>
                        ) : msg.streaming ? (
                          <div className="text-sm text-gray-700">
                            {msg.thinking && <ThinkingBlock text={msg.thinking} live={msg.text.length === 0} />}
                            <div className="whitespace-pre-wrap">
                              {msg.text}
                              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-gray-400 align-text-bottom" />
                            </div>
                          </div>
                        ) : (
                          <div className="markdown-body text-sm text-gray-700">
                            <ReactMarkdown>{msg.text}</ReactMarkdown>
                          </div>
                        )}
                        {msg.ts && (
                          <div className="mt-1.5 text-[10px] text-gray-400">{formatMessageTime(msg.ts)}</div>
                        )}
                        {msg.response && (
                          <div className="mt-2 flex items-center gap-2">
                            {msg.response.answer.citations.length > 0 && (
                              <button
                                type="button"
                                onClick={() => setDetails({ msgId: msg.id, tab: "citations" })}
                                className="flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] font-medium text-gray-500 transition-colors hover:border-accent/40 hover:text-accent"
                              >
                                <Quote className="h-3 w-3" />
                                Citations ({msg.response.answer.citations.length})
                              </button>
                            )}
                            {msg.response.answer.evidence.length > 0 && (
                              <button
                                type="button"
                                onClick={() => setDetails({ msgId: msg.id, tab: "preuves" })}
                                className="flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] font-medium text-gray-500 transition-colors hover:border-accent/40 hover:text-accent"
                              >
                                <FileSearch className="h-3 w-3" />
                                Preuves ({msg.response.answer.evidence.length})
                              </button>
                            )}
                            <span className="flex-1" />
                            <CopyButton text={msg.response.answer.answer} />
                            <ExportMenu
                              response={msg.response}
                              query={queryByAssistantId.get(msg.id) ?? ""}
                              scope="response"
                              iconOnly
                            />
                            <BookmarkButton
                              query={queryByAssistantId.get(msg.id) ?? ""}
                              answer={msg.response.answer.answer}
                              confidence={msg.response.answer.confidence}
                              sessionId={msg.response.session_id}
                              token={token}
                            />
                            <ShareAnswerButton
                              query={queryByAssistantId.get(msg.id) ?? ""}
                              answer={msg.response.answer.answer}
                              citations={msg.response.answer.citations}
                              confidence={msg.response.answer.confidence}
                              token={token}
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
                      </div>
                    </div>
                  ),
                )}
                {busy && (
                  <div className="flex flex-col gap-0.5 text-sm text-gray-500">
                    <div className="flex items-center gap-2">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
                      <span className="truncate">{currentStepLabel}</span>
                    </div>
                    <div className="pl-5 text-xs text-gray-400">
                      Traitement en cours par les agents… ({elapsed}s)
                    </div>
                  </div>
                )}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input (docked at the bottom once the conversation has started) */}
          {!showInlineInput && (
            <div className="z-10 px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 sm:px-6">
              {inputForm}
            </div>
          )}
        </main>

        {/* Citations / preuves dialog (opened from each answer's buttons) */}
        {details && detailsMessage?.response && (
          <AnswerDetailsModal
            citations={detailsMessage.response.answer.citations}
            evidence={detailsMessage.response.answer.evidence}
            initialTab={details.tab}
            onClose={() => setDetails(null)}
          />
        )}

        {/* Mobile overlay */}
        {historyOpen && (
          <button
            type="button"
            onClick={() => setHistoryOpen(false)}
            className="absolute inset-0 z-20 bg-black/60 backdrop-blur-sm md:hidden"
            aria-label="Fermer l'historique"
          />
        )}
    </div>
  );
}
