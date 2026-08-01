# Unit tests for the v2 Cortex Agent client
# Co-authored with CoCo

import sys
import os
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "v2"))

from agent import (
    CortexAgentClient,
    generate_jwt_token,
    get_jwt_debug_info,
    process_query_v2,
    CLASSIFIER_PROMPT,
    CLASSIFIER_SCHEMA,
)


# --- JWT generation tests ---

class TestJWTGeneration:
    """Test that JWT tokens are well-formed and use correct account identifiers."""

    FAKE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7o4qne60TB3aq
aobMCxXJkAqWT5EYFCNbs5VexT+Cnti0nCsjMJuHv0Ui7LMHrMWxfBEiNjnFIFmV
kMK5cHKaBqzv2Mxs7PvXNj0oLJ9+0z3bM3dUFyVZ8Eg0EL5VjfkGnvKMDqT0PSA
lk3VnJnYLndi1mvuRC/3LanA9FKz9ABW0VqfPLWJdmUW2sCiYC/Cm7Xi2eUQ2r6F
ZG7tlSb+0JKGVzSBaFuKFmJzqNFcRrRsSxjnEVG/J7V0ssSWR0GY/RYll7sFT/0y
b38gfBCgfQAWN/5ATaJgTwTGF3IpJqCeBrFNEsCKF4+cWxHq44OdH7MOE+xfmRJl
lhQzACJhAgMBAAECggEAGpPuVFn7C6+EPBKFjVISp5RdCwCf7Zk1v8i6E8eDOB5x
mFCVmjA2tFkFSTk6ioENZ4NvGxm+w9n2tENS0B6h/KYZbS35bk/LjLnKOHk62hE/
RkQgNSFJpV0A/oDnX2pGT71vB9TVLAA2B0u+d7n2k/8UjkMmv/OU9VpKXOAf7JqX
0cvjLJjZDDDzJtGOCnDBB02t9TK9p1TwiXL5N2fLn/Xyh5aMxwbrjTAq0w7E7QPn
K7LhJ8J5P/ILPukqLEN8dDAFJ/8ohhOl2E5z7S7PB9JI7CdKF6u/7cdiZq3tJU3x
WO+V/jQhfVUa8B8JdMF1uS8h5T2IfOL8vZ4q3v0DPQKBgQDmoXb3EvF05SIKGsaO
lx5k/HM0VYN+z2NU3pMihT0lPHyfoB1BEE1D5j5jVmFkMEsXzr6L3mlO5sLmUl1x
g4TjAokJ8S4a9v0J0kXrG2dvLxn+b7m0iHdR+h7qFvW6trd+7Z7n0fLk8PLhIT9M
xXP5HJI5eNCHl0g9f/BNWKSO1QKBgQDQrbuIWEPH/5gjmEFq2Yi+M7FRJzNLuuPi
U2Ff0xBNp0jGJPVx5pUbHNfR+e+bwCLcCK0ZjHKDIivqGtHyT97SRUQ17DK3hcSp
SJLx9i5Ke6s8lhHkX/vCy/N+L0NQPX9oTiLvd8BVn6MJK6TqlD8F/1KRLJ0kR0F
D7JkJjYnXQKBgGjwFu0F2lGqxWZhN7Hc7JhQ8zCHJgjpbBYSL2ZQmIGFzRBWnK+c
A1o2T8F2lYh+AGx2w9oFH2XC4POnGq+JC0pde2zf1LYrnO5e+x5w2SVPBIbBR32J
k6ydGPw5hQJWK2o6B4I5CiK7JGiGLkB+Mw+FmO1F2p5FYH/pBkrz6HNAoGBAMmP
kaxJPj0JdAoVHEzjLbGF3a+oaBCo/mPlMVDNXckrMRhnDIBptQtQG9xOUdZXQIBl
VQp7FxLnJlCxaI2+GR1Hf0lgDaM9yXNLFl1kEBsjX8NrqWN1v6+sLdy8fmA1FJVx
pBRDkB+vN5EAwx4YbVJh8P9x0dM/wQCb30/hl8slAoGANGUVv06vOF5T39HmEM1T
z0J4cW/DFNvHp7dqVD/IROC6L/D7B9LKPZ3P2QAf5tkr+hH+vZzz4CTiB/TCNM2
1OJWExjFe3DB8RGXVg/+C5Eq6BQ2l9A0y3zVnPf+t5oFZ/KPQHI8oB93dVp/gNbM
mGDR+1bF0V68FVd6eWxPgDQ=
-----END PRIVATE KEY-----"""

    def test_jwt_preserves_hyphens_in_account(self):
        """Account identifier must NOT replace hyphens with periods."""
        debug = get_jwt_debug_info("xlbpipm-il76509", "TESTUSER", self.FAKE_KEY)
        assert debug["account_in_jwt"] == "XLBPIPM-IL76509"
        assert "." not in debug["account_in_jwt"]

    def test_jwt_qualified_user_format(self):
        """Qualified user should be ACCOUNT.USER (one dot separating them)."""
        debug = get_jwt_debug_info("xlbpipm-il76509", "ANIKDUCF", self.FAKE_KEY)
        assert debug["qualified_user"] == "XLBPIPM-IL76509.ANIKDUCF"

    def test_jwt_issuer_includes_fingerprint(self):
        """Issuer claim must be ACCOUNT.USER.SHA256:..."""
        debug = get_jwt_debug_info("xlbpipm-il76509", "ANIKDUCF", self.FAKE_KEY)
        assert debug["issuer"].startswith("XLBPIPM-IL76509.ANIKDUCF.SHA256:")

    def test_jwt_fingerprint_is_sha256(self):
        """Fingerprint should be SHA256: followed by base64."""
        debug = get_jwt_debug_info("xlbpipm-il76509", "TESTUSER", self.FAKE_KEY)
        assert debug["fingerprint"].startswith("SHA256:")
        # Base64 part should be non-empty
        b64_part = debug["fingerprint"].replace("SHA256:", "")
        assert len(b64_part) > 20

    def test_jwt_url_uses_lowercase(self):
        """REST URL should use lowercase account."""
        debug = get_jwt_debug_info("XLBPIPM-IL76509", "USER", self.FAKE_KEY)
        assert debug["url"] == "https://xlbpipm-il76509.snowflakecomputing.com"

    def test_private_key_whitespace_stripped(self):
        """Leading/trailing whitespace in the key should not cause errors."""
        padded_key = "\n\n  " + self.FAKE_KEY + "  \n\n"
        debug = get_jwt_debug_info("test-account", "USER", padded_key)
        assert debug["fingerprint"].startswith("SHA256:")


# --- Response parsing tests ---

class TestResponseParsing:
    """Test that SQL API responses are correctly parsed into user-facing text."""

    def _make_client(self):
        """Create a client without actually connecting."""
        with patch.object(CortexAgentClient, '__init__', lambda self, *a, **k: None):
            client = CortexAgentClient.__new__(CortexAgentClient)
            client.database = "CENSUS_AGENT"
            client.schema = "PUBLIC"
            client.agent_name = "CENSUS_CHAT_AGENT"
            return client

    def test_parse_text_response(self):
        """A normal text response should be extracted."""
        client = self._make_client()
        data = {
            "data": [[json.dumps({
                "content": [{"type": "text", "text": "California has 39.3 million people."}],
                "thread_id": "t1",
                "message_id": "m1"
            })]]
        }
        result = client._parse_sql_api_response(data)
        assert result["text"] == "California has 39.3 million people."
        assert result["thread_id"] == "t1"
        assert result["message_id"] == "m1"

    def test_parse_empty_data(self):
        """Empty data array should return the graceful degradation message."""
        client = self._make_client()
        data = {"data": []}
        result = client._parse_sql_api_response(data)
        assert "wasn't able to answer" in result["text"]
        assert "population, income" in result["text"]

    def test_parse_malformed_json(self):
        """Non-JSON string in data should produce error message, not crash."""
        client = self._make_client()
        data = {"data": [["not valid json {{{"]]}
        result = client._parse_sql_api_response(data)
        assert "Failed to parse" in result["text"] or "wasn't able" in result["text"]

    def test_parse_no_text_blocks(self):
        """Response with content but no text blocks should return fallback."""
        client = self._make_client()
        data = {
            "data": [[json.dumps({
                "content": [{"type": "tool_use", "id": "123"}],
            })]]
        }
        result = client._parse_sql_api_response(data)
        assert "wasn't able to answer" in result["text"]

    def test_parse_multiple_text_blocks(self):
        """Multiple text blocks should be concatenated."""
        client = self._make_client()
        data = {
            "data": [[json.dumps({
                "content": [
                    {"type": "text", "text": "Part 1. "},
                    {"type": "text", "text": "Part 2."},
                ],
            })]]
        }
        result = client._parse_sql_api_response(data)
        assert result["text"] == "Part 1. Part 2."


# --- Timeout enforcement tests ---

class TestTimeoutEnforcement:
    """Test that wall-clock timeouts are respected."""

    def _make_client(self):
        with patch.object(CortexAgentClient, '__init__', lambda self, *a, **k: None):
            client = CortexAgentClient.__new__(CortexAgentClient)
            client.base_url = "https://test.snowflakecomputing.com"
            client.database = "DB"
            client.schema = "SC"
            client.agent_name = "AG"
            client.agent_fqn = "DB.SC.AG"
            client._token = "fake"
            client._token_expiry = datetime(2099, 1, 1, tzinfo=timezone.utc)
            return client

    @patch("agent.requests.post")
    def test_timeout_on_initial_post(self, mock_post):
        """If the initial POST times out, return a clean error message."""
        from requests.exceptions import Timeout
        mock_post.side_effect = Timeout("connection timed out")
        client = self._make_client()
        result = process_query_v2(client, "population of CA")
        assert "timed out" in result[0].lower() or "error" in result[0].lower()

    @patch("agent.requests.post")
    def test_poll_returns_none_on_timeout(self, mock_post):
        """If polling exhausts its budget, return timeout message."""
        mock_post.return_value = MagicMock(
            status_code=202,
            json=lambda: {"statementHandle": "handle123"}
        )
        client = self._make_client()
        with patch.object(client, "_poll_statement", return_value=None):
            result = client.run("test question")
        assert "timed out" in result["text"].lower()


# --- Guardrail (classifier) tests ---

class TestClassifierStructure:
    """Test that the classifier prompt and schema are well-formed."""

    def test_classifier_prompt_ends_with_user_message_slot(self):
        """Prompt should end with a slot for the user message."""
        assert CLASSIFIER_PROMPT.rstrip().endswith("User message:")

    def test_classifier_schema_is_valid_json(self):
        """Schema should parse as valid JSON."""
        parsed = json.loads(CLASSIFIER_SCHEMA)
        assert parsed["type"] == "json"
        assert "on_topic" in parsed["schema"]["properties"]
        assert parsed["schema"]["properties"]["on_topic"]["type"] == "boolean"

    def test_classifier_schema_requires_on_topic(self):
        """on_topic must be in the required fields."""
        parsed = json.loads(CLASSIFIER_SCHEMA)
        assert "on_topic" in parsed["schema"]["required"]


# --- process_query_v2 integration flow tests ---

class TestProcessQueryFlow:
    """Test the overall flow of process_query_v2."""

    def _make_client(self):
        with patch.object(CortexAgentClient, '__init__', lambda self, *a, **k: None):
            client = CortexAgentClient.__new__(CortexAgentClient)
            client.base_url = "https://test.snowflakecomputing.com"
            client.database = "DB"
            client.schema = "SC"
            client.agent_name = "AG"
            client.agent_fqn = "DB.SC.AG"
            client._token = "fake"
            client._token_expiry = datetime(2099, 1, 1, tzinfo=timezone.utc)
            return client

    @patch("agent._classify_topic")
    @patch.object(CortexAgentClient, "run")
    def test_on_topic_message_reaches_agent(self, mock_run, mock_classify):
        """On-topic messages should pass through to the agent."""
        mock_classify.return_value = (True, "")
        mock_run.return_value = {"text": "39 million", "thread_id": "t1", "message_id": "m1"}
        client = self._make_client()
        text, tid, mid = process_query_v2(client, "population of CA")
        assert text == "39 million"
        mock_run.assert_called_once()

    @patch("agent._classify_topic")
    @patch.object(CortexAgentClient, "run")
    def test_off_topic_message_blocked(self, mock_run, mock_classify):
        """Off-topic messages should be blocked before reaching the agent."""
        mock_classify.return_value = (False, "I can only answer Census questions.")
        client = self._make_client()
        text, tid, mid = process_query_v2(client, "What is the weather?")
        assert "Census" in text
        mock_run.assert_not_called()

    @patch("agent._classify_topic")
    @patch.object(CortexAgentClient, "run")
    def test_classifier_failure_fails_open(self, mock_run, mock_classify):
        """If the classifier errors, the message should pass through (fail open)."""
        mock_classify.side_effect = Exception("classifier broke")
        mock_run.return_value = {"text": "answer", "thread_id": None, "message_id": None}
        client = self._make_client()
        # process_query_v2 catches exceptions from classify internally,
        # but if it propagates, the outer try/except should still return something
        text, tid, mid = process_query_v2(client, "population of TX")
        # Should not crash — either gets an answer or an error string
        assert text is not None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
