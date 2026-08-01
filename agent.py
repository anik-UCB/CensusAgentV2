# Tool-calling Census agent using AI_COMPLETE with structured outputs (response_format)
# Co-authored with CoCo

import json
import re
import pandas as pd
from census_schema import SCHEMA_DESCRIPTION, STATE_ABBREVIATIONS


# JSON schema that constrains AI_COMPLETE output — no more hoping the model follows instructions
TOOL_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["query", "refuse", "clarify", "unavailable"]
        },
        "sql": {
            "type": "string",
            "description": "SQL SELECT query against CENSUS_AGENT.PUBLIC views"
        },
        "explanation": {
            "type": "string",
            "description": "Brief explanation of what the query does"
        },
        "message": {
            "type": "string",
            "description": "Response message for refuse/clarify/unavailable actions"
        }
    },
    "required": ["action"]
}

SYSTEM_PROMPT = f"""You are a US Census data assistant. You answer questions about American demographics
using the American Community Survey (ACS) 2020 5-year estimates, aggregated at the state and county level.

{SCHEMA_DESCRIPTION}

DECISION RULES:
- If the user asks about Census/demographics data you can answer: action = "query", provide SQL
- If the user asks about non-Census topics (weather, sports, politics, etc.): action = "refuse"
- If the question is ambiguous and you need more info: action = "clarify"
- If it's Census-related but the data isn't in the available views: action = "unavailable"

SQL RULES:
- Always use fully qualified names: CENSUS_AGENT.PUBLIC.<view_name>
- Use two-letter state abbreviations in WHERE clauses (e.g., STATE_NAME = 'CA')
- Convert full state names the user mentions to abbreviations
- Always include ORDER BY for ranked results
- Use LIMIT for top-N queries (default LIMIT 10 unless specified)
- Never use SELECT *; always specify columns explicitly
- Return at most 20 rows unless user asks for more
- Use V_STATE_* views for state-level questions (pre-aggregated, one row per state)
- Only use county-level views when user specifically asks about counties
- All V_STATE_* views include TOTAL_POPULATION — no JOIN needed to sort by population
"""

# Allowed views for SQL validation
ALLOWED_VIEWS = [
    "V_POPULATION", "V_INCOME", "V_HOUSING", "V_EDUCATION", "V_EMPLOYMENT",
    "V_COMMUTE", "V_POVERTY", "V_HEALTH_INSURANCE", "V_INTERNET",
    "V_LANGUAGE", "V_HOUSEHOLD_TYPE", "V_VETERANS", "V_SNAP",
    "V_STATE_SUMMARY", "V_STATE_INCOME", "V_STATE_EMPLOYMENT",
    "V_STATE_EDUCATION", "V_STATE_HOUSING", "V_STATE_COMMUTE",
    "V_STATE_POVERTY", "V_STATE_HEALTH_INSURANCE", "V_STATE_INTERNET",
    "V_STATE_LANGUAGE", "V_STATE_HOUSEHOLD_TYPE", "V_STATE_VETERANS",
    "V_STATE_SNAP"
]

FORBIDDEN_SQL_OPS = re.compile(
    r'\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXECUTE)\b',
    re.IGNORECASE
)


def validate_sql(sql: str) -> tuple[bool, str]:
    """Validate generated SQL is safe and references allowed views."""
    match = FORBIDDEN_SQL_OPS.search(sql)
    if match:
        return False, f"Forbidden operation: {match.group(1)}"
    sql_upper = sql.upper()
    if not any(f"CENSUS_AGENT.PUBLIC.{v}" in sql_upper for v in ALLOWED_VIEWS):
        return False, "Query does not reference any allowed Census views"
    return True, ""


def _build_conversation_array(conversation_history: list, user_message: str) -> str:
    """Build the conversation array for AI_COMPLETE (JSON string of messages)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in conversation_history[-10:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return json.dumps(messages)


def call_ai_complete(conn, conversation_history: list, user_message: str,
                     model: str = "llama3.1-70b") -> dict:
    """
    Call AI_COMPLETE with response_format to get a guaranteed-valid structured response.
    No more parsing hacks — the model is constrained to output valid JSON matching our schema.
    """
    messages_json = _build_conversation_array(conversation_history, user_message)
    schema_json = json.dumps(TOOL_CALL_SCHEMA)

    sql = f"""
    SELECT AI_COMPLETE(
        model => '{model}',
        prompt => PARSE_JSON(:1),
        response_format => PARSE_JSON(:2)
    )
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (messages_json, schema_json))
        row = cursor.fetchone()
        if not row or not row[0]:
            return {"action": "error", "message": "Empty response from AI_COMPLETE."}
        result = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return result
    finally:
        cursor.close()


def format_results(df: pd.DataFrame, explanation: str) -> str:
    """Format query results into a readable response."""
    if df is None or len(df) == 0:
        return "The query returned no results. The data might not be available for that area or criteria."

    parts = [explanation, ""]
    if len(df) == 1 and len(df.columns) <= 4:
        for col in df.columns:
            val = df.iloc[0][col]
            if isinstance(val, (int, float)) and abs(val) > 1000:
                val = f"{val:,.0f}"
            elif isinstance(val, float):
                val = f"{val:.1f}"
            parts.append(f"**{col.replace('_', ' ').title()}**: {val}")
    else:
        parts.append(df.head(20).to_markdown(index=False))
        if len(df) > 20:
            parts.append(f"\n*Showing first 20 of {len(df)} results.*")
    return "\n".join(parts)


def process_query(conn, conversation_history: list, user_message: str,
                  model: str = "llama3.1-70b") -> tuple[str, list]:
    """
    Main agent loop: send user message to AI_COMPLETE with structured output,
    then execute the tool call (SQL query) or return the refusal/clarification.

    Returns (response_text, updated_conversation_history).
    """
    try:
        tool_call = call_ai_complete(conn, conversation_history, user_message, model)
    except Exception as e:
        return f"Error calling AI service: {str(e)[:200]}", conversation_history

    action = tool_call.get("action", "error")

    if action == "refuse":
        response = tool_call.get("message", "I can only answer questions about US Census data.")
    elif action == "clarify":
        response = tool_call.get("message", "Could you provide more details?")
    elif action == "unavailable":
        response = tool_call.get("message", "That data isn't available in my dataset.")
    elif action == "query":
        sql = tool_call.get("sql", "")
        explanation = tool_call.get("explanation", "")

        is_valid, err = validate_sql(sql)
        if not is_valid:
            response = f"I generated an unsafe query and caught it ({err}). Please rephrase."
        else:
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(50)
                cursor.close()
                df = pd.DataFrame(rows, columns=columns)
                response = format_results(df, explanation)
            except Exception as e:
                response = (
                    f"Query failed — could you rephrase or be more specific?\n\n"
                    f"Detail: {str(e)[:300]}"
                )
    else:
        response = tool_call.get("message", "Something went wrong. Could you rephrase?")

    updated_history = conversation_history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": response}
    ]
    return response, updated_history
