# Workflow

The exact pipeline is defined in `backend/workflows/graph.py`
(`build_graph`). Retry budgets come from `backend/core/config.py`:

| Budget | Value | Enforced in |
|---|---|---|
| `max_retrieval_retries` | 1 | `_route_after_reasoning`, `_route_after_reflection` |
| `max_reflection_iterations` | 1 | `_route_after_reflection` |

The planner's corrective JSON retry is a fixed single retry inside
`LLMClient.complete_json` (not configurable).

## Graph

```mermaid
flowchart TB
    START((START)) --> ig[input_guardrail]
    ig -->|guardrail.allowed| pl[planner]
    ig -->|not allowed| ref[refusal]
    ref --> END1((END))

    pl --> ca[context_agent]
    ca --> ma[memory_agent]
    ma --> rc[retrieval_coordinator]
    rc --> cr[conflict_resolver]
    cr --> er[evidence_ranking]
    er --> cva[coverage_auditor]

    cva -->|"needs_more_retrieval AND<br/>retrieval_retries < 1"| rc
    cva -->|else| ra[reasoning_agent]

    ra -->|"needs_more_retrieval AND<br/>retrieval_retries < 1"| rc
    ra -->|else| rf[reflection]

    rf -->|"should_retry_retrieval AND<br/>reflection_count ≤ 1 AND<br/>retrieval_retries < 1"| rc
    rf -->|else| rg[response_generator]

    rg --> clv[claim_verification]
    clv --> cv[citation_verification]
    cv --> og[output_guardrail]
    og --> END2((END))
```

Notes on the routing functions (verbatim from `graph.py`):

- `_route_after_guardrail` — blocked queries go straight to `refusal`.
- `_route_after_reasoning` — the reasoning agent may request one extra
  retrieval pass (`needs_more_retrieval`) as long as the shared retrieval
  retry budget is not exhausted.
- `_route_after_reflection` — reflection may send the state back to
  `retrieval_coordinator` at most once; both the reflection budget and the
  retrieval budget are checked.

The state (`GraphState`, `backend/core/state.py`) is a `TypedDict` with
`total=False`; each node returns only the keys it updates. Retry counters
(`planning_retries`, `retrieval_retries`, `reflection_count`) are explicit
state keys, which makes the "max retry = 1" policy inspectable at runtime.

## Full chat request — sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as Client
    participant API as FastAPI /api/v1/chat
    participant W as LangGraph workflow
    participant P as Planner
    participant R as RetrievalCoordinator
    participant M as MemoryStore
    participant L as LLM

    U->>API: POST /api/v1/chat {query, session_id?}
    API->>API: auth (JWT), rate limit, validate ChatRequest
    API->>W: ainvoke(initial_state(query, session_id))
    W->>W: input_guardrail (injection / jailbreak check)
    W->>P: plan(query)
    P->>L: complete_json (or heuristic fallback)
    L-->>P: RetrievalPlan
    W->>M: load_buffer(session_id) — context_agent
    W->>M: recall(user_id, query) — memory_agent
    W->>R: retrieve(tasks) — parallel workers
    R-->>W: EvidenceChunk[] (RRF-fused, deduped, reranked)
    W->>W: conflict_resolver → evidence_ranking
    W->>L: reasoning_agent (grounded analysis)
    opt needs more evidence and retries < 1
        W->>R: retrieve(retry tasks)
    end
    W->>L: reflection (self-critique)
    W->>L: response_generator (grounded answer + [n] citations)
    W->>W: claim_verification (per-claim support levels)
    W->>W: citation_verification (reject fabricated citations)
    W->>W: output_guardrail (thresholds + disclaimer)
    W-->>API: final_state (FinalAnswer, trace, errors)
    API->>M: append_turn(user msg, answer) — best effort
    API-->>U: ChatResponse {session_id, answer, trace, latency_ms}
```

## Streaming

`stream_query` uses `graph.astream(state, stream_mode="updates")` and yields
`{"node": <name>, "update": <serialized state patch>}` events — this powers
both the SSE endpoint and the WebSocket endpoint, so clients can render
progress node by node.
