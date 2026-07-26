// API client for the Burkina Faso Legal AI backend.
// TypeScript interfaces mirror backend/core/models.py exactly.

// ---------------------------------------------------------------------------
// Types (mirror of backend/core/models.py)
// ---------------------------------------------------------------------------

export type AuthorityLevel =
  | "constitution"
  | "treaty_ohada"
  | "law"
  | "amended_law"
  | "decree"
  | "order"
  | "ministerial_circular"
  | "official_gazette"
  | "case_law"
  | "official_press_release"
  | "official_news"
  | "uploaded_document"
  | "trusted_legal_site"
  | "news"
  | "blog"
  | "unknown";

export interface Citation {
  label: string;
  chunk_id?: string | null;
  document_name: string;
  article?: string | null;
  url?: string | null;
  verified: boolean;
}

export interface EvidenceChunk {
  chunk_id: string;
  document_id?: string;
  document_name: string;
  content: string;
  article?: string | null;
  section?: string | null;
  page?: number | null;
  publication_date?: string | null;
  effective_date?: string | null;
  government_body?: string | null;
  url?: string | null;
  source_kind: string;
  authority: AuthorityLevel | string;
  language?: string;
  parent_chunk_id?: string | null;
  version?: number;
  confidence: number;
  retrieval_score: number;
  rerank_score: number;
  metadata: Record<string, unknown>;
}

export interface ConflictReport {
  topic: string;
  kept_chunk_id: string;
  dropped_chunk_id: string;
  reason: string;
  resolved: boolean;
}

export interface FinalAnswer {
  answer: string;
  citations: Citation[];
  evidence: EvidenceChunk[];
  confidence: number;
  language: string;
  warnings: string[];
  conflicts: ConflictReport[];
  requires_human_review: boolean;
  refused: boolean;
  refusal_reason?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ChatRequest {
  query: string;
  session_id?: string | null;
  user_id?: string | null;
  language?: string | null;
  scenario_date?: string | null; // YYYY-MM-DD
}

export interface ChatResponse {
  session_id: string;
  answer: FinalAnswer;
  trace: string[];
  latency_ms: number;
  trace_id: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: string;
}

export type StreamEvent =
  | { type: "update"; node: string; update: Record<string, unknown> }
  | { type: "final"; response: ChatResponse }
  | { type: "error"; detail: string };

export interface FeedbackPayload {
  trace_id: string;
  score: "thumbs-up" | "thumbs-down";
  comment?: string;
}

// ---------------------------------------------------------------------------
// Pipeline definition (fixed order, French labels)
// ---------------------------------------------------------------------------

export interface PipelineNode {
  id: string;
  label: string;
}

export const PIPELINE_NODES: PipelineNode[] = [
  { id: "input_guardrail", label: "Garde-fou d'entrée" },
  { id: "refusal", label: "Refus" },
  { id: "planner", label: "Planificateur" },
  { id: "context_agent", label: "Agent de contexte" },
  { id: "memory_agent", label: "Agent mémoire" },
  { id: "retrieval_coordinator", label: "Recherche" },
  { id: "conflict_resolver", label: "Résolution de conflits" },
  { id: "evidence_ranking", label: "Classement des preuves" },
  { id: "reasoning_agent", label: "Raisonnement" },
  { id: "reflection_agent", label: "Réflexion" },
  { id: "citation_verification", label: "Vérification des citations" },
  { id: "response_generator", label: "Génération de la réponse" },
  { id: "output_guardrail", label: "Garde-fou de sortie" },
];

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// When NEXT_PUBLIC_API_URL is set, the browser calls the API directly.
// Otherwise it uses the same-origin "/backend-api" path, which Next.js
// rewrites to the API server-side (the backend does not enable CORS).
const PROXY_BASE = "/backend-api";

export function apiBase(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_URL;
  if (fromEnv && fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/+$/, "");
  }
  return PROXY_BASE;
}

function apiUrl(path: string): string {
  return `${apiBase()}/api/v1${path}`;
}

// ---------------------------------------------------------------------------
// Auth token storage (localStorage)
// ---------------------------------------------------------------------------

const TOKEN_KEY = "legal_ai_token";
const SESSION_KEY = "legal_ai_session_id";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export function getSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(SESSION_KEY);
}

export function setSessionId(sessionId: string | null): void {
  if (typeof window === "undefined") return;
  if (sessionId) window.localStorage.setItem(SESSION_KEY, sessionId);
  else window.localStorage.removeItem(SESSION_KEY);
}

function authHeaders(token?: string | null): Record<string, string> {
  const t = token ?? getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export async function login(username: string, password: string): Promise<TokenResponse> {
  const res = await fetch(apiUrl("/auth/token"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec de connexion (${res.status})`);
  }
  return (await res.json()) as TokenResponse;
}

export async function chat(request: ChatRequest, token?: string | null): Promise<ChatResponse> {
  const res = await fetch(apiUrl("/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Erreur du serveur (${res.status})`);
  }
  return (await res.json()) as ChatResponse;
}

/**
 * Stream a chat query over SSE using fetch + ReadableStream (EventSource
 * cannot send Authorization headers). Calls `onEvent` for each parsed
 * `data:` frame. Throws on HTTP-level failures so the caller can fall back
 * to POST /chat.
 */
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  token?: string | null,
  signal?: AbortSignal,
): Promise<void> {
  const params = new URLSearchParams({ query: request.query });
  if (request.session_id) params.set("session_id", request.session_id);
  if (request.language) params.set("language", request.language);

  const res = await fetch(`${apiUrl("/chat/stream")}?${params.toString()}`, {
    headers: { Accept: "text/event-stream", ...authHeaders(token) },
    signal,
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Flux indisponible (${res.status})`);
  }
  if (!res.body) {
    throw new Error("Flux indisponible (pas de corps de réponse)");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let sepIndex: number;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload) as StreamEvent);
        } catch {
          // Ignore malformed frames; the final event or an error will follow.
        }
      }
    }
  }
}

export async function submitFeedback(payload: FeedbackPayload, token?: string | null): Promise<void> {
  const res = await fetch(apiUrl("/chat/feedback"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec de l'envoi du feedback (${res.status})`);
  }
}

async function safeDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail != null) return JSON.stringify(body.detail);
  } catch {
    // not JSON
  }
  return null;
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export type ExportFormat = "pdf" | "word" | "csv";

export interface ExportRequest {
  query: string;
  answer: FinalAnswer;
  session_id: string;
  latency_ms: number;
}

export async function exportAnswer(format: ExportFormat, payload: ExportRequest, token?: string | null): Promise<Blob> {
  const res = await fetch(apiUrl(`/export/${format}`), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Export failed (${res.status})`);
  }
  return res.blob();
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
