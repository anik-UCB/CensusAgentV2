# US Census Data Chat Agent (v2)

An interactive chat agent that answers natural language questions about US Census data, powered by a Snowflake Cortex Agent with native tool calling.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Streamlit (External - Community Cloud / local)             │
│                                                             │
│  ┌───────────────────┐    ┌──────────────────────────────┐ │
│  │ app/streamlit_app  │───▶│ app/agent.py                 │ │
│  │ (Chat UI)          │    │ - CortexAgentClient          │ │
│  │                    │    │ - JWT key-pair auth           │ │
│  │ session_state      │    │ - Multi-turn (thread_id)     │ │
│  │ (messages)         │    │                              │ │
│  └───────────────────┘    └──────────┬───────────────────┘ │
│                                       │                     │
└───────────────────────────────────────┼─────────────────────┘
                                        │ REST API (/agents/:run)
                                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Snowflake (Deployed Objects)                               │
│                                                             │
│  ┌──────────────────────────────────────────────────┐      │
│  │ CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT            │      │
│  │ (Cortex Agent - llama3.1-70b)                    │      │
│  │                                                  │      │
│  │ Tools:                                           │      │
│  │   └── cortex_analyst_tool → CENSUS_SV            │      │
│  │       (native tool calling, structured outputs)  │      │
│  └──────────────────────┬───────────────────────────┘      │
│                         │                                   │
│  ┌──────────────────────▼───────────────────────────┐      │
│  │ CENSUS_AGENT.PUBLIC.CENSUS_SV (Semantic View)    │      │
│  │ ├── v_state_summary                              │      │
│  │ ├── v_state_income                               │      │
│  │ ├── v_state_housing                              │      │
│  │ ├── v_state_education                            │      │
│  │ ├── v_state_employment                           │      │
│  │ └── v_state_poverty                              │      │
│  └──────────────────────────────────────────────────┘      │
│                         ▲                                   │
│          Curated views over SafeGraph ACS 2020              │
│          (242K+ block groups → state/county)                │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
us-census-agent-v2/
├── app/
│   ├── streamlit_app.py      # Chat UI (Streamlit entrypoint)
│   └── agent.py              # CortexAgentClient (REST + JWT)
├── cortex_project/
│   ├── cortex-project.yaml   # Project manifest
│   ├── CENSUS_CHAT_AGENT.agent.yaml  # Agent spec (source of truth)
│   └── CENSUS_SV.sv.yaml    # Semantic view spec (source of truth)
├── sql/
│   └── setup_views.sql       # DDL for Census views in Snowflake
├── .streamlit/
│   └── secrets.toml.example  # Credential template
├── requirements.txt          # Python dependencies
└── README.md
```

## Deployed Snowflake Objects

| Object | Type | FQN |
|--------|------|-----|
| Agent | Cortex Agent | `CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT` |
| Semantic View | Semantic View | `CENSUS_AGENT.PUBLIC.CENSUS_SV` |
| Views | SQL Views | `CENSUS_AGENT.PUBLIC.V_STATE_*` |
| Database | Database | `CENSUS_AGENT` |

## Setup

### 1. Snowflake Prerequisites

The following must already exist in your Snowflake account:
- Database `CENSUS_AGENT` with the Census views (run `sql/setup_views.sql`)
- Semantic view deployed (`cortex_project/CENSUS_SV.sv.yaml`)
- Cortex Agent deployed (`cortex_project/CENSUS_CHAT_AGENT.agent.yaml`)
- A user with key-pair auth configured

### 2. Configure Key-Pair Auth

```sql
-- Generate a key pair (local machine)
-- openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
-- openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub

-- Register public key with your user
ALTER USER ANIKDUCF SET RSA_PUBLIC_KEY='MIIBIjANBg...';
```

### 3. Local Development

```bash
git clone <your-repo-url>
cd us-census-agent-v2

pip install -r requirements.txt

# Configure credentials
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit with your account, user, and private key

# Run
streamlit run app/streamlit_app.py
```

### 4. Deploy to Streamlit Community Cloud

1. Push to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo, set main file to `app/streamlit_app.py`
4. In **App Settings > Secrets**, add:

```toml
[snowflake]
account = "rq52109"
user = "ANIKDUCF"
private_key = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBg...your key...
-----END PRIVATE KEY-----"""
```

5. Deploy

## v1 vs v2

| | v1 (agent.py) | v2 (Cortex Agent) |
|---|---|---|
| LLM orchestration | DIY prompt → parse JSON → execute SQL | Native tool calling via Cortex Agent |
| Tool calling | Simulated (prompt engineering) | Real (structured outputs, constrained) |
| Guardrails | Manual keyword filter + SQL allowlist | Agent instructions + Cortex enforcement |
| Multi-turn | Client-side history array | Server-side thread_id |
| Auth | Snowflake connector (password/key) | REST API + JWT |
| Parsing risk | High (fragile JSON extraction) | None (structured API response) |

## Updating the Agent

To modify the agent or semantic view, edit the YAML files in `cortex_project/` and redeploy using the Cortex Code workspace or SQL:

```sql
-- Example: update agent spec
ALTER AGENT CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT
  MODIFY LIVE VERSION SET SPECIFICATION = $$<yaml>$$;
```

## Data Source

- **Dataset**: SafeGraph US Open Census Data (Snowflake Marketplace)
- **Source**: US Census Bureau ACS 2020 5-year estimates
- **Coverage**: All 50 states + DC + Puerto Rico
- **Topics**: Population, income, housing, education, employment, poverty, commuting, health insurance, internet, language, veterans, SNAP
