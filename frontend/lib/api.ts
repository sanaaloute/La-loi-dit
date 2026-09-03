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
  law_number?: string | null;
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
  /** Child excerpts grouped under an expanded parent chunk (when present). */
  child_chunks?: EvidenceChunk[];
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
  model?: string | null; // selected model id; omitted = tier default
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

export type Tier = "gratuit" | "pro" | "cabinet";

export interface UserFeatures {
  export: string[];
  drafting: boolean;
  priority?: boolean;
}

export interface UserProfile {
  id: string;
  email: string;
  phone?: string;
  name?: string | null;
  role: string;
  tier: Tier;
  workspace_id?: string | null;
  workspace_name: string;
  features: UserFeatures;
}

export interface ModelInfo {
  id: string;
  provider: string;
  label: string;
  tier_required: Tier;
  allowed: boolean;
}

export interface ModelList {
  default_model: string;
  models: ModelInfo[];
}

export interface ChatSessionSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatSessionList {
  sessions: ChatSessionSummary[];
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  answer: FinalAnswer | null;
  created_at: string;
  /** Per-session position (0, 1, 2, …) — matches a prompt to its answer. */
  index?: number;
}

export interface ChatSessionDetail {
  session_id: string;
  messages: HistoryMessage[];
}

export type DraftFieldType = "text" | "textarea" | "date" | "number" | "select";

export interface DraftField {
  name: string;
  label: string;
  type: DraftFieldType;
  required: boolean;
  placeholder: string;
  options: string[];
}

export type DraftCategory = "contract" | "case";

export interface DraftTemplate {
  id: string;
  category: DraftCategory;
  label: string;
  description: string;
  fields: DraftField[];
}

export interface DraftTemplateList {
  templates: DraftTemplate[];
}

export interface DraftRequest {
  template_id: string;
  fields: Record<string, string>;
  instructions?: string;
  model?: string;
}

export interface DraftResponse {
  title: string;
  template_id: string;
  draft_markdown: string;
  citations: Citation[];
  warnings: string[];
  requires_human_review: boolean;
  latency_ms: number;
}

export interface UsageDay {
  day: string; // YYYY-MM-DD
  tokens_in: number;
  tokens_out: number;
  requests: number;
}

export interface UsageResponse {
  tier: Tier;
  daily_budget: number;
  today: { tokens_in: number; tokens_out: number; requests: number };
  remaining_tokens: number;
  history: UsageDay[]; // last 30 days, most recent first; zero-activity days omitted
}

export interface BillingConfig {
  enabled: boolean;
  provider: "paddle" | null;
}

export interface CheckoutResponse {
  checkout_url: string;
}

export type SubscriptionStatus = "active" | "past_due" | "canceled" | "none";

export interface SubscriptionInfo {
  tier: string;
  status: SubscriptionStatus;
  current_period_end: string | null; // ISO date
  cancel_at_period_end: boolean;
}

export type StreamEvent =
  | { type: "update"; node: string; update: Record<string, unknown> }
  | { type: "node_start"; node: string }
  | { type: "delta"; text: string }
  | { type: "final"; response: ChatResponse }
  | { type: "cancelled" }
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
  { id: "query_router", label: "Analyse de la question" },
  { id: "planner", label: "Planificateur" },
  { id: "context_agent", label: "Agent de contexte" },
  { id: "memory_agent", label: "Agent mémoire" },
  { id: "retrieval_branch", label: "Recherches parallèles" },
  { id: "retrieval_merge", label: "Fusion des preuves" },
  { id: "conflict_resolver", label: "Résolution de conflits" },
  { id: "parent_expansion", label: "Enrichissement des passages" },
  { id: "evidence_ranking", label: "Classement des preuves" },
  { id: "coverage_auditor", label: "Audit de couverture" },
  { id: "reasoning_agent", label: "Raisonnement" },
  { id: "reflection_agent", label: "Réflexion" },
  { id: "response_generator", label: "Génération de la réponse" },
  { id: "claim_verification", label: "Vérification des affirmations" },
  { id: "citation_verification", label: "Vérification des citations" },
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
const MODEL_KEY = "legal_ai_model";

export const AUTH_CHANGE_EVENT = "legal-ai-auth-change";

function parseJwtPayload(token: string): { exp?: number } | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json) as { exp?: number };
  } catch {
    return null;
  }
}

