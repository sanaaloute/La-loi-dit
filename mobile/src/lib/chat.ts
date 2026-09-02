// Chat engine — port of the resilience state machine from
// frontend/components/ChatWindow.tsx. Framework-agnostic: screens subscribe
// to an immutable snapshot (useSyncExternalStore) and call engine methods.

import { AppState, type AppStateStatus } from "react-native";
import {
  ApiError,
  cancelChat,
  chat,
  getRunStatus,
  getSession,
  PIPELINE_NODES,
  STEP_LABELS,
  submitFeedback,
  type ChatResponse,
  type ChatSessionDetail,
  type StreamEvent,
} from "./api";
import { streamChat } from "./sse";
import { getModel, getSessionId, getToken, setSessionId } from "./storage";
import { uuid } from "./uuid";

export type NodeStatus = "pending" | "running" | "done";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts?: string;
  response?: ChatResponse;
  streaming?: boolean;
  error?: boolean;
  quota?: boolean;
  feedback?: "thumbs-up" | "thumbs-down";
  feedbackPending?: boolean;
}

export interface ChatSnapshot {
  messages: ChatMessage[];
  sessionId: string | null;
  busy: boolean;
  historyLoading: boolean;
  statuses: Record<string, NodeStatus>;
  selectedId: string | null;
  /** Bump to tell the history screen to reload its list. */
  historyRefresh: number;
}

/** Questions are capped in words (matches backend input_max_words). */
export const MAX_WORDS = 200;

