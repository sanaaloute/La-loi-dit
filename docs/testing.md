# Testing

## Running tests

```bash
pytest                 # or: make test
pytest -k retrieval    # subset
pytest -v --tb=short
```

Pytest is configured in `pyproject.toml`: `pythonpath = ["."]`,
`asyncio_mode = "auto"`, `testpaths = ["tests"]`, quiet output. All tests
run fully offline — the default `mock` LLM provider, hashing embedder,
in-memory vector store, `InMemoryCache` and SQLite require no credentials
or services.

## What the suite covers

Current `tests/` layout:

| Test file | Coverage |
|---|---|
| `tests/conftest.py` | shared fixtures (offline context, test client) |
| `tests/test_graph_e2e.py` | end-to-end graph runs: node order, conditional edges, retry budgets (max 1) |
| `tests/test_planner.py` | heuristic planner: domain keywords, language detection, scenario-date extraction, LLM fallback |
| `tests/test_retrieval.py` | RRF fusion, dedup by `chunk_id`, authority weighting, worker fallbacks |
| `tests/test_conflict_resolution.py` | resolution order: authority > recency > timeline-in-force; unresolved conflicts surfaced |
| `tests/test_citation_verification.py` | verified vs. rejected citations |
| `tests/test_memory.py` | buffer append/load, summarization, semantic recall, preferences |
| `tests/test_guardrails.py` | injection/jailbreak detection, sanitization, refusal output |
| `tests/test_chunking.py` | ingestion chunking (parent-child/semantic) |
| `tests/test_sandbox.py` | sandboxed tool isolation |
| `tests/test_api.py` | endpoint contracts (auth token, chat REST/SSE, health/ready) via FastAPI `TestClient` |

## Writing tests

- Async tests need no decorator (`asyncio_mode = "auto"`).
- Build a context with `build_context()` — it assembles the same offline
  adapters production uses when services are disabled.
- For node tests, call the node directly with a minimal `GraphState` dict
  and assert on the returned state patch.
- For graph tests, use `build_graph(ctx)` and `await graph.ainvoke(
  initial_state("..."))`, then assert on `final_state["trace"]` and the
  `FinalAnswer`.
- Keep tests offline: never configure a real provider in tests; patch at
  the protocol boundary (`RetrieverProtocol`, `LLMClient`) when you need a
  failure mode.

## Load testing (sketch)

Locust:

```python
# locustfile.py
from locust import HttpUser, task, between

class LegalUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def chat(self):
        self.client.post("/api/v1/chat", json={
            "query": "Quels sont les droits d'un salarié licencié ?"
        })
```

`locust -f locustfile.py --host http://localhost:8000 -u 50 -r 5`

k6:

```js
// chat.js
import http from "k6/http";
import { sleep } from "k6";
export const options = { vus: 50, duration: "2m" };
export default function () {
  http.post("http://localhost:8000/api/v1/chat",
    JSON.stringify({ query: "Quels sont les droits d'un salarié licencié ?" }),
    { headers: { "Content-Type": "application/json" } });
  sleep(1);
}
```

Watch the Grafana latency and error panels while load-testing; the mock
provider isolates pipeline overhead, a real provider measures true
end-to-end cost.