export function isTokenExpired(token: string): boolean {
  const payload = parseJwtPayload(token);
  if (!payload?.exp) return false;
  return payload.exp * 1000 < Date.now();
}

function notifyAuthChange() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(AUTH_CHANGE_EVENT, { detail: { token: getToken() } }),
  );
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  const token = window.localStorage.getItem(TOKEN_KEY);
  if (!token) return null;
  if (isTokenExpired(token)) {
    window.localStorage.removeItem(TOKEN_KEY);
    notifyAuthChange();
    return null;
  }
  return token;
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
  notifyAuthChange();
}

export function clearToken(): void {
  setToken(null);
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

export function getModel(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(MODEL_KEY);
}

export function setModel(model: string | null): void {
  if (typeof window === "undefined") return;
  if (model) window.localStorage.setItem(MODEL_KEY, model);
  else window.localStorage.removeItem(MODEL_KEY);
}

function authHeaders(token?: string | null): Record<string, string> {
  const t = token ?? getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// ---------------------------------------------------------------------------
// Token renewal (sliding session via POST /auth/refresh)
// ---------------------------------------------------------------------------

// Renew the token when it has less than this many seconds left.
const REFRESH_THRESHOLD_S = 5 * 60;

function tokenSecondsLeft(token: string): number | null {
  const payload = parseJwtPayload(token);
  if (!payload?.exp) return null;
  return payload.exp - Date.now() / 1000;
}

let refreshInFlight: Promise<string | null> | null = null;

async function requestRefresh(token: string): Promise<string | null> {
  try {
    const res = await fetch(apiUrl("/auth/refresh"), {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      // 401: the session is over (expired or displaced by another login).
      if (res.status === 401) clearToken();
      return null;
    }
    const data = (await res.json()) as TokenResponse;
    setToken(data.access_token);
    return data.access_token;
  } catch {
    // Network error: keep the current token, it is still valid for a while.
    return null;
  }
}

/**
 * Return a valid token, renewing it through `/auth/refresh` when it is about
 * to expire. Returns null when no token is stored or the session is over
 * (the user must log in again).
 */
export function ensureFreshToken(): Promise<string | null> {
  if (typeof window === "undefined") return Promise.resolve(null);
  const token = window.localStorage.getItem(TOKEN_KEY);
  if (!token) return Promise.resolve(null);
  const secondsLeft = tokenSecondsLeft(token);
  if (secondsLeft === null || secondsLeft > REFRESH_THRESHOLD_S) {
    return Promise.resolve(getToken());
  }
  if (secondsLeft <= 0) {
    clearToken();
    return Promise.resolve(null);
  }
  if (!refreshInFlight) {
    refreshInFlight = requestRefresh(token).finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

interface ApiFetchOptions extends RequestInit {
  token?: string | null;
}

async function apiFetch(path: string, options: ApiFetchOptions = {}): Promise<Response> {
  const { token, ...fetchOptions } = options;
  // Renew a soon-to-expire token before the request so a valid session never
  // dies mid-call (auth endpoints are excluded: they manage tokens directly).
  if (!path.startsWith("/auth/") && (token ?? getToken())) {
    await ensureFreshToken();
  }
  const headers: Record<string, string> = {
    ...(fetchOptions.headers as Record<string, string> ?? {}),
    ...authHeaders(token),
  };
  const res = await fetch(apiUrl(path), {
    ...fetchOptions,
    headers,
  });
  if (res.status === 401) {
    clearToken();
  }
  return res;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export async function login(username: string, password: string): Promise<TokenResponse> {
  const res = await apiFetch("/auth/token", {
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

export async function register(
  identifier: { email?: string; phone?: string },
  password: string,
  name?: string,
): Promise<TokenResponse> {
  const res = await apiFetch("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...(identifier.email ? { email: identifier.email } : {}),
      ...(identifier.phone ? { phone: identifier.phone } : {}),
      password,
      ...(name ? { name } : {}),
    }),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec de l'inscription (${res.status})`);
  }
  return (await res.json()) as TokenResponse;
}

export async function me(token?: string | null): Promise<UserProfile> {
  const res = await apiFetch("/auth/me", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec de la récupération du profil (${res.status})`);
  }
  return (await res.json()) as UserProfile;
}

export async function usageMe(token?: string | null): Promise<UsageResponse> {
  const res = await apiFetch("/usage/me", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement de l'usage (${res.status})`, res.status);
  }
  return (await res.json()) as UsageResponse;
}

export async function billingConfig(): Promise<BillingConfig> {
  const res = await apiFetch("/billing/config");
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement de la configuration de paiement (${res.status})`, res.status);
  }
  return (await res.json()) as BillingConfig;
}

export async function createCheckout(tier: "pro" | "cabinet", token?: string | null): Promise<CheckoutResponse> {
  const res = await apiFetch("/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la création du paiement (${res.status})`, res.status);
  }
  return (await res.json()) as CheckoutResponse;
}

export async function getSubscription(token?: string | null): Promise<SubscriptionInfo> {
  const res = await apiFetch("/billing/subscription", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement de l'abonnement (${res.status})`, res.status);
  }
  return (await res.json()) as SubscriptionInfo;
}

export async function listModels(token?: string | null): Promise<ModelList> {
  const res = await apiFetch("/models", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec du chargement des modèles (${res.status})`);
  }
  return (await res.json()) as ModelList;
}

export async function listSessions(token?: string | null): Promise<ChatSessionList> {
  const res = await apiFetch("/chat/sessions", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec du chargement de l'historique (${res.status})`);
  }
  return (await res.json()) as ChatSessionList;
}

export async function getSession(sessionId: string, token?: string | null): Promise<ChatSessionDetail> {
  const res = await apiFetch(`/chat/sessions/${encodeURIComponent(sessionId)}`, { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec du chargement de la conversation (${res.status})`);
  }
  return (await res.json()) as ChatSessionDetail;
}

export interface RunStatus {
  running: boolean;
  node?: string | null;
}

/** Best-effort status of an in-flight run. Returns null when the status is
 * unreadable (network down): an unreadable status must never count as
 * "not running". */
export async function getRunStatus(
  sessionId: string,
  token?: string | null,
): Promise<RunStatus | null> {
  try {
    const res = await apiFetch(`/chat/sessions/${encodeURIComponent(sessionId)}/run`, { token });
    if (!res.ok) return null;
    return (await res.json()) as RunStatus;
  } catch {
    return null;
  }
}

/** Delete a conversation (204 on success; 404 when not the owner). */
export async function deleteSession(sessionId: string, token?: string | null): Promise<void> {
  const res = await apiFetch(`/chat/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    token,
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la suppression de la conversation (${res.status})`, res.status);
  }
}

export async function listDraftTemplates(token?: string | null): Promise<DraftTemplateList> {
  const res = await apiFetch("/draft/templates", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement des modèles de documents (${res.status})`, res.status);
  }
  return (await res.json()) as DraftTemplateList;
}

export async function createDraft(payload: DraftRequest, token?: string | null): Promise<DraftResponse> {
  const res = await apiFetch("/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la génération du document (${res.status})`, res.status);
  }
  return (await res.json()) as DraftResponse;
}

export async function chat(request: ChatRequest, token?: string | null): Promise<ChatResponse> {
  const res = await apiFetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Erreur du serveur (${res.status})`, res.status);
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
  if (request.model) params.set("model", request.model);
  if (request.scenario_date) params.set("scenario_date", request.scenario_date);

  const res = await apiFetch(`/chat/stream?${params.toString()}`, {
    headers: { Accept: "text/event-stream" },
    token,
    signal,
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Flux indisponible (${res.status})`, res.status);
  }
  if (!res.body) {
    throw new Error("Flux indisponible (pas de corps de réponse)");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawFinal = false;

  // Silence watchdog: the backend emits a heartbeat every ~10 s, so ~15 s
  // with no bytes at all means the connection is dead or the stream is being
  // buffered by an intermediary (mobile carrier proxies commonly buffer
  // text/event-stream — on those paths no frame ever arrives). Fail fast so
  // the caller switches to history polling instead of staring at a dead
  // stream; mobile OS suspend/resume cycles also drop sockets silently.
  const SILENCE_TIMEOUT_MS = 15_000;
  let silenceTimer: ReturnType<typeof setTimeout> | undefined;
  const readChunk = (): Promise<ReadableStreamReadResult<Uint8Array>> =>
    new Promise((resolve, reject) => {
      silenceTimer = setTimeout(() => {
        void reader.cancel().catch(() => {});
        reject(new Error("La connexion au serveur s'est interrompue (aucune donnée reçue)."));
      }, SILENCE_TIMEOUT_MS);
      reader.read().then(resolve, reject);
    });

  try {
    for (;;) {
      const result = await readChunk();
      clearTimeout(silenceTimer);
      silenceTimer = undefined;
      const { done, value } = result;
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
            const event = JSON.parse(payload) as StreamEvent;
            if (event.type === "final" || event.type === "cancelled" || event.type === "error") {
              sawFinal = true;
            }
            onEvent(event);
          } catch (err) {
            // A handler error (e.g. backend "error" frame) must propagate.
            if (err instanceof Error && !(err instanceof SyntaxError)) throw err;
            // Ignore malformed frames; the final event or an error will follow.
          }
        }
      }
    }
  } finally {
    if (silenceTimer) clearTimeout(silenceTimer);
  }

  // A stream that ends with no terminal frame was truncated somewhere between
  // the backend and the browser (proxy timeout, worker restart...). Never
  // fail silently: the caller must show an error instead of just stopping.
  if (!sawFinal) {
    throw new Error(
      "Le flux s'est interrompu avant la réponse finale (proxy ou serveur). Réessayez — ou arrêtez et relancez la question.",
    );
  }
}

