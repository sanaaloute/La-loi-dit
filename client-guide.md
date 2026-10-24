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

Chat models:

- `gpt-oss-20b` — primary model
- `nemotron-3-nano` — fallback
- `qwen2.5-7b` — secondary fallback
- `gemma4-e4b` — French/legal alternate

Embedding models:

- `qwen3-embedding-0.6b` — 1024-dimensional embeddings; fast and light
- `qwen3-embedding-8b` — 4096-dimensional embeddings; best retrieval quality for RAG

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
    "model": "gpt-oss-20b",
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

Use validated limits unless your administrator changes them:

- Keep rendered input at or below 1,000 tokens, including tool schemas and conversation history.
- Keep `max_tokens` at or below 256 for normal production requests.

## Streaming chat

Set `stream: true`:

```bash
curl -N http://127.0.0.1:4010/v1/chat/completions \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss-20b",
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

## Tool calling

Provide an OpenAI-compatible tool schema. Example:

```bash
curl http://127.0.0.1:4010/v1/chat/completions \
  -H "Authorization: Bearer $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss-20b",
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

- Use `gpt-oss-20b` unless your administrator tells you to use another model.
- Do not expose raw chain-of-thought to end users.
- Keep prompts free of credentials and unnecessary personal data.
