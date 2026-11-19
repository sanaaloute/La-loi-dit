# Client guide

This guide is for applications calling the Afrikia local LLM provider.

## Base URL

Public Cloudflare URL:

```text
https://afrikia.neobytech.net/v1
```

Local development URL:

```text
http://127.0.0.1:4010/v1
```

Use the same endpoints and bearer tokens on either base URL.

## Authentication

Every request requires a bearer token:

```http
Authorization: Bearer YOUR_APP_KEY
Content-Type: application/json
```

Requests without a token or with an invalid token return HTTP 401 or 403.

Do not put the token in client-side browser code, public repositories, logs or URLs.

## Available models

Chat models (call these with `POST /v1/chat/completions`):

- `gemma4-e4b` — classification / routing between agents
- `qwen2.5-7b` — planning / plan generation
- `nemotron-3-nano` — analysis & synthesis; grounds answers with citations and abstains when unsure
- `nemotron-3.5-bolt-e4b` — general-purpose chat, reasoning and tool use; loads on first request
- `qwen3-vl-30b` — multimodal chat for text and image input; loads on first request and uses its projector

Embedding models:

- `qwen3-embedding-0.6b` — 1024-dimensional embeddings; fast and light
- `qwen3-embedding-8b` — 4096-dimensional embeddings; best retrieval quality for RAG

Decision model (call this with `POST /v1/systemone`, not chat completions):

- `nimble-9b` — structured decisions through Ollama's systemone API; available in the model catalog, decision-only

The three existing chat models and `qwen3-embedding-8b` are loaded at startup. The two added chat models and `qwen3-embedding-0.6b` load automatically on first request, so the first call includes model-load latency. The chat router keeps at most three models loaded; an on-demand model may evict an idle chat model. Nimble loads through Ollama on first decision and shares the Mac's unified memory; its Ollama `keep_alive` setting can be set to `0` to unload it after a request.

Nimble scored 15/15 on a small synthetic decision smoke set, with a 0.68 s warm median and 7.98 s first request including model load. This is not a measure of real-world legal or financial reliability.

Note: `nemotron-3-nano` and `gemma4-e4b` are reasoning models — give them enough `max_tokens` (e.g. 1500) so the final answer fits after the reasoning. See Reasoning (thinking) below for how to turn reasoning off per request.

List the models available to your key:

```bash
curl http://127.0.0.1:4010/v1/models \
  -H "Authorization: Bearer $APP_KEY"
```

## Chat completions

Endpoint:

```http
POST /v1/chat/completions
```

Minimal request:

```bash
curl http://127.0.0.1:4010/v1/chat/completions \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemotron-3-nano",
    "messages": [
      {"role": "user", "content": "Bonjour"}
    ]
  }'
```

Common parameters:

| Parameter | Type | Purpose |
|---|---|---|
| `model` | string | Required model ID |
| `messages` | array | Required conversation messages |
| `temperature` | number | Controls randomness; use 0 for deterministic tasks |
| `top_p` | number | Nucleus sampling parameter |
| `max_tokens` | number | Maximum generated tokens |
| `stream` | boolean | Stream response chunks with server-sent events |
| `tools` | array | Available function/tool schemas |
| `tool_choice` | string/object | Tool selection mode, usually `auto` |
| `response_format` | object | Request a structured response when supported |
| `stop` | string/array | Stop sequences |
| `presence_penalty` | number | Presence penalty |
| `frequency_penalty` | number | Frequency penalty |
| `user` | string | Optional end-user identifier for tracking |
| `reasoning_effort` | string | `"none"` disables reasoning on reasoning models — see Reasoning (thinking) below |

There is no fixed cap on input or output tokens: `max_tokens` is set by the client, and the prompt + completion together must fit within the context window described under Notes below.

## Reasoning (thinking)

`gemma4-e4b` and `nemotron-3-nano` reason before answering; `qwen2.5-7b` does not. Reasoning is **on by default** and comes back in `reasoning_content` — the final answer is always in `content`, so read `content`.

Reasoning is capped server-side at 1024 tokens: if a model thinks longer, the reasoning is cut off and the final answer still follows in `content`. Keep `max_tokens` comfortably above that cap (e.g. 1500+) so the answer always fits.

Turn reasoning off per request when you want a faster, cheaper, answer-only response — either form works:

```bash
curl http://127.0.0.1:4010/v1/chat/completions \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemotron-3-nano",
    "messages": [
      {"role": "user", "content": "Is 9.11 greater than 9.9? Answer yes or no."}
    ],
    "reasoning_effort": "none"
  }'
```

| Parameter | Values | Effect |
|---|---|---|
| `reasoning_effort` | `"none"` | Reasoning off: empty `reasoning_content`, answer-only |
| | `"low"` / `"high"` | Reasoning on (the current models make no distinction between levels — control is effectively on/off) |
| `chat_template_kwargs` | `{"enable_thinking": false}` | Reasoning off |
| | `{"enable_thinking": true}` | Reasoning on (default) |

