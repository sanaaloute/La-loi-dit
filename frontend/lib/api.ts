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
  { id: "planner", label: "Planificateur" },
  { id: "context_agent", label: "Agent de contexte" },
  { id: "memory_agent", label: "Agent mémoire" },
  { id: "retrieval_branch", label: "Recherches parallèles" },
  { id: "retrieval_merge", label: "Fusion des preuves" },
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

interface ApiFetchOptions extends RequestInit {
  token?: string | null;
}

async function apiFetch(path: string, options: ApiFetchOptions = {}): Promise<Response> {
  const { token, ...fetchOptions } = options;
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

export async function register(email: string, password: string, name?: string): Promise<TokenResponse> {
  const res = await apiFetch("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, ...(name ? { name } : {}) }),
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
}

/** Editable ingestion metadata sent with the upload (empty fields omitted). */
export type DocumentMetadata = Record<string, string | string[]>;

export interface IngestionDocumentStatus {
  document_id: string;
  version: number;
  content_hash: string;
  article_count: number;
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
  providers: () => adminRequest<ProvidersResponse>("/providers"),
  folders: () => adminRequest<FoldersResponse>("/documents/folders"),
  createFolder: (name: string) =>
    adminRequest<FolderCreateResponse>("/documents/folders", { method: "POST", json: { name } }),
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