export async function submitFeedback(payload: FeedbackPayload, token?: string | null): Promise<void> {
  const res = await apiFetch("/chat/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail ?? `Échec de l'envoi du feedback (${res.status})`);
  }
}

/** Ask the backend to stop an in-flight chat run (UI stop button). */
export async function cancelChat(sessionId: string, token?: string | null): Promise<void> {
  try {
    await apiFetch("/chat/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      token,
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {
    // Best effort: the local AbortController already stops the client side.
  }
}

/**
 * Transcribe a recorded audio blob (voice input for the chat composer).
 * The returned text is meant to be reviewed by the user before sending.
 */
export async function transcribeAudio(blob: Blob, token?: string | null): Promise<{ text: string }> {
  // Give the file a real extension: the backend sniffs the audio format
  // from the filename (MediaRecorder blobs have none).
  const ext = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mp4") ? "m4a" : "webm";
  const form = new FormData();
  form.append("file", blob, `vocal.${ext}`);
  let res: Response;
  try {
    // A misconfigured STT provider can hang — fail visibly instead of
    // leaving the "Transcription en cours…" state forever. Manual fallback:
    // AbortSignal.timeout is missing on older mobile browsers.
    res = await apiFetch("/chat/transcribe", {
      method: "POST",
      body: form,
      token,
      signal: timeoutSignal(120_000),
    });
  } catch (err) {
    if (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError")) {
      throw new Error("La transcription a expiré. Réessayez avec un extrait plus court.");
    }
    console.error("Transcription request failed", err);
    throw new Error("Impossible de joindre le serveur de transcription. Vérifiez votre connexion puis réessayez.");
  }
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `La transcription a échoué (${res.status})`, res.status);
  }
  return (await res.json()) as { text: string };
}

