const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");

  const upstream = await fetch(`${API_PROXY_TARGET}/api/v1/chat/stream${url.search}`, {
    method: "GET",
    headers,
    cache: "no-store",
    signal: request.signal,
  });

  const responseHeaders = new Headers(upstream.headers);
  for (const header of ["connection", "content-encoding", "content-length", "transfer-encoding"]) {
    responseHeaders.delete(header);
  }
  responseHeaders.set("Cache-Control", "no-cache");
  responseHeaders.set("X-Accel-Buffering", "no");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
