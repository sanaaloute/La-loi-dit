const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The generic /backend-api rewrite proxy drops requests that take longer than
// ~30s; corpus search can legitimately be slow while the local embedding
// backend is busy (ingestion), so this route streams the response through
// without the proxy timeout. Same pattern as the chat stream route.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");

  const upstream = await fetch(`${API_PROXY_TARGET}/api/v1/search${url.search}`, {
    method: "GET",
    headers,
    cache: "no-store",
    signal: request.signal,
  });

  const responseHeaders = new Headers(upstream.headers);
  for (const header of ["connection", "content-encoding", "content-length", "transfer-encoding"]) {
    responseHeaders.delete(header);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