/** AbortSignal.timeout with a fallback for browsers that lack it. */
function timeoutSignal(ms: number): AbortSignal | undefined {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return AbortSignal.timeout(ms);
  }
  if (typeof AbortController === "undefined") return undefined;
  const controller = new AbortController();
  setTimeout(() => controller.abort(new DOMException("Transcription timeout", "TimeoutError")), ms);
  return controller.signal;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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

export interface ExportItem {
  query: string;
  answer: FinalAnswer;
}

export interface ExportRequest {
  query: string;
  answer?: FinalAnswer;
  /** When set, exports these exchanges instead of the single query/answer. */
  items?: ExportItem[];
  session_id: string;
  latency_ms: number;
}

export async function exportAnswer(format: ExportFormat, payload: ExportRequest, token?: string | null): Promise<Blob> {
  const res = await apiFetch(`/export/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Export failed (${res.status})`, res.status);
  }
  return res.blob();
}

export async function exportMarkdown(payload: ExportRequest, token?: string | null): Promise<Blob> {
  const res = await apiFetch("/export/md", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Export failed (${res.status})`, res.status);
  }
  return res.blob();
}

/**
 * Export a generated draft by wrapping it in the FinalAnswer shape the
 * export endpoints expect, then reusing the standard export calls.
 */
export async function exportDraft(
  draft: DraftResponse,
  format: ExportFormat | "md",
  token?: string | null,
): Promise<Blob> {
  const answer: FinalAnswer = {
    answer: draft.draft_markdown,
    citations: draft.citations,
    evidence: [],
    confidence: 1,
    language: "fr",
    warnings: draft.warnings,
    conflicts: [],
    requires_human_review: draft.requires_human_review,
    refused: false,
    metadata: {},
  };
  const payload: ExportRequest = {
    query: draft.title,
    answer,
    session_id: draft.template_id,
    latency_ms: draft.latency_ms,
  };
  return format === "md" ? exportMarkdown(payload, token) : exportAnswer(format, payload, token);
}

// ---------------------------------------------------------------------------
// Corpus browser (backend/api/routers/sources.py + articles.py + search.py)
// ---------------------------------------------------------------------------

export interface SourceListItem {
  document_id: string;
  document_name: string;
  version: number;
  chunk_count: number;
  /** Corpus folder: bf | ohada | uemoa | cima | … ("" when unknown). */
  folder: string;
  status: string;
  authority: string;
  document_type: string;
  law_number: string;
  publication_date: string;
  legal_domains: string[];
}

export interface ArticleIndexEntry {
  article: string;
  section: string;
  page: number | null;
  preview: string;
}

export interface ArticleChunk {
  chunk_id: string;
  document_id: string;
  document_name: string;
  content: string;
  article?: string | null;
  section?: string | null;
  page?: number | null;
  publication_date?: string | null;
  effective_date?: string | null;
  url?: string | null;
  authority?: string | null;
  metadata: Record<string, unknown>;
}

/** GET /articles/{doc}/{article} returns every matching chunk (404 if none). */
export interface ArticleLookupResponse {
  document_id: string;
  article: string;
  count: number;
  chunks: ArticleChunk[];
}

export interface SearchResponse {
  query: string;
  count: number;
  results: EvidenceChunk[];
}

export async function listSources(token?: string | null): Promise<SourceListItem[]> {
  const res = await apiFetch("/sources", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement des sources (${res.status})`, res.status);
  }
  return (await res.json()) as SourceListItem[];
}