export function countWords(text: string): number {
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

/** Realtime progress line shown while agents are running. */
export function currentStepLabel(statuses: Record<string, NodeStatus>): string {
  const runningIndex = PIPELINE_NODES.findIndex((n) => statuses[n.id] === "running");
  if (runningIndex !== -1) {
    const node = PIPELINE_NODES[runningIndex];
    return STEP_LABELS[node.id] ?? node.label;
  }
  let lastDoneIndex = -1;
  for (let i = PIPELINE_NODES.length - 1; i >= 0; i--) {
    if (statuses[PIPELINE_NODES[i].id] === "done") {
      lastDoneIndex = i;
      break;
    }
  }
  if (lastDoneIndex !== -1) {
    const node = PIPELINE_NODES[lastDoneIndex];
    return STEP_LABELS[node.id] ?? node.label;
  }
  return "Préparation du traitement…";
}

/** Map the persisted session history to UI messages (shared by loadSession
 * and the dropped-connection recovery). */
function mapHistoryMessages(detail: ChatSessionDetail): ChatMessage[] {
  return detail.messages.map((m) => ({
    id: nextId(),
    role: m.role,
    text: m.role === "assistant" && m.answer ? m.answer.answer : m.content,
    ts: m.created_at,
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
}

const initialState = (): ChatSnapshot => ({
  messages: [],
  sessionId: null,
  busy: false,
  historyLoading: false,
  statuses: emptyStatuses(),
  selectedId: null,
  historyRefresh: 0,
});

export class ChatEngine {
  private snapshot: ChatSnapshot = initialState();
  private listeners = new Set<() => void>();

  private abortController: AbortController | null = null;
  // The in-flight run (set while a question is being processed): lets the
  // app-resume handler know a stream is open and worth recovering.
  private inFlight: { sid: string; sendStart: number; query: string } | null = null;
  // Marks an abort triggered by the app-resume handler (as opposed to the
  // user pressing stop): the catch path then recovers instead of interrupting.
  private resumeRecovery = false;
  // Session id found in storage at engine creation (undefined = not read yet).
  private mountSessionId: string | null | undefined = undefined;
  private restored = false;
  // Id of the provisional assistant message receiving `delta` frames (the
  // verified-answer playback), replaced by the full message on `final`.
  private streamMsgId: string | null = null;
  private progressPoll: ReturnType<typeof setInterval> | null = null;
  private appStateSubscription: { remove: () => void } | null = null;

  constructor() {
    // Mobile OSes (screen lock, app switch, network loss/roaming) kill the
    // SSE socket while the app is suspended, without firing any error: on
    // resume the frozen reader would hang until the silence watchdog fires
    // (~15 s). Abort it immediately so the silent history-recovery path
    // starts at once; the backend keeps running and persists the answer.
    this.appStateSubscription = AppState.addEventListener(
      "change",
      this.handleAppStateChange,
    );
  }

  // -------------------------------------------------------------------------
  // Store plumbing (useSyncExternalStore)
  // -------------------------------------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): ChatSnapshot => this.snapshot;

  private patch(patch: Partial<ChatSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }

  // -------------------------------------------------------------------------
  // Pipeline statuses
  // -------------------------------------------------------------------------

  private markNodeRunning(nodeId: string): void {
    const index = PIPELINE_NODES.findIndex((n) => n.id === nodeId);
    if (index === -1) return;
    const next = { ...this.snapshot.statuses };
    PIPELINE_NODES.forEach((n, i) => {
      if (i < index) next[n.id] = "done";
      else if (i === index) next[n.id] = "running";
    });
    this.patch({ statuses: next });
  }

  private markNodeDone(nodeId: string): void {
    const index = PIPELINE_NODES.findIndex((n) => n.id === nodeId);
    if (index === -1) return;
    const next = { ...this.snapshot.statuses };
    PIPELINE_NODES.forEach((n, i) => {
      if (i <= index) next[n.id] = "done";
    });
    this.patch({ statuses: next });
  }

  private markAllDone(): void {
    const next: Record<string, NodeStatus> = {};
    for (const node of PIPELINE_NODES) next[node.id] = "done";
    this.patch({ statuses: next });
  }

  // Progress poll: even when SSE frames are buffered by a proxy, the backend
  // exposes the currently executing node; poll it to keep the timeline and
  // the progress line moving in real time.
  private startProgressPoll(): void {
    this.stopProgressPoll();
    this.progressPoll = setInterval(() => {
      const sid = this.snapshot.sessionId;
      if (!this.snapshot.busy || !sid) return;
      void getRunStatus(sid).then((status) => {
        if (status?.node) this.markNodeRunning(status.node);
      });
    }, 1200);
  }

  private stopProgressPoll(): void {
    if (this.progressPoll) {
      clearInterval(this.progressPoll);
      this.progressPoll = null;
    }
  }

  // -------------------------------------------------------------------------
  // Message mutations
  // -------------------------------------------------------------------------

  private acceptResponse(response: ChatResponse, replaceId?: string | null): void {
    this.markAllDone();
    setSessionId(response.session_id);
    const msg: ChatMessage = {
      id: nextId(),
      role: "assistant",
      text: response.answer.answer,
      ts: new Date().toISOString(),
      response,
    };
    // Delta playback: the provisional streaming message is REPLACED by the
    // authoritative one (no duplicate bubble).
    const prev = this.snapshot.messages;
    const messages =
      replaceId && prev.some((m) => m.id === replaceId)
        ? prev.map((m) => (m.id === replaceId ? msg : m))
        : [...prev, msg];
    this.patch({
      messages,
      sessionId: response.session_id,
      selectedId: msg.id,
      historyRefresh: this.snapshot.historyRefresh + 1,
    });
  }

  // Verified-answer playback: `delta` frames type the final text out in a
  // provisional plain-text message; the `final` frame swaps in the full one.
  private appendDelta(text: string): void {
    let id = this.streamMsgId;
    if (!id) {
      id = nextId();
      this.streamMsgId = id;
    }
    const msgId = id;
    const prev = this.snapshot.messages;
    const messages = prev.some((m) => m.id === msgId)
      ? prev.map((m) => (m.id === msgId ? { ...m, text: m.text + text } : m))
      : [
          ...prev,
          { id: msgId, role: "assistant" as const, text, ts: new Date().toISOString(), streaming: true },
        ];
    this.patch({ messages });
  }

  private discardStreamingMessage(): void {
    const id = this.streamMsgId;
    if (!id) return;
    this.streamMsgId = null;
    this.patch({ messages: this.snapshot.messages.filter((m) => m.id !== id) });
  }

  private failWith(detail: string): void {
    this.patch({
      messages: [
        ...this.snapshot.messages,
        { id: nextId(), role: "assistant", text: detail, ts: new Date().toISOString(), error: true },
      ],
    });
  }

  private quotaReached(detail: string): void {
    this.patch({
      messages: [
        ...this.snapshot.messages,
        { id: nextId(), role: "assistant", text: detail, ts: new Date().toISOString(), quota: true },
      ],
    });
  }

  private interrupted(): void {
    // Drop any half-streamed provisional bubble before the interrupt notice.
    const streamId = this.streamMsgId;
    const base = this.snapshot.messages;
    const messages = streamId ? base.filter((m) => m.id !== streamId) : base;
    this.streamMsgId = null;
    this.patch({
      messages: [
        ...messages,
        {
          id: nextId(),
          role: "assistant",
          text: "Génération interrompue par l'utilisateur.",
          ts: new Date().toISOString(),
        },
      ],
    });
  }

  // -------------------------------------------------------------------------
  // Session management
  // -------------------------------------------------------------------------

  selectMessage(id: string | null): void {
    this.patch({ selectedId: id });
  }

  newConversation(): void {
    setSessionId(null);
    this.patch({
      messages: [],
      sessionId: null,
      selectedId: null,
      statuses: emptyStatuses(),
    });
  }

  /** Full reset on logout: drop everything, next login starts fresh. */
  reset(): void {
    this.stopProgressPoll();
    this.abortController?.abort();
    this.inFlight = null;
    this.streamMsgId = null;
    this.restored = false;
    this.mountSessionId = undefined;
    this.snapshot = initialState();
    for (const listener of this.listeners) listener();
  }

  async loadSession(id: string): Promise<void> {
    if (!getToken() || this.snapshot.busy) return;
    this.patch({ historyLoading: true, messages: [], selectedId: null, statuses: emptyStatuses() });
    try {
      const detail = await getSession(id);
      setSessionId(detail.session_id);
      this.patch({ messages: mapHistoryMessages(detail), sessionId: detail.session_id });
    } catch (err) {
      this.failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
    } finally {
      this.patch({ historyLoading: false });
    }
  }

  /**
   * Restore the active conversation after an app restart: the session id
   * survives in the secure store but the messages live only in the backend.
   * Only the conversation found in storage at engine creation may be
   * reloaded — never a session created later by the user. A session that
   * cannot be read (deleted elsewhere, expired run…) is dropped silently.
   */
  async restoreSession(): Promise<void> {
    if (this.mountSessionId === undefined) {
      this.mountSessionId = getSessionId();
      if (this.mountSessionId) this.patch({ sessionId: this.mountSessionId });
    }
    const sid = this.mountSessionId;
    if (this.restored || !getToken() || !sid || this.snapshot.sessionId !== sid) return;
    this.restored = true;
    this.patch({ historyLoading: true });
    try {
      const detail = await getSession(sid);
      this.patch({ messages: mapHistoryMessages(detail) });
    } catch {
      setSessionId(null);
      this.patch({ sessionId: null });
    } finally {
      this.patch({ historyLoading: false });
    }
  }

  // -------------------------------------------------------------------------
  // Resume / recovery
  // -------------------------------------------------------------------------

  private handleAppStateChange = (state: AppStateStatus): void => {
    if (state !== "active") return;
    if (!this.inFlight) return;
    this.resumeRecovery = true;
    this.abortController?.abort();
  };

  /**
   * Mobile connections die mid-run while the backend keeps working and
   * persists the completed answer in the session history. Poll that history
   * (backend runs are capped at ~10 min) instead of failing outright. The
   * run-status endpoint tells us a server thread is still working on the
   * prompt: while it runs we keep waiting; three consecutive "not running"
   * readings with no answer landed mean the turn is dead — stop early with
   * an honest failure instead of polling the full window in vain.
   */
  private async recoverAnswer(sid: string, sendStart: number, query: string): Promise<boolean> {
    let deadStreak = 0;
    // 5 s cadence × 130 attempts ≈ 11 min: fast answer pickup on phones,
    // still covering the backend run cap (~10 min). Polls are cheap GETs,
    // far below the nginx per-IP rate limits. The stream silence watchdog
    // deliberately stays ~15 s — the backend heartbeat ticks every 10 s,
    // so a shorter watchdog would kill healthy streams mid-run.
    for (let attempt = 0; attempt < 130; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5_000));
      try {
        const detail = await getSession(sid);
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
          this.patch({ messages: mapHistoryMessages(detail) });
          this.markAllDone();
          this.patch({ historyRefresh: this.snapshot.historyRefresh + 1 });
          return true;
        }
      } catch {
        // History not readable yet — keep polling.
      }
      // Null = status unreadable (network blip): never counts as dead.
      const runStatus = await getRunStatus(sid);
      deadStreak = runStatus?.running === false ? deadStreak + 1 : 0;
      if (deadStreak >= 3) return false;
    }
    return false;
  }

  /** Recovery after a dropped connection: poll the session history silently —
   * the "Traitement en cours…" indicator keeps running, no error bubble —
   * until the backend finishes and the persisted answer appears. */
  private async attemptRecovery(sid: string, sendStart: number, query: string): Promise<boolean> {
    return this.recoverAnswer(sid, sendStart, query);
  }

  // -------------------------------------------------------------------------
  // Feedback / stop
  // -------------------------------------------------------------------------

  async sendFeedback(messageId: string, response: ChatResponse, score: "thumbs-up" | "thumbs-down"): Promise<void> {
    this.patch({
      messages: this.snapshot.messages.map((m) =>
        m.id === messageId ? { ...m, feedbackPending: true } : m,
      ),
    });
    try {
      await submitFeedback({ trace_id: response.trace_id, score });
      this.patch({
        messages: this.snapshot.messages.map((m) =>
          m.id === messageId ? { ...m, feedback: score, feedbackPending: false } : m,
        ),
      });
    } catch {
      // Non-blocking: users can retry if they notice the failure.
      this.patch({
        messages: this.snapshot.messages.map((m) =>
          m.id === messageId ? { ...m, feedbackPending: false } : m,
        ),
      });
    }
  }

  stop(): void {
    this.abortController?.abort();
    if (this.snapshot.sessionId) void cancelChat(this.snapshot.sessionId);
  }

  // -------------------------------------------------------------------------
  // Send
  // -------------------------------------------------------------------------

  async send(query: string): Promise<void> {
    const text = query.trim();
    // A prompt can never be submitted without an authenticated session, nor
    // beyond the word limit.
    if (!text || this.snapshot.busy || !getToken() || countWords(text) > MAX_WORDS) return;

    // Ensure a session id exists up front so the run can be cancelled
    // server-side (the backend keys in-flight runs by session_id).
    let sid = this.snapshot.sessionId;
    if (!sid) {
      sid = uuid();
      setSessionId(sid);
      this.patch({ sessionId: sid });
    }

    const controller = new AbortController();
    this.abortController = controller;
    this.streamMsgId = null;
    this.resumeRecovery = false;

    this.patch({
      busy: true,
      statuses: emptyStatuses(),
      messages: [
        ...this.snapshot.messages,
        { id: nextId(), role: "user", text, ts: new Date().toISOString() },
      ],
    });
    this.startProgressPoll();

    const request = { query: text, session_id: sid, language: "fr", model: getModel() ?? undefined };
    const sendStart = Date.now();
    this.inFlight = { sid, sendStart, query: text };
    let streamed = false;
    let cancelled = false;
    // Distinguishes a backend-reported failure (nothing to recover) from a
    // dropped connection (the backend may still complete and persist).
    let serverFailed = false;

    try {
      await streamChat(
        request,
        (event: StreamEvent) => {
          if (event.type === "update") {
            // The node has just finished: mark it done immediately.
            streamed = true;
            this.markNodeDone(event.node);
          } else if (event.type === "node_start") {
            // Node started executing: show it as running in real time.
            streamed = true;
            this.markNodeRunning(event.node);
          } else if (event.type === "delta") {
            // Verified-answer playback: type the final text out progressively.
            streamed = true;
            this.appendDelta(event.text);
          } else if (event.type === "final") {
            streamed = true;
            this.acceptResponse(event.response, this.streamMsgId);
            this.streamMsgId = null;
          } else if (event.type === "cancelled") {
            streamed = true;
            cancelled = true;
            this.discardStreamingMessage();
          } else if (event.type === "error") {
            // The backend itself reported the failure — nothing to recover.
            serverFailed = true;
            throw new Error(event.detail || "Erreur pendant le traitement");
          }
        },
        null,
        controller.signal,
      );
      if (cancelled) this.interrupted();
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        if (this.resumeRecovery) {
          // Aborted by the app-resume handler: the OS killed the socket while
          // the app was suspended, but the backend drains and persists the
          // run — recover the answer from the session history.
          this.resumeRecovery = false;
          const recovered = await this.attemptRecovery(sid, sendStart, text);
          if (!recovered) {
            this.discardStreamingMessage();
            this.failWith("La connexion s'est interrompue. Réessayez.");
          }
        } else {
          this.interrupted();
        }
      } else if (err instanceof ApiError && err.status === 409) {
        this.interrupted();
      } else if (err instanceof ApiError && err.status === 429) {
        this.quotaReached(err.message);
      } else if (!streamed) {
        // The stream never delivered a frame. When the run still reached the
        // backend it is draining there — poll for the persisted answer rather
        // than starting a duplicate run. An unreadable status (null) keeps
        // the historical behavior: one POST /chat attempt.
        const runStatus = await getRunStatus(sid);
        if (runStatus?.running) {
          const recovered = await this.attemptRecovery(sid, sendStart, text);
          if (!recovered) {
            this.failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
          }
        } else {
          try {
            const response = await chat(request);
            this.acceptResponse(response);
          } catch (postErr) {
            if (postErr instanceof ApiError && postErr.status === 409) {
              this.interrupted();
            } else if (postErr instanceof ApiError && postErr.status === 429) {
              this.quotaReached(postErr.message);
            } else if (!(postErr instanceof ApiError) || postErr.status >= 500) {
              // Network failure OR a bare proxy 5xx: the backend run still
              // completes and persists the answer — poll the history.
              const recovered = await this.attemptRecovery(sid, sendStart, text);
              if (!recovered) {
                this.failWith(
                  postErr instanceof Error ? `Erreur : ${postErr.message}` : "Une erreur est survenue.",
                );
              }
            } else {
              this.failWith(
                postErr instanceof Error ? `Erreur : ${postErr.message}` : "Une erreur est survenue.",
              );
            }
          }
        }
      } else {
        // Dropped connection mid-stream: poll the session history for the
        // answer the backend may still be computing before declaring failure.
        const recovered = !serverFailed && (await this.attemptRecovery(sid, sendStart, text));
        if (!recovered) {
          this.discardStreamingMessage();
          this.failWith(err instanceof Error ? `Erreur : ${err.message}` : "Une erreur est survenue.");
        }
      }
    } finally {
      this.abortController = null;
      this.inFlight = null;
      this.stopProgressPoll();
      this.patch({ busy: false });
    }
  }

  destroy(): void {
    this.appStateSubscription?.remove();
    this.stopProgressPoll();
  }
}

/** App-wide singleton: survives tab switches, shared by chat and history. */
export const chatEngine = new ChatEngine();
