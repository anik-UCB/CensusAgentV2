# Cortex Agent REST API client for US Census chat agent
# Co-authored with CoCo

import json
import hashlib
from datetime import datetime, timedelta, timezone

import jwt
import requests
from cryptography.hazmat.primitives import serialization


def generate_jwt_token(account: str, user: str, private_key_pem: str) -> str:
    """Generate a JWT token for Snowflake key-pair authentication."""
    import base64
    private_key_pem = private_key_pem.strip()
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    public_key_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    sha256_hash = hashlib.sha256(public_key_der).digest()
    fingerprint = "SHA256:" + base64.b64encode(sha256_hash).decode()

    # For JWT, account identifier must use UPPERCASE.
    # Per Snowflake docs: if account contains periods, replace with hyphens.
    # The org-account format (e.g. xlbpipm-il76509) stays hyphenated.
    account_upper = account.upper()
    qualified_user = f"{account_upper}.{user.upper()}"

    now = datetime.now(timezone.utc)
    payload = {
        "iss": f"{qualified_user}.{fingerprint}",
        "sub": qualified_user,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_jwt_debug_info(account: str, user: str, private_key_pem: str) -> dict:
    """Return debug info about JWT claims without exposing the key."""
    import base64
    private_key_pem = private_key_pem.strip()
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    public_key_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    sha256_hash = hashlib.sha256(public_key_der).digest()
    fingerprint = "SHA256:" + base64.b64encode(sha256_hash).decode()
    account_upper = account.upper()
    qualified_user = f"{account_upper}.{user.upper()}"
    return {
        "account_in_jwt": account_upper,
        "qualified_user": qualified_user,
        "issuer": f"{qualified_user}.{fingerprint}",
        "fingerprint": fingerprint,
        "url": f"https://{account.lower()}.snowflakecomputing.com",
    }


class CortexAgentClient:
    """Client for calling a Snowflake Cortex Agent via REST API."""

    def __init__(self, account: str, user: str, private_key_pem: str,
                 agent_fqn: str = "CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT"):
        self.account = account
        self.user = user
        self.private_key_pem = private_key_pem
        self.agent_fqn = agent_fqn
        self._token = None
        self._token_expiry = None

        parts = agent_fqn.split(".")
        self.database = parts[0]
        self.schema = parts[1]
        self.agent_name = parts[2]

        self.base_url = f"https://{account.lower()}.snowflakecomputing.com"

    def _get_token(self) -> str:
        """Get or refresh JWT token."""
        if self._token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._token
        self._token = generate_jwt_token(self.account, self.user, self.private_key_pem)
        self._token_expiry = datetime.now(timezone.utc) + timedelta(minutes=55)
        return self._token

    def run(self, user_message: str, thread_id: int = None, parent_message_id: int = None) -> dict:
        """Send a message to the agent via the SQL API (DATA_AGENT_RUN).
        Hard wall-clock limit of 60s covers both the initial POST and any polling."""
        import time
        deadline = time.time() + 60

        url = f"{self.base_url}/api/v2/statements"

        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        }

        # Build the request JSON for DATA_AGENT_RUN
        agent_request = {
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": user_message}]
            }]
        }
        if thread_id is not None:
            agent_request["thread_id"] = thread_id
            agent_request["parent_message_id"] = parent_message_id or 0

        request_json = json.dumps(agent_request).replace("'", "\\'")
        sql = (f"SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN("
               f"'{self.agent_fqn}', '{request_json}')")

        body = {
            "statement": sql,
            "timeout": 55,  # server-side statement timeout (seconds), under wall-clock
            "warehouse": "COMPUTE_WH",
            "role": "ACCOUNTADMIN",
            "database": self.database,
            "schema": self.schema,
        }

        remaining = max(1, int(deadline - time.time()))
        response = requests.post(url, headers=headers, json=body, timeout=remaining)

        if response.status_code not in (200, 202):
            error_text = response.text[:500]
            return {
                "text": f"Agent returned an error (HTTP {response.status_code}): {error_text}",
                "thread_id": thread_id,
                "message_id": None,
                "citations": []
            }

        data = response.json()

        # Handle async (202) - poll for results within remaining wall-clock budget
        if response.status_code == 202:
            statement_handle = data.get("statementHandle")
            poll_budget = max(1, int(deadline - time.time()))
            data = self._poll_statement(statement_handle, max_wait=poll_budget)
            if data is None:
                return {
                    "text": "The request timed out (60s limit). Please try a simpler question.",
                    "thread_id": thread_id,
                    "message_id": None,
                    "citations": []
                }

        return self._parse_sql_api_response(data, thread_id)

    def _poll_statement(self, handle: str, max_wait: int = 55) -> dict:
        """Poll the SQL API for async statement results."""
        import time
        url = f"{self.base_url}/api/v2/statements/{handle}"
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        }
        deadline = time.time() + max_wait
        while time.time() < deadline:
            remaining = max(1, int(deadline - time.time()))
            resp = requests.get(url, headers=headers, timeout=min(remaining, 10))
            if resp.status_code == 200:
                return resp.json()
            time.sleep(2)
        return None

    def _parse_sql_api_response(self, data: dict, prev_thread_id: int = None) -> dict:
        """Parse the SQL API response containing DATA_AGENT_RUN output."""
        result = {
            "text": "",
            "thread_id": prev_thread_id,
            "message_id": None,
            "citations": []
        }

        try:
            # SQL API returns data in data array
            rows = data.get("data", [])
            if rows and rows[0]:
                agent_output = rows[0][0]
                if isinstance(agent_output, str):
                    agent_output = json.loads(agent_output)

                # Extract text from agent response
                content = agent_output.get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        result["text"] += block.get("text", "")

                result["thread_id"] = agent_output.get("thread_id", prev_thread_id)
                result["message_id"] = agent_output.get("message_id")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            result["text"] = f"Failed to parse agent response: {str(e)[:200]}"

        if not result["text"]:
            result["text"] = "I received a response but couldn't extract the answer. Please try rephrasing."

        return result