export async function listSourceArticles(
  documentId: string,
  token?: string | null,
): Promise<ArticleIndexEntry[]> {
  const res = await apiFetch(`/sources/${encodeURIComponent(documentId)}/articles`, { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement des articles (${res.status})`, res.status);
  }
  return (await res.json()) as ArticleIndexEntry[];
}

export async function getArticle(
  documentId: string,
  article: string,
  token?: string | null,
): Promise<ArticleLookupResponse> {
  const res = await apiFetch(
    `/articles/${encodeURIComponent(documentId)}/${encodeURIComponent(article)}`,
    { token },
  );
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Article introuvable (${res.status})`, res.status);
  }
  return (await res.json()) as ArticleLookupResponse;
}

/** Hybrid corpus search (vector + keyword), evidence chunks ranked. */
export async function searchCorpus(
  q: string,
  topK = 10,
  token?: string | null,
): Promise<SearchResponse> {
  const res = await apiFetch(`/search?q=${encodeURIComponent(q)}&top_k=${topK}`, { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la recherche (${res.status})`, res.status);
  }
  return (await res.json()) as SearchResponse;
}

// ---------------------------------------------------------------------------
// Freshness feed (backend/api/routers/freshness.py)
// ---------------------------------------------------------------------------

export interface FreshnessEvent {
  source_name: string;
  url: string;
  kind: string;
  detected_at: string; // ISO 8601
  detail: string;
  metadata: Record<string, unknown>;
}

export async function listFreshnessEvents(
  limit = 10,
  token?: string | null,
): Promise<FreshnessEvent[]> {
  const res = await apiFetch(`/freshness/events?limit=${limit}`, { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement des nouveautés (${res.status})`, res.status);
  }
  return (await res.json()) as FreshnessEvent[];
}

// ---------------------------------------------------------------------------
// Bookmarks (backend/api/routers/bookmarks.py)
// ---------------------------------------------------------------------------

export interface Bookmark {
  id: string;
  query: string;
  answer: string;
  confidence: number;
  session_id: string;
  created_at: string;
}

export interface BookmarkInput {
  query: string;
  answer: string;
  confidence: number;
  session_id: string;
}

