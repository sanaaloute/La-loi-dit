// API client for the Yawoto backend — port of frontend/lib/api.ts.
// All endpoints live under `{apiUrl}/api/v1`; errors are `{"detail": "..."}`.
// Every request carries `X-Device-Id` (the backend binds mobile sessions to
// the device instead of the client IP).

import Constants from "expo-constants";
import { getDeviceId } from "./device";
import {
  clearToken,
  getToken,
  getTokenUnchecked,
  setToken,
  tokenSecondsLeft,
} from "./storage";

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

export interface RunStatus {
  running: boolean;
  node?: string | null;
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

/** Friendly one-line descriptions of each pipeline step (from ChatWindow). */
export const STEP_LABELS: Record<string, string> = {
  input_guardrail: "Vérification des règles de sécurité…",
  refusal: "Vérification des limites…",
  query_router: "Analyse de votre requête…",
  planner: "Planification de la recherche…",
  context_agent: "Analyse du contexte…",
  memory_agent: "Consultation de la mémoire…",
  retrieval_branch: "Recherche documentaire…",
  retrieval_merge: "Fusion des résultats…",
  conflict_resolver: "Résolution des conflits…",
  parent_expansion: "Enrichissement des passages…",
  evidence_ranking: "Classement des preuves…",
  coverage_auditor: "Vérification de la couverture…",
  reasoning_agent: "Raisonnement juridique…",
  reflection_agent: "Vérification interne…",
  response_generator: "Rédaction de la réponse…",
  claim_verification: "Vérification des affirmations…",
  citation_verification: "Vérification des citations…",
  output_guardrail: "Contrôle final…",
};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DEFAULT_API_URL = "https://api-yawoto.neobytech.net";

/**
 * API origin. `EXPO_PUBLIC_API_URL` (dev override, e.g. http://localhost:8000)
 * wins over the `extra.apiUrl` bundled in app.json.
 */
export function apiBase(): string {
  const env = (process.env as Record<string, string | undefined>).EXPO_PUBLIC_API_URL;
  if (env && env.trim().length > 0) {
    return env.replace(/\/+$/, "");
  }
  const extra = Constants.expoConfig?.extra as { apiUrl?: string } | undefined;
  if (extra?.apiUrl && extra.apiUrl.trim().length > 0) {
    return extra.apiUrl.replace(/\/+$/, "");
  }
  return DEFAULT_API_URL;
}

export function apiUrl(path: string): string {
  return `${apiBase()}/api/v1${path}`;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function safeDetail(res: Response): Promise<string | null> {
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
// Token renewal (sliding session via POST /auth/refresh)
// ---------------------------------------------------------------------------

// Renew the token when it has less than this many seconds left.
const REFRESH_THRESHOLD_S = 5 * 60;

let refreshInFlight: Promise<string | null> | null = null;

async function requestRefresh(token: string): Promise<string | null> {
  try {
    const res = await apiFetch("/auth/refresh", {
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
 * (the user must log in again). Single in-flight promise, like the web app.
 */
export function ensureFreshToken(): Promise<string | null> {
  const token = getTokenUnchecked();
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

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

interface ApiFetchOptions extends RequestInit {
  token?: string | null;
}

async function baseHeaders(token?: string | null): Promise<Record<string, string>> {
  const t = token ?? getToken();
  const headers: Record<string, string> = {
    "X-Device-Id": await getDeviceId(),
  };
  if (t) headers.Authorization = `Bearer ${t}`;
  return headers;
}

export async function apiFetch(path: string, options: ApiFetchOptions = {}): Promise<Response> {
  const { token, ...fetchOptions } = options;
  // Renew a soon-to-expire token before the request so a valid session never
  // dies mid-call (auth endpoints are excluded: they manage tokens directly).
  if (!path.startsWith("/auth/") && (token ?? getToken())) {
    await ensureFreshToken();
  }
  const headers: Record<string, string> = {
    ...(await baseHeaders(token)),
    ...((fetchOptions.headers as Record<string, string> | undefined) ?? {}),
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
// Auth
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

/** End the server-side session (best effort — local state is cleared anyway). */
export async function logout(): Promise<void> {
  try {
    await apiFetch("/auth/logout", { method: "POST" });
  } catch {
    // Best effort.
  }
}

/**
 * Delete the account permanently. 204 on success; 403 when the user is the
 * last administrator of the workspace.
 */
export async function deleteAccount(): Promise<void> {
  const res = await apiFetch("/auth/me", { method: "DELETE" });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la suppression du compte (${res.status})`, res.status);
  }
}

/** Always 202 — never reveals whether the identifier exists. */
export async function requestPasswordReset(identifier: string): Promise<void> {
  const res = await apiFetch("/auth/password-reset/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier }),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de la demande (${res.status})`, res.status);
  }
}

export async function confirmPasswordReset(token: string, newPassword: string): Promise<void> {
  const res = await apiFetch("/auth/password-reset/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(
      detail ?? "Lien de réinitialisation invalide ou expiré.",
      res.status,
    );
  }
}

// ---------------------------------------------------------------------------
// Account / billing / models
// ---------------------------------------------------------------------------

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
    throw new ApiError(
      detail ?? `Échec du chargement de la configuration de paiement (${res.status})`,
      res.status,
    );
  }
  return (await res.json()) as BillingConfig;
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

// ---------------------------------------------------------------------------
// Chat sessions
// ---------------------------------------------------------------------------

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
    throw new ApiError(
      detail ?? `Échec de la suppression de la conversation (${res.status})`,
      res.status,
    );
  }
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Voice transcription
// ---------------------------------------------------------------------------

export interface AudioFile {
  uri: string;
  /** File extension without the dot (m4a, mp4, webm, ogg…). */
  ext: string;
  mimeType: string;
}

/**
 * Transcribe a recorded audio file (voice input for the chat composer).
 * The returned text is meant to be reviewed by the user before sending.
 */
export async function transcribeAudio(
  file: AudioFile,
  token?: string | null,
): Promise<{ text: string }> {
  const form = new FormData();
  // React Native FormData accepts a {uri, name, type} file descriptor; the
  // backend sniffs the audio format from the filename extension.
  form.append("file", {
    uri: file.uri,
    name: `vocal.${file.ext}`,
    type: file.mimeType,
  } as unknown as Blob);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000);
  let res: Response;
  try {
    res = await apiFetch("/chat/transcribe", {
      method: "POST",
      body: form,
      token,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error("La transcription a expiré. Réessayez avec un extrait plus court.");
    }
    throw new Error(
      "Impossible de joindre le serveur de transcription. Vérifiez votre connexion puis réessayez.",
    );
  } finally {
    clearTimeout(timeout);
  }
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `La transcription a échoué (${res.status})`, res.status);
  }
  return (await res.json()) as { text: string };
}

// ---------------------------------------------------------------------------
// Drafting
// ---------------------------------------------------------------------------

export async function listDraftTemplates(token?: string | null): Promise<DraftTemplateList> {
  const res = await apiFetch("/draft/templates", { token });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(
      detail ?? `Échec du chargement des modèles de documents (${res.status})`,
      res.status,
    );
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

// ---------------------------------------------------------------------------
// Export (binary payloads)
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

async function exportRequest(path: string, payload: ExportRequest): Promise<ArrayBuffer> {
  const res = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new ApiError(detail ?? `Échec de l'export (${res.status})`, res.status);
  }
  return res.arrayBuffer();
}

export function exportAnswer(format: ExportFormat, payload: ExportRequest): Promise<ArrayBuffer> {
  return exportRequest(`/export/${format}`, payload);
}

export function exportMarkdown(payload: ExportRequest): Promise<ArrayBuffer> {
  return exportRequest("/export/md", payload);
}

/**
 * Export a generated draft by wrapping it in the FinalAnswer shape the
 * export endpoints expect, then reusing the standard export calls.
 */
export function exportDraft(
  draft: DraftResponse,
  format: ExportFormat | "md",
): Promise<ArrayBuffer> {
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
  return format === "md" ? exportMarkdown(payload) : exportAnswer(format, payload);
}