def process_query_v2(client: CortexAgentClient, user_message: str,
                     thread_id: int = None, parent_message_id: int = None) -> tuple:
    """Process a user query via the Cortex Agent REST API."""
    # Run LLM guardrail on every turn
    is_on_topic, refusal = _classify_topic(client, user_message)
    if not is_on_topic:
        return refusal, thread_id, None

    try:
        result = client.run(user_message, thread_id, parent_message_id)
        return result["text"], result["thread_id"], result["message_id"]
    except requests.exceptions.Timeout:
        return "The request timed out. Please try a simpler question.", thread_id, None
    except requests.exceptions.ConnectionError:
        return "Could not connect to the Snowflake agent service. Please try again.", thread_id, None
    except Exception as e:
        return f"An error occurred: {str(e)[:200]}", thread_id, None


CLASSIFIER_PROMPT = """You are a topic classifier. Determine if the user's message is related to US Census data, demographics, or population statistics.

ON-TOPIC examples (return true):
- Questions about population, income, housing, education, employment, poverty, commuting, health insurance, internet access, language, household types, veterans, SNAP/food stamps
- Questions about US states, counties, or demographic comparisons
- Follow-up questions that reference prior census data answers (e.g. "what about Texas?", "show me the lowest")

OFF-TOPIC examples (return false):
- Weather, sports, politics, recipes, programming, stocks, entertainment
- Requests to write code, tell jokes, or do non-data tasks
- Questions about non-US countries (unless comparing to US data)

User message: """

CLASSIFIER_SCHEMA = json.dumps({
    "type": "json",
    "schema": {
        "type": "object",
        "properties": {
            "on_topic": {"type": "boolean"},
            "reason": {"type": "string"}
        },
        "required": ["on_topic"]
    }
})


def _classify_topic(client: CortexAgentClient, user_message: str) -> tuple:
    """Classify whether a message is on-topic using a fast LLM call with structured output."""
    url = f"{client.base_url}/api/v2/statements"
    headers = {
        "Authorization": f"Bearer {client._get_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
    }

    prompt = CLASSIFIER_PROMPT + user_message
    prompt_escaped = prompt.replace("\\", "\\\\").replace("'", "\\'")
    sql = (f"SELECT SNOWFLAKE.CORTEX.COMPLETE("
           f"'openai-gpt-5-mini', '{prompt_escaped}', "
           f"PARSE_JSON('{CLASSIFIER_SCHEMA}'))")

    body = {
        "statement": sql,
        "timeout": 15,
        "warehouse": "COMPUTE_WH",
        "role": "ACCOUNTADMIN",
        "database": client.database,
        "schema": client.schema,
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=20)
        if resp.status_code not in (200, 202):
            return True, ""  # fail open — let the agent handle it

        data = resp.json()
        if resp.status_code == 202:
            handle = data.get("statementHandle")
            data = client._poll_statement(handle, max_wait=15)
            if data is None:
                return True, ""

        rows = data.get("data", [])
        if rows and rows[0]:
            result = rows[0][0]
            if isinstance(result, str):
                result = json.loads(result)
            if not result.get("on_topic", True):
                reason = result.get("reason", "")
                return False, (
                    "I can only answer questions about US Census and demographic data "
                    "(population, income, housing, education, employment, poverty, "
                    "commuting, health insurance, internet, language, household types, "
                    "veterans, and SNAP benefits).\n\n"
                    f"Your question appears to be about something else{': ' + reason if reason else ''}. "
                    "Feel free to ask me anything about US demographics!"
                )
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError):
        pass  # fail open

    return True, ""
