// SSE transport for GET /chat/stream.
//
// EventSource cannot send Authorization headers and React Native's built-in
// fetch does not expose streaming response bodies, so this uses expo/fetch —
// the WinterCG-compliant implementation bundled with the Expo runtime, whose
// `Response.body` is a real ReadableStream<Uint8Array> on iOS and Android.
// Frame protocol: `data: {json}\n\n`; heartbeat frames are SSE comments
// (`: hb`) and are skipped by the parser (only `data:` lines are read).

import { fetch as expoFetch } from "expo/fetch";
import {
  ApiError,
  apiUrl,
  ensureFreshToken,
  safeDetail,
  type ChatRequest,
  type StreamEvent,
} from "./api";
import { getDeviceId } from "./device";
import { getToken } from "./storage";

// The backend emits a heartbeat every ~10 s, so ~15 s with no bytes at all
// means the connection is dead or the stream is being buffered by an
// intermediary (mobile carrier proxies commonly buffer text/event-stream).
const SILENCE_TIMEOUT_MS = 15_000;

/**
 * Stream a chat query over SSE. Calls `onEvent` for each parsed `data:`
 * frame. Throws on HTTP-level failures, on silence timeout and when the
 * stream ends without a terminal frame, so the caller can fall back to
 * history polling or POST /chat.
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

  // Renew a soon-to-expire token before opening the stream, like apiFetch.
  if (token ?? getToken()) {
    await ensureFreshToken();
  }
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "X-Device-Id": await getDeviceId(),
  };
  const t = token ?? getToken();
  if (t) headers.Authorization = `Bearer ${t}`;

  const res = await expoFetch(apiUrl(`/chat/stream?${params.toString()}`), {
    headers,
    signal,
  });
  if (!res.ok) {
    const detail = await safeDetail(res as unknown as Response);
    throw new ApiError(detail ?? `Flux indisponible (${res.status})`, res.status);
  }
  if (!res.body) {
    throw new Error("Flux indisponible (pas de corps de réponse)");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawFinal = false;

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
  // the backend and the device (proxy timeout, worker restart...). Never
  // fail silently: the caller must recover or show an error.
  if (!sawFinal) {
    throw new Error(
      "Le flux s'est interrompu avant la réponse finale (proxy ou serveur). Réessayez — ou arrêtez et relancez la question.",
    );
  }
}