export async function addBookmark(payload: BookmarkInput, token?: string | null): Promise<Bookmark> {
  const res = await apiFetch("/bookmarks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de l'enregistrement du marque-page (${res.status})`, res.status);
  }
  return (await res.json()) as Bookmark;
}

/** Newest-first bookmarks of the current user. */
export async function listBookmarks(token?: string | null): Promise<Bookmark[]> {
  const res = await apiFetch("/bookmarks", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement des marque-pages (${res.status})`, res.status);
  }
  return (await res.json()) as Bookmark[];
}

/** 204 on success; 404 when the bookmark is foreign or unknown. */
export async function deleteBookmark(id: string, token?: string | null): Promise<void> {
  const res = await apiFetch(`/bookmarks/${encodeURIComponent(id)}`, {
    method: "DELETE",
    token,
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la suppression du marque-page (${res.status})`, res.status);
  }
}

// ---------------------------------------------------------------------------
// Public answer sharing (backend/api/routers/share.py)
// ---------------------------------------------------------------------------

export interface ShareInput {
  query: string;
  answer: string;
  citations: Citation[];
  confidence: number;
}

export interface ShareResponse {
  token: string;
  /** "/partage/<token>" — prepend window.location.origin for the public URL. */
  url_path: string;
}

export interface SharedAnswer {
  query: string;
  answer: string;
  citations: Citation[];
  confidence: number;
  created_at: string;
}

export async function createShare(payload: ShareInput, token?: string | null): Promise<ShareResponse> {
  const res = await apiFetch("/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la création du lien de partage (${res.status})`, res.status);
  }
  return (await res.json()) as ShareResponse;
}

/** PUBLIC endpoint (no auth required): read a shared answer snapshot. */
export async function getSharedAnswer(shareToken: string): Promise<SharedAnswer> {
  const res = await apiFetch(`/share/${encodeURIComponent(shareToken)}`);
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Lien de partage invalide ou expiré (${res.status})`, res.status);
  }
  return (await res.json()) as SharedAnswer;
}

// ---------------------------------------------------------------------------
// Preferences & memories (backend/api/routers/auth.py)
// ---------------------------------------------------------------------------

export interface PreferencesResponse {
  preferences: Record<string, unknown>;
}

export async function getPreferences(token?: string | null): Promise<PreferencesResponse> {
  const res = await apiFetch("/auth/me/preferences", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement des préférences (${res.status})`, res.status);
  }
  return (await res.json()) as PreferencesResponse;
}

/** The backend merges `preferences` into the stored set and returns the result. */
export async function putPreferences(
  preferences: Record<string, unknown>,
  token?: string | null,
): Promise<PreferencesResponse> {
  const res = await apiFetch("/auth/me/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    token,
    body: JSON.stringify({ preferences }),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de l'enregistrement des préférences (${res.status})`, res.status);
  }
  return (await res.json()) as PreferencesResponse;
}

export interface MemoryEntry {
  id: string;
  kind: string;
  content: string;
  created_at: string;
  last_accessed: string;
}

export interface MemoriesResponse {
  memories: MemoryEntry[];
}

export async function listMemories(token?: string | null): Promise<MemoriesResponse> {
  const res = await apiFetch("/auth/me/memories", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec du chargement de la mémoire (${res.status})`, res.status);
  }
  return (await res.json()) as MemoriesResponse;
}

/** Erase everything the assistant remembers about the current user (204). */
export async function eraseMemories(token?: string | null): Promise<void> {
  const res = await apiFetch("/auth/me/memories", { method: "DELETE", token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de l'effacement de la mémoire (${res.status})`, res.status);
  }
}

/** Revoke the server-side session (204); callers must also discard the token. */
export async function logout(token?: string | null): Promise<void> {
  const res = await apiFetch("/auth/logout", { method: "POST", token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la déconnexion (${res.status})`, res.status);
  }
}

/** Permanently delete the caller's account and associated data (204). */
export async function deleteAccount(token?: string | null): Promise<void> {
  const res = await apiFetch("/auth/me", { method: "DELETE", token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la suppression du compte (${res.status})`, res.status);
  }
}

// ---------------------------------------------------------------------------
// Admin dashboard (backend/api/routers/admin.py — ADMIN role required)
// ---------------------------------------------------------------------------

