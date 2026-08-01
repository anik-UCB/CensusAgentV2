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
        """Send a message to the agent and get a response."""
        url = (f"{self.base_url}/api/v2/databases/{self.database}"
               f"/schemas/{self.schema}/agents/{self.agent_name}:run")

        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
            "X-Snowflake-Warehouse": "COMPUTE_WH",
        }

        body = {
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": user_message}]
            }],
            "stream": False,
        }

        if thread_id is not None:
            body["thread_id"] = thread_id
            body["parent_message_id"] = parent_message_id or 0

        response = requests.post(url, headers=headers, json=body, timeout=60)

        if response.status_code != 200:
            error_text = response.text[:500]
            return {
                "text": f"Agent returned an error (HTTP {response.status_code}): {error_text}",
                "thread_id": thread_id,
                "message_id": None,
                "citations": []
            }

        data = response.json()
        return self._parse_response(data, thread_id)

    def _parse_response(self, data: dict, prev_thread_id: int = None) -> dict:
        """Parse the agent response into a clean format."""
        result = {
            "text": "",
            "thread_id": data.get("thread_id", prev_thread_id),
            "message_id": data.get("message_id"),
            "citations": []
        }

        message = data.get("message", {})
        content = message.get("content", [])
        for block in content:
            if block.get("type") == "text":
                result["text"] += block.get("text", "")
            elif block.get("type") == "tool_results":
                for tool_result in block.get("tool_results", []):
                    if "content" in tool_result:
                        for item in tool_result["content"]:
                            if item.get("type") == "text":
                                result["text"] += "\n" + item.get("text", "")

        if "citations" in data:
            result["citations"] = data["citations"]

        if not result["text"]:
            result["text"] = "I received a response but couldn't extract the answer. Please try rephrasing."

        return result


def process_query_v2(client: CortexAgentClient, user_message: str,
                     thread_id: int = None, parent_message_id: int = None) -> tuple:
    """Process a user query via the Cortex Agent REST API."""
    try:
        result = client.run(user_message, thread_id, parent_message_id)
        return result["text"], result["thread_id"], result["message_id"]
    except requests.exceptions.Timeout:
        return "The request timed out. Please try a simpler question.", thread_id, None
    except requests.exceptions.ConnectionError:
        return "Could not connect to the Snowflake agent service. Please try again.", thread_id, None
    except Exception as e:
        return f"An error occurred: {str(e)[:200]}", thread_id, None