## Streaming chat

Set `stream: true`:

```bash
curl -N http://127.0.0.1:4010/v1/chat/completions \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemotron-3-nano",
    "messages": [
      {"role": "user", "content": "Explique en français pourquoi les tests de performance sont importants."}
    ],
    "temperature": 0.2,
    "top_p": 0.9,
    "max_tokens": 256,
    "stream": true
  }'
```

Read each `data:` event until `data: [DONE]`.

## Image input

Use `qwen3-vl-30b` when the request includes an image. For example, pass a base64-encoded image as a data URL:

```json
{
  "model": "qwen3-vl-30b",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BASE64_ENCODED_JPEG"}}
      ]
    }
  ],
  "max_tokens": 512
}
```

Send this body to `POST /v1/chat/completions`. The Qwen3-VL projector is configured for this model; keep images reasonably sized to avoid oversized requests.

## Nimble decisions

Nimble is a decision model, not a chat model. It appears as `nimble-9b` in `GET /v1/models` and accepts the Ollama systemone request shape through the authenticated gateway:

```bash
curl http://127.0.0.1:4010/v1/systemone \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nimble-9b",
    "state": "Task A is complete. Task B depends only on Task A.",
    "questions": {
      "can_start": {
        "type": "noul",
        "instructions": "Can Task B start now?",
        "criteria": {
          "true": "Task B is ready to start.",
          "false": "Task B is not ready to start."
        }
      }
    }
  }'
```

The response contains the decision under `answers.can_start.noul`. Use a `choice` question with named criteria when selecting among multiple alternatives. A key restricted to specific models must include `nimble-9b`. Calling Nimble through `/v1/chat/completions` returns HTTP 405; use `/v1/systemone`.

## Tool calling

Provide an OpenAI-compatible tool schema. Example:

```bash
curl http://127.0.0.1:4010/v1/chat/completions \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemotron-3-nano",
    "messages": [
      {"role": "user", "content": "Quel temps fait-il à Nairobi ? Utilise les unités métriques."}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "lookup_weather",
          "description": "Get current weather for a city",
          "parameters": {
            "type": "object",
            "properties": {
              "city": {"type": "string"},
              "units": {"type": "string", "enum": ["metric", "imperial"]}
            },
            "required": ["city", "units"]
          }
        }
      }
    ],
    "tool_choice": "auto",
    "temperature": 0,
    "max_tokens": 256
  }'
```

A correct response contains `choices[0].message.tool_calls`. Execute the tool in your application, append the tool result to the conversation and call the model again for the final answer.

## Embeddings

Endpoint:

```http
POST /v1/embeddings
```

Example:

```bash
curl http://127.0.0.1:4010/v1/embeddings \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-embedding-0.6b",
    "input": "Article 37: Le Président du Faso est élu au suffrage universel direct, égal et secret pour un mandat de cinq ans."
  }'
```

The response contains:

```json
{
  "data": [
    {
      "embedding": [0.01, -0.02]
    }
  ]
}
```

Use the same embedding model for document ingestion and search. If the embedding model changes, re-embed the document corpus. Never mix `qwen3-embedding-0.6b` and `qwen3-embedding-8b` vectors in the same index — their dimensions differ (1024 vs 4096).

Do not send `stream` with embedding requests — embeddings have no streaming mode, and the gateway currently returns HTTP 500 instead of ignoring the parameter.

## Errors

| Status | Meaning | Client action |
|---:|---|---|
| 400 | Invalid request or unsupported parameter | Fix the request body |
| 401 | Missing or invalid token | Check the `Authorization` header |
| 403 | Token not allowed for this model/action | Ask the administrator for access |
| 403 (non-JSON, Cloudflare page) | Blocked at the Cloudflare edge, usually a script-like `User-Agent` | Use a standard SDK or set a normal `User-Agent`; bare `Python-urllib/x.y` agents are filtered |

Every SDK mentioned in this guide (openai-python, httpx, requests, node, okhttp) sends an accepted `User-Agent`. Only hand-rolled urllib clients without a `User-Agent` header are filtered by Cloudflare's bot protection.
| 429 | Rate or concurrency limit reached | Retry with backoff |
| 500 | Upstream model failure | Retry later or contact the administrator |
| 503 | Model unavailable or loading | Retry after the model loads |

## Notes

- Pick the chat model by role: `gemma4-e4b` for classification/routing, `qwen2.5-7b` for planning, `nemotron-3-nano` for analysis and synthesis.
- The chat context window is 65,536 tokens (prompt + completion) per request, and the embedding window is 16,384 tokens. The server runs 4 parallel chat slots and 2 embedding slots; when all slots are busy the context is split across them, so plan for roughly 16k tokens per chat request and 8k per embedding request under full load — a request arriving when slots are free can use the full window. The `max_input_tokens` reported by `/v1/models` are the models' native maxima, not the per-request limit.
- Do not expose raw chain-of-thought to end users.
- Keep prompts free of credentials and unnecessary personal data.