/** Readiness probe: lives at the server ROOT ("/ready"), not under "/api/v1". */
export interface ReadyResponse {
  status: string; // "ready" | "degraded" | "not_ready"
  checks: Record<string, unknown>;
}

export type AdminRole = "viewer" | "user" | "legal_expert" | "admin";

export interface AdminUserEntry {
  id: string;
  email: string;
  name: string;
  role: string;
  tier: string;
  created_at: string;
  today_tokens_in: number;
  today_tokens_out: number;
  today_requests: number;
}

export interface AdminUsersResponse {
  users: AdminUserEntry[];
}

export interface AdminUserPatch {
  tier?: Tier;
  role?: AdminRole;
}

export interface AdminUsageRow {
  user_id: string;
  email: string;
  tokens_in: number;
  tokens_out: number;
  requests: number;
}

export interface AdminUsageResponse {
  per_user: AdminUsageRow[];
  totals: Record<string, number>;
}

export interface TierBudgets {
  daily_token_budget: number;
  daily_request_budget: number;
}

export interface TierBudgetsResponse {
  effective: Record<Tier, TierBudgets>;
  defaults: Record<Tier, TierBudgets>;
}

/** PATCH body: per tier, only the fields to change. */
export type TierBudgetsPatch = Partial<Record<Tier, Partial<TierBudgets>>>;

export interface ProviderModelInfo {
  id: string;
  label: string;
  tier_required: Tier; // lowest tier unlocking this model
}

export interface ProviderInfo {
  provider: string;
  configured: boolean;
  api_base: string; // per-provider base URL
  key_suffix: string | null; // "…" + last 4 chars, never the full key
  model: string;
  models: ProviderModelInfo[]; // this provider's catalog models
}

export interface ProvidersResponse {
  providers: ProviderInfo[];
  defaults: Record<string, string>; // tier -> default catalog model id
  infra: Record<string, unknown>;
}

export type PromptSource = "search" | "chat" | "chat_stream" | "ws_chat";

export interface UserPromptRecord {
  id: number;
  user_id: string;
  email: string;
  prompt: string;
  source: PromptSource;
  session_id: string;
  created_at: string; // ISO 8601
  metadata: Record<string, unknown>;
}

export interface UserPromptsResponse {
  prompts: UserPromptRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserPromptsQuery {
  q?: string;
  source?: PromptSource;
  user_id?: string;
  from?: string; // YYYY-MM-DD
  to?: string; // YYYY-MM-DD
  page?: number;
  page_size?: number;
}

export interface FolderInfo {
  name: string;
  files: number;
}

export interface FoldersResponse {
  folders: FolderInfo[];
}

export interface FolderCreateResponse {
  name: string; // domain slug actually created
  created: boolean;
}

/** One legal-domain taxonomy entry (slug, French label, keyword stems). */
export interface DomainInfo {
  slug: string;
  label: string;
  keywords: string[];
}

export interface DomainsResponse {
  domains: DomainInfo[];
}

export interface MetadataSuggestion {
  document_name: string;
  authority: string;
  document_type: string;
  law_number: string;
  legal_domains: string[];
  publication_date: string;
  effective_date: string;
  government_body: string;
  url: string;
}

export interface MetadataSuggestionResponse {
  suggestion: MetadataSuggestion;
  available_domains: string[];
  domain_labels: Record<string, string>; // slug -> French display label
}

/** Editable ingestion metadata sent with the upload (empty fields omitted). */
export type DocumentMetadata = Record<string, string | string[]>;

export interface IngestionDocumentStatus {
  document_id: string;
  /** Display name from the latest ingestion record ("" when unknown). */
  document_name?: string;
  version: number;
  content_hash: string;
  article_count: number;
  /** Real chunk count in the vector store; null when the store is unavailable. */
  chunk_count?: number | null;
  /** Latest ingestion outcome ("ingested", "failed", "skipped_duplicate", ...). */
  last_status?: string;
  last_error?: string;
}

export interface IngestionStatusResponse {
  documents: IngestionDocumentStatus[];
  total_documents: number;
  store_updated_at?: string | null;
  failed_documents: Record<string, unknown>[];
  note?: string;
}

export interface EvaluationLatestResponse {
  path: string;
  generated_at?: string | null;
  dataset?: string | null;
  total_cases?: number | null;
  pass_rate?: number | null;
  report: Record<string, unknown>;
}

export interface EndpointStats {
  path: string;
  requests: number;
  errors: number;
  avg_latency_ms: number;
}

export interface UserRequestStats {
  user: string;
  requests: number;
}

export interface RetrievalAnalyticsResponse {
  total_requests: number;
  by_path: EndpointStats[];
  by_user: UserRequestStats[];
  note?: string;
}

export interface DocumentIngestResult {
  document_id: string;
  document_name: string;
  chunks_created: number;
  version: number;
  status: "indexed" | "failed" | "skipped_duplicate" | "deleted";
  detail: string;
}

export async function ready(): Promise<ReadyResponse> {
  // Root-level probe: apiBase() already points at the server root (either the
  // direct API URL or the "/backend-api" rewrite, both without "/api/v1").
  const res = await fetch(`${apiBase()}/ready`);
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // not JSON
  }
  // /ready answers 200 with {status, checks} even when degraded (503 only in
  // strict infra mode, with the same body), so accept any parseable payload.
  if (body && typeof body === "object" && "checks" in body) {
    return body as ReadyResponse;
  }
  throw new ApiError(`Sonde de disponibilité indisponible (${res.status})`, res.status);
}

