"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Bot,
  AlertTriangle,
  History,
  Loader2,
  Menu,
  MessageSquarePlus,
  PanelRight,
  Send,
  ThumbsDown,
  ThumbsUp,
  User,
} from "lucide-react";
import AgentTimeline, { type NodeStatus } from "@/components/AgentTimeline";
import AnswerView from "@/components/AnswerView";
import AppHeader from "@/components/AppHeader";
import CitationPanel from "@/components/CitationPanel";
import EvidenceViewer from "@/components/EvidenceViewer";
import ExportMenu from "@/components/ExportMenu";
import HistoryPanel from "@/components/HistoryPanel";
import ModelPicker from "@/components/ModelPicker";
import {
  ApiError,
  chat,
  getModel,
  getSession,
  getSessionId,
  getToken,
  PIPELINE_NODES,
  setSessionId,
  streamChat,
  submitFeedback,
  type ChatResponse,
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
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionIdState] = useState<string | null>(null);
  const [token, setTokenState] = useState<string | null>(null);
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

  useEffect(() => {
    setSessionIdState(getSessionId());
    setTokenState(getToken());
    setModelState(getModel());
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

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

  const send = useCallback(async () => {
    const query = input.trim();
    if (!query || busy) return;

    setInput("");
    setBusy(true);
    setStatuses(emptyStatuses());
    setTab("agents");
    setMessages((prev) => [...prev, { id: nextId(), role: "user", text: query }]);

    const request = { query, session_id: sessionId ?? undefined, language: "fr", model: model ?? undefined };
    let streamed = false;

    try {
      await streamChat(
        request,
        (event: StreamEvent) => {
          if (event.type === "update") {
            streamed = true;
            markNode(event.node);
          } else if (event.type === "final") {
            streamed = true;
            acceptResponse(event.response);
          } else if (event.type === "error") {
            throw new Error(event.detail || "Erreur pendant le traitement");
          }
        },
        token,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        quotaReached(err.message);
      } else if (!streamed) {
        try {
          const response = await chat(request, token);
          acceptResponse(response);
        } catch (postErr) {
          if (postErr instanceof ApiError && postErr.status === 429) {
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
      setBusy(false);
    }
  }, [input, busy, sessionId, token, model, markNode, acceptResponse, failWith, quotaReached]);

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

  const selectedMessage = messages.find((m) => m.id === selectedId);
  const selectedAnswer = selectedMessage?.response?.answer;

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
        onTokenChange={setTokenState}
        leftSlot={
          token ? (
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-300 transition-colors hover:bg-white/5 hover:text-white md:hidden"
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
          className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-300 transition-colors hover:bg-white/5 hover:text-white md:hidden"
          title="Panneau latéral"
        >
          <PanelRight className="h-5 w-5" />
        </button>
        <ModelPicker token={token} value={model} onChange={setModelState} />
        <button
          type="button"
          onClick={newConversation}
          className="hidden items-center gap-1.5 rounded-lg border border-slate-600/60 bg-slate-800/60 px-3 py-2 text-xs font-medium text-slate-200 backdrop-blur-sm transition-colors hover:border-slate-500 hover:bg-slate-700/60 sm:flex"
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
              <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin text-law-cyan" />
                Chargement de la conversation…
              </div>
            ) : messages.length === 0 ? (
              <div className="mx-auto mt-8 flex max-w-2xl flex-col items-center text-center sm:mt-16">
                <div className="mb-6 flex h-20 w-20 animate-float items-center justify-center rounded-3xl bg-gradient-to-br from-law-cyan via-law-blue to-law-purple shadow-glow">
                  <Bot className="h-10 w-10 text-white" />
                </div>
                <h2 className="mb-3 text-2xl font-semibold text-white sm:text-3xl">
                  Posez votre question de droit
                </h2>
                <p className="mb-8 max-w-lg text-sm leading-relaxed text-slate-400 sm:text-base">
                  Assistant agentique de recherche juridique pour l&apos;Afrique de l&apos;Ouest
                  (OHADA et droits nationaux). Réponses fondées sur des sources officielles,
                  citations vérifiées et traçabilité complète.
                </p>
                <div className="grid w-full gap-3 sm:grid-cols-3">
                  {suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => {
                        setInput(s);
                      }}
                      className="rounded-xl border border-slate-700/60 bg-slate-800/40 p-4 text-left text-sm text-slate-300 backdrop-blur-sm transition-all hover:border-law-cyan/50 hover:bg-slate-700/50 hover:text-white"
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
                      <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-law-cyan/20 bg-gradient-to-br from-law-cyan/20 to-law-blue/20 px-4 py-3 text-sm text-white shadow-panel sm:max-w-[75%]">
                        {msg.text}
                      </div>
                      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-700 text-slate-300">
                        <User className="h-4 w-4" />
                      </div>
                    </div>
                  ) : (
                    <div key={msg.id} className="flex justify-start gap-3">
                      <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-law-purple to-law-blue text-white shadow-glow-sm">
                        <Bot className="h-4 w-4" />
                      </div>
                      <button
                        type="button"
                        onClick={() => setSelectedId(msg.id)}
                        className={`max-w-[90%] rounded-2xl rounded-bl-sm border px-4 py-3 text-left transition-all sm:max-w-[82%] ${
                          msg.error
                            ? "border-rose-500/30 bg-rose-500/10"
                            : msg.quota
                              ? "border-amber-500/30 bg-amber-500/10"
                              : selectedId === msg.id
                                ? "border-law-cyan/40 bg-surface-elevated shadow-glow-sm"
                                : "border-slate-700/60 bg-surface/80 hover:border-slate-600 hover:bg-surface-elevated"
                        }`}
                        title="Sélectionner pour voir citations et preuves"
                      >
                        {msg.error ? (
                          <p className="text-sm text-rose-300">{msg.text}</p>
                        ) : msg.quota ? (
                          <div className="text-sm">
                            <p className="mb-1 flex items-center gap-2 font-semibold text-amber-300">
                              <AlertTriangle className="h-4 w-4" />
                              Quota journalier atteint
                            </p>
                            <p className="text-amber-100">{msg.text}</p>
                            <p className="mt-1 text-xs text-amber-300/80">
                              Passez à l&apos;offre supérieure pour continuer.
                            </p>
                          </div>
                        ) : msg.response ? (
                          <AnswerView answer={msg.response.answer} />
                        ) : (
                          <div className="markdown-body text-sm text-slate-200">
                            <ReactMarkdown>{msg.text}</ReactMarkdown>
                          </div>
                        )}
                        {msg.response && msg.response.latency_ms > 0 && (
                          <p className="mt-2 text-[11px] text-slate-500">
                            {msg.response.latency_ms.toFixed(0)} ms — cliquer pour voir les détails
                          </p>
                        )}
                        {msg.response && msg.response.trace_id && (
                          <div className="mt-2 flex items-center gap-2">
                              <button
                                type="button"
                                disabled={msg.feedbackPending}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void sendFeedback(msg.id, msg.response!, "thumbs-up");
                                }}
                                className={`rounded p-1 transition-colors ${
                                  msg.feedback === "thumbs-up"
                                    ? "text-emerald-400"
                                    : "text-slate-500 hover:text-slate-300"
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
                                    ? "text-rose-400"
                                    : "text-slate-500 hover:text-slate-300"
                                }`}
                                aria-label="Pas utile"
                                title="Pas utile"
                              >
                                <ThumbsDown className={`h-3.5 w-3.5 ${msg.feedbackPending ? "opacity-50" : ""}`} />
                              </button>
                          </div>
                        )}
                      </button>
                    </div>
                  ),
                )}
                {busy && (
                  <div className="flex items-center gap-3 text-sm text-slate-400">
                    <span className="relative flex h-3 w-3">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-law-cyan opacity-75" />
                      <span className="relative inline-flex h-3 w-3 rounded-full bg-law-cyan" />
                    </span>
                    Traitement en cours par les agents…
                  </div>
                )}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="glass z-10 px-4 py-3 sm:px-6">
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={2}
                placeholder="Votre question juridique… (Entrée pour envoyer, Maj+Entrée pour sauter une ligne)"
                className="flex-1 resize-none rounded-xl border border-slate-700/60 bg-slate-900/60 px-4 py-3 text-sm text-white placeholder:text-slate-500 focus:border-law-cyan/60 focus:bg-slate-900/80 focus:outline-none disabled:opacity-60"
                disabled={busy}
              />
              <button
                type="button"
                onClick={() => void send()}
                disabled={busy || input.trim().length === 0}
                className="btn-primary flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-white disabled:opacity-50 disabled:hover:translate-y-0"
              >
                <Send className="h-5 w-5" />
              </button>
            </div>
            <p className="mx-auto mt-2 max-w-3xl text-center text-[10px] text-slate-500">
              Avertissement : cet outil est une aide à la recherche juridique. Ses réponses ne
              constituent pas un conseil juridique.
            </p>
          </div>
        </main>

        {/* Side panel (desktop + mobile drawer) */}
        <aside
          className={`absolute inset-y-0 right-0 z-30 flex w-[min(86vw,22rem)] flex-col border-l border-slate-700/40 bg-[#0b1120]/95 shadow-2xl backdrop-blur-xl transition-transform duration-300 md:static md:w-80 md:translate-x-0 md:bg-surface/70 md:shadow-none lg:w-96 ${
            panelOpen ? "translate-x-0" : "translate-x-full md:translate-x-0"
          }`}
        >
          <div className="flex items-center justify-between border-b border-slate-700/40 px-4 py-3 md:hidden">
            <span className="text-sm font-medium text-white">Détails de la réponse</span>
            <button
              type="button"
              onClick={() => setPanelOpen(false)}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-white/5 hover:text-white"
            >
              <Menu className="h-5 w-5" />
            </button>
          </div>
          <div className="flex border-b border-slate-700/40 bg-slate-900/30">
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
                    ? "border-b-2 border-law-cyan text-law-cyan"
                    : "text-slate-400 hover:text-slate-200"
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
          {selectedMessage?.response && (
            <div className="border-t border-slate-700/40 p-3">
              <ExportMenu response={selectedMessage.response} query={selectedMessage.text} />
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
