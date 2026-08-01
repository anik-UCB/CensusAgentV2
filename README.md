# US Census Data Chat Agent

A natural-language chat agent that answers questions about US demographics, powered by Snowflake Cortex Agent with native tool calling. Two implementations are included — a hand-rolled v1 and a production v2 using Snowflake's managed agent infrastructure.

## Accessing the Running Application

**Live URL (v2):** [censusagentv2.streamlit.app](https://censusagentv2.streamlit.app)

**Authentication:** The app requires Snowflake credentials configured in Streamlit secrets. For the deployed app, these are pre-configured. To run your own instance:

| Setting | Value |
|---|---|
| Account | `xlbpipm-il76509` (org-account format, hyphen not period) |
| User | Your Snowflake user with `RSA_PUBLIC_KEY` set |
| Auth method | RSA key-pair (private key in PEM format in secrets.toml) |
| Role | `ACCOUNTADMIN` (or any role with access to `CENSUS_AGENT.PUBLIC`) |
| Warehouse | `COMPUTE_WH` |

No password is used — authentication is via JWT signed with your RSA private key. See [Setup](#setup) below for key generation steps.

## Written Reflection

See **[WRITTEN_REFLECTION.md](WRITTEN_REFLECTION.md)** for a detailed discussion of:

- **Development process and key architectural decisions** — Why SQL API over Agent REST endpoint, why `openai-gpt-5-mini` for the classifier, why 60s hard deadline, why semantic view over raw table access
- **What I'd improve with more time** — Eval dataset, observability/logging, rate limiting, streaming responses, config externalization
- **Edge cases and failure modes** — Adversarial inputs table (11 scenarios), known gaps (6 items with impact + fix path)
- **Testing approach** — 19 unit tests across 5 layers, what they cover vs. what requires live integration tests
- **v1 vs v2 comparison** — 11-dimension comparison table with guidance on when to choose which

## Example Queries

Try these queries:
- "What is the population of California?"
- "Compare median income between Texas counties"
- "Which states have the highest unemployment rate?"
- "What's the gender pay gap by state?"
- "Which states have the highest divorce rates?"
- "Show occupation breakdown for Massachusetts"
- "What is the weather today?" ← (blocked by guardrail)
- "Crime rates in Texas" ← (fast-fail: suggests FBI UCR)

## Architecture (v2)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Streamlit (External)                                               │
│                                                                     │
│  ┌──────────────┐   ┌────────────────────────────────────────────┐ │
│  │ streamlit_app│──▶│ agent.py                                   │ │
│  │  (Chat UI)   │   │                                            │ │
│  │  progress     │   │ 1. guardrails.sanitize_input()            │ │
│  │  updates      │   │    Blocks injection, length, empty        │ │
│  │              │   │                                            │ │
│  │  session:    │   │ 2. guardrails.fast_fail_unanswerable()    │ │
│  │  - messages  │   │    Regex: wrong year/geo/topic (<10ms)    │ │
│  │  - thread_id │   │                                            │ │
│  │  - history   │   │ 3. _classify_topic()                      │ │
│  │              │   │    LLM guardrail (openai-gpt-5-mini)       │ │
│  └──────────────┘   │    structured output + conversation ctx   │ │
│                      │                                            │ │
│                      │ 4. CortexAgentClient.run()                │ │
│                      │    SQL API → DATA_AGENT_RUN                │ │
│                      │    60s wall-clock deadline                  │ │
│                      │    JWT key-pair auth                        │ │
│                      └───────────────────────┬────────────────────┘ │
└──────────────────────────────────────────────┼──────────────────────┘
                                               │ POST /api/v2/statements
                                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Snowflake                                                          │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT (Cortex Agent)         │  │
│  │ Model: claude-sonnet-4-5                                     │  │
│  │ Tool: cortex_analyst_text_to_sql → CENSUS_SV                 │  │
│  │ Budget: 60s                                                  │  │
│  │                                                              │  │
│  │ Instructions:                                                │  │
│  │   - Schema awareness (reads semantic view dynamically)       │  │
│  │   - Ambiguous queries (asks for clarification)               │  │
│  │   - Partial matches (answers what it can)                    │  │
│  │   - Cross-category joins (uses relationships)                │  │
│  │   - Unanswerable (suggests closest alternative)              │  │
│  └──────────────────────────┬───────────────────────────────────┘  │
│                              │                                      │
│  ┌──────────────────────────▼───────────────────────────────────┐  │
│  │ CENSUS_AGENT.PUBLIC.CENSUS_SV (Semantic View)                │  │
│  │                                                              │  │
│  │ 18 state-level views: V_STATE_SUMMARY, V_STATE_INCOME, ...  │  │
│  │ 18 county-level views: V_POPULATION, V_INCOME, V_HOUSING,...│  │
│  │ 39 relationships (state↔state, county↔county joins)         │  │
│  │ 11 verified queries (golden SQL examples incl. JOINs)       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              ▲                                      │
│              36 curated SQL views over SafeGraph ACS 2020           │
│              (242K+ block groups aggregated to state & county)      │
└─────────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
├── v1/                          # Version 1: Hand-rolled agent
│   ├── agent.py                 #   LLM orchestration + SQL gen + retry loop
│   ├── streamlit_app.py         #   Chat UI (snowflake-connector auth)
│   ├── census_schema.py         #   Schema definitions for prompts
│   ├── guardrails.py            #   3-layer validation (sanitize + fast-fail + topic)
│   ├── requirements.txt
│   ├── sql/setup_views.sql
│   └── tests/
│       ├── test_guardrails.py   #   Keyword filter + SQL validation tests
│       ├── test_query_gen.py    #   SQL pattern validation
│       └── test_conversation.py #   Multi-turn context tests
│
├── v2/                          # Version 2: Cortex Agent (production)
│   ├── agent.py                 #   REST client + LLM guardrail + timeout enforcement
│   ├── streamlit_app.py         #   Chat UI (JWT auth, progress updates)
│   ├── guardrails.py            #   3-layer validation (shared with v1)
│   └── tests/
│       └── test_agent.py        #   JWT, parsing, timeout, flow tests
│
├── cortex_project/              # Deployed Snowflake objects (source of truth)
│   ├── cortex-project.yaml
│   ├── CENSUS_CHAT_AGENT.agent.yaml
│   └── CENSUS_SV.sv.yaml
│
├── WRITTEN_REFLECTION.md        # Design rationale, tradeoffs, v1 vs v2 analysis
├── .streamlit/
│   └── secrets.toml.example
├── requirements.txt
└── README.md
```

## Key Design Decisions

### Why v2 over v1?

| Concern | v1 (hand-rolled) | v2 (Cortex Agent) |
|---|---|---|
| **Tool calling** | Simulated — prompt says "output JSON", ~90 lines of fragile parsing | Native — model is constrained to call tools via structured protocol |
| **SQL generation** | LLM writes raw SQL, client validates + retries (up to 3 attempts) | Cortex Analyst generates SQL from semantic view with 39 relationships |
| **Guardrails** | 3-layer pipeline: sanitize + fast-fail + keyword check | 3-layer pipeline: sanitize + fast-fail + LLM classifier with conversation context |
| **Multi-turn** | Client manages `conversation_history` array, truncates at 10 | Server-side `thread_id` — Snowflake manages context |
| **Schema** | Static — 36 views hardcoded in `census_schema.py` | Dynamic — agent reads semantic view at runtime, no code changes needed to add tables |
| **Timeout** | 60s wall-clock deadline, shared across LLM + retries | 60s wall-clock deadline, 55s statement timeout, dynamic poll budget |

### Guardrail Architecture (both versions)

A dedicated 3-layer validation pipeline runs before any LLM or agent call:

```
User message
  │
  ├─ Layer 1: Input Sanitization (<1ms)
  │   Blocks: prompt injection patterns, >2000 chars, empty input, encoded payloads
  │
  ├─ Layer 2a: Fast-Fail for Unanswerable (<10ms)
  │   Blocks: wrong year (non-2020), wrong geo (ZIP/city/tract),
  │           out-of-scope topics (crime, weather, elections, GDP, COVID),
  │           non-US countries, individual-level data requests
  │   Each with a helpful message suggesting the closest available alternative
  │
  ├─ Layer 2b: Topic Classification
  │   v1: keyword match (instant) — blocks first messages with no census signal
  │   v2: LLM classifier (openai-gpt-5-mini, ~1-2s) with conversation context
  │       structured output → {on_topic: bool, reason: string}
  │       fails OPEN on error (agent handles it)
  │
  └─ ✅ PASS → message reaches the agent/LLM
```

Fast-fail ensures "correct and fast-refused" > "slow and correctly refused." A question like "crime rates in Texas" is rejected in <10ms instead of after a 15-30s LLM round-trip that would reach the same conclusion.

### Timeout Strategy (v2)

```
    60s wall-clock deadline (absolute cap)
    ├── 55s server-side statement timeout (Snowflake cancels query)
    ├── requests.post(timeout=remaining)  (shrinks dynamically)
    └── _poll_statement(max_wait=remaining)
         └── each GET: timeout=min(remaining, 10s)
```

Nothing silently exceeds 60s.

## Setup

### Prerequisites

- Snowflake account with Cortex Agent support
- Database `CENSUS_AGENT` with views created (see `v1/sql/setup_views.sql`)
- Key-pair authentication configured for your user

### 1. Generate Key Pair

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

### 2. Register Public Key in Snowflake

```sql
ALTER USER <your_user> SET RSA_PUBLIC_KEY='<contents of rsa_key.pub without headers>';
```

### 3. Configure Secrets

```toml
# .streamlit/secrets.toml
[snowflake]
account = "orgname-accountname"   # e.g. "xlbpipm-il76509"
user = "YOUR_USER"
private_key = """-----BEGIN PRIVATE KEY-----
...paste contents of rsa_key.p8...
-----END PRIVATE KEY-----"""
```

**Important:** No leading newline before `-----BEGIN`. The account must be in org-account format (with hyphen, not period).

### 4. Run Locally

```bash
pip install -r requirements.txt
streamlit run v2/streamlit_app.py
```

### 5. Deploy to Streamlit Community Cloud

1. Push to GitHub
2. Connect repo at share.streamlit.io
3. Set main file to `v2/streamlit_app.py`
4. Paste secrets in App Settings > Secrets
5. Deploy

## Running Tests

```bash
pip install pytest PyJWT cryptography requests
pytest v2/tests/ -v          # v2: JWT, parsing, timeouts, flow
pytest v1/tests/ -v          # v1: guardrails, SQL validation, conversation
```

### Test Strategy (v2)

| Layer | Tests | Approach |
|---|---|---|
| JWT generation | 6 | Unit — verifies account format, fingerprint, URL |
| Response parsing | 5 | Unit — constructed payloads, edge cases |
| Timeout enforcement | 2 | Mocked requests — verifies clean error messages |
| Classifier structure | 3 | Static validation of prompt and schema |
| Process flow | 3 | Mocked classifier + agent — verifies control flow |

**Tradeoffs:** These are fast, repeatable, CI-friendly tests. They don't prove the agent answers correctly (that requires an eval dataset against the live agent) or that the Streamlit UI renders properly (thin enough to trust by inspection).

## Deployed Snowflake Objects

| Object | FQN | Purpose |
|---|---|---|
| Agent | `CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT` | Orchestrates queries via Cortex Analyst |
| Semantic View | `CENSUS_AGENT.PUBLIC.CENSUS_SV` | 36 tables, 39 relationships, 11 VQRs for the Analyst tool |
| Views (36) | `CENSUS_AGENT.PUBLIC.V_STATE_*`, `V_*` | 18 categories × (state + county) aggregations |
| Database | `CENSUS_AGENT` | Container for all objects |

### Data Categories (18 topics)

Population, income, earnings, employment, occupation, education, school enrollment, housing, commuting, poverty, SNAP benefits, health insurance, internet access, language, household types, veterans, marital status, geographic mobility

## Updating the Agent

Edit `cortex_project/CENSUS_CHAT_AGENT.agent.yaml`, then deploy:

```sql
CREATE OR REPLACE AGENT CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT
  FROM SPECIFICATION $$<paste yaml>$$;
ALTER AGENT CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT COMMIT;
```

Or use the Cortex Code workspace (edits deploy automatically via `semantic_studio`).

## Data Source

- **Dataset:** SafeGraph US Open Census Data (Snowflake Marketplace)
- **Source:** US Census Bureau American Community Survey (ACS) 2020 5-year estimates
- **Coverage:** All 50 states + DC + Puerto Rico, all ~3,200 counties
- **Topics (18):** Population, income, earnings, employment, occupation, education, school enrollment, housing, commuting, poverty, health insurance, internet, language, household types, veterans, SNAP, marital status, geographic mobility
- **Source tables used:** 15 of 30 B/C-series tables + 1 metadata table (FIPS codes)
- **Aggregation:** 242K+ census block groups → state and county level