async function adminRequest<T>(
  path: string,
  init?: { method?: string; json?: unknown; form?: FormData },
): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  } else if (init?.form) {
    // Let the browser set the multipart boundary.
    body = init.form;
  }
  const res = await apiFetch(`/admin${path}`, {
    method: init?.method,
    headers,
    body,
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Requête admin échouée (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

export const adminApi = {
  users: () => adminRequest<AdminUsersResponse>("/users"),
  patchUser: (id: string, body: AdminUserPatch) =>
    adminRequest<AdminUserEntry>(`/users/${encodeURIComponent(id)}`, {
      method: "PATCH",
      json: body,
    }),
  usage: (days = 30) => adminRequest<AdminUsageResponse>(`/usage?days=${days}`),
  tierBudgets: () => adminRequest<TierBudgetsResponse>("/settings/tier-budgets"),
  patchTierBudgets: (body: TierBudgetsPatch) =>
    adminRequest<TierBudgetsResponse>("/settings/tier-budgets", {
      method: "PATCH",
      json: body,
    }),
  providers: () => adminRequest<ProvidersResponse>("/providers"),
  folders: () => adminRequest<FoldersResponse>("/documents/folders"),
  createFolder: (name: string) =>
    adminRequest<FolderCreateResponse>("/documents/folders", { method: "POST", json: { name } }),
  getDomains: () => adminRequest<DomainsResponse>("/domains"),
  createDomain: (body: { slug: string; label: string; keywords: string[] }) =>
    adminRequest<DomainInfo>("/domains", { method: "POST", json: body }),
  deleteDomain: (slug: string) =>
    adminRequest<DomainsResponse>(`/domains/${encodeURIComponent(slug)}`, { method: "DELETE" }),
  suggestMetadata: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return adminRequest<MetadataSuggestionResponse>("/documents/metadata-suggestion", {
      method: "POST",
      form,
    });
  },
  uploadDocument: (file: File, folder: string, metadata: DocumentMetadata) => {
    const form = new FormData();
    form.append("file", file);
    form.append("folder", folder);
    form.append("metadata", Object.keys(metadata).length > 0 ? JSON.stringify(metadata) : "");
    return adminRequest<DocumentIngestResult>("/documents/upload", { method: "POST", form });
  },
  deleteDocument: (id: string) =>
    adminRequest<DocumentIngestResult>(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  ingestionStatus: () => adminRequest<IngestionStatusResponse>("/ingestion/status"),
  retrievalAnalytics: () => adminRequest<RetrievalAnalyticsResponse>("/retrieval/analytics"),
  evaluationLatest: () => adminRequest<EvaluationLatestResponse>("/evaluation/latest"),
  prompts: (query: UserPromptsQuery = {}) => {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.source) params.set("source", query.source);
    if (query.user_id) params.set("user_id", query.user_id);
    if (query.from) params.set("from", query.from);
    if (query.to) params.set("to", query.to);
    if (query.page !== undefined) params.set("page", String(query.page));
    if (query.page_size !== undefined) params.set("page_size", String(query.page_size));
    const qs = params.toString();
    return adminRequest<UserPromptsResponse>(`/prompts${qs ? `?${qs}` : ""}`);
  },
};

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
