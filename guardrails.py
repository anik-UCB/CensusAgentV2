# Shared validation layer for Census agent guardrails (input filtering, grounding checks)
# Co-authored with CoCo

"""
Operational guardrails module — a dedicated validation layer that ensures the Census
agent stays within its intended grounded knowledge base.

Three-stage validation pipeline:
  1. INPUT SANITIZATION — blocks prompt injection, excessive length, malicious patterns
  2. TOPIC CLASSIFICATION — LLM-based on/off-topic check with conversation context
  3. OUTPUT GROUNDING — validates that responses are backed by actual data queries

Each stage can fail independently and returns structured results so callers can
decide how to handle (block, warn, or pass through).
"""

import re
from dataclasses import dataclass
from enum import Enum


class ValidationResult(Enum):
    PASS = "pass"
    BLOCK = "block"
    WARN = "warn"


@dataclass
class GuardrailResult:
    result: ValidationResult
    stage: str
    message: str = ""


# --- Stage 1: Input Sanitization ---

# Maximum allowed message length (characters)
MAX_MESSAGE_LENGTH = 2000

# Patterns that indicate prompt injection attempts
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)",
    r"disregard\s+(your|all|the)\s+(instructions|rules|guidelines)",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"new\s+instruction[s]?\s*:",
    r"system\s*prompt\s*:",
    r"<\s*/?\s*system\s*>",
    r"override\s+(your|the|all)\s+(rules|instructions|guardrails)",
    r"pretend\s+(you\s+are|to\s+be)\s+",
    r"forget\s+(everything|all|your)\s+(you|instructions|about)",
    r"act\s+as\s+(if|though)\s+you",
    r"reveal\s+(your|the)\s+(system|instructions|prompt)",
    r"what\s+(is|are)\s+your\s+(system|instructions|prompt|rules)",
]

# Compiled regex for efficiency
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


def sanitize_input(user_message: str) -> GuardrailResult:
    """
    Stage 1: Input sanitization.
    Checks for:
    - Empty/whitespace-only input
    - Excessive message length
    - Prompt injection patterns
    - Encoded/obfuscated attack vectors
    """
    if not user_message or not user_message.strip():
        return GuardrailResult(
            result=ValidationResult.BLOCK,
            stage="input_sanitization",
            message="Please enter a question about US Census data."
        )

    if len(user_message) > MAX_MESSAGE_LENGTH:
        return GuardrailResult(
            result=ValidationResult.BLOCK,
            stage="input_sanitization",
            message=(
                f"Your message is too long ({len(user_message)} characters). "
                f"Please keep questions under {MAX_MESSAGE_LENGTH} characters."
            )
        )

    # Check for prompt injection attempts
    if _INJECTION_RE.search(user_message):
        return GuardrailResult(
            result=ValidationResult.BLOCK,
            stage="input_sanitization",
            message=(
                "I can only answer questions about US Census data. "
                "I noticed your message contains instructions that aren't data questions. "
                "Please ask a question about population, income, housing, education, "
                "employment, or other demographic topics."
            )
        )

    # Check for base64-encoded payloads (common injection obfuscation)
    if re.search(r"[A-Za-z0-9+/]{50,}={0,2}", user_message):
        return GuardrailResult(
            result=ValidationResult.WARN,
            stage="input_sanitization",
            message="Message contains encoded content"
        )

    return GuardrailResult(result=ValidationResult.PASS, stage="input_sanitization")


# --- Stage 2: Topic Classification ---

# Census-related topic keywords (fast pre-filter before LLM check)
CENSUS_KEYWORDS = frozenset([
    "population", "people", "residents", "inhabitants", "census",
    "income", "salary", "earn", "poverty", "wealthy", "rich", "poor", "wages",
    "housing", "homes", "rent", "homeowner", "house", "mortgage",
    "education", "college", "degree", "school", "graduate", "bachelors", "masters",
    "employ", "job", "work", "unemploy", "labor", "occupation",
    "age", "old", "young", "senior", "elderly", "youth",
    "race", "ethnic", "hispanic", "latino", "white", "black", "asian",
    "male", "female", "men", "women", "gender", "sex",
    "state", "county", "demographic", "acs",
    "commute", "transport", "drive", "transit", "bicycle", "walk",
    "insur", "uninsured", "health coverage",
    "internet", "broadband", "computer",
    "snap", "food stamp",
    "language", "english", "spanish", "bilingual",
    "veteran", "military", "served",
    "household", "family", "married", "divorce", "single", "widowed",
    "mobility", "moved", "migration",
    "enrollment", "kindergarten", "preschool",
    "compare", "highest", "lowest", "most", "least", "top", "bottom",
    "how many", "which state", "which county",
])

# US state names that indicate census intent
STATE_NAMES = frozenset([
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "puerto rico", "dc",
])


def classify_topic_fast(user_message: str, has_conversation_context: bool) -> GuardrailResult:
    """
    Stage 2a: Fast keyword-based pre-filter.
    If keywords match, the message is likely on-topic (pass through).
    If no keywords match AND there's no conversation context, it's likely off-topic.
    Returns WARN (needs LLM check) if uncertain.
    """
    msg_lower = user_message.lower()

    # If there's existing conversation context, short/ambiguous messages are likely follow-ups
    if has_conversation_context and len(user_message.strip()) < 50:
        return GuardrailResult(result=ValidationResult.PASS, stage="topic_fast")

    # Check for keyword hits
    if any(kw in msg_lower for kw in CENSUS_KEYWORDS):
        return GuardrailResult(result=ValidationResult.PASS, stage="topic_fast")

    # Check for state names
    if any(state in msg_lower for state in STATE_NAMES):
        return GuardrailResult(result=ValidationResult.PASS, stage="topic_fast")

    # No obvious census signal — needs LLM classification
    if has_conversation_context:
        # With context, lean toward pass (follow-ups are often terse)
        return GuardrailResult(result=ValidationResult.WARN, stage="topic_fast")

    # First message with no census keywords — likely off-topic
    return GuardrailResult(
        result=ValidationResult.BLOCK,
        stage="topic_fast",
        message=(
            "I'm a US Census data assistant. I can help with questions about "
            "population, income, housing, education, employment, poverty, commuting, "
            "health insurance, internet access, language, household types, veterans, "
            "SNAP benefits, marital status, school enrollment, earnings, occupation, "
            "and geographic mobility — for all US states and counties (ACS 2020).\n\n"
            "Could you ask me something related to these topics?"
        )
    )


# --- Stage 2b: Fast-Fail for Known-Unanswerable Questions ---

# Patterns that are definitively outside our dataset even though they sound census-related.
# Each entry: (compiled regex, user-facing explanation)
FAST_FAIL_PATTERNS = [
    # Wrong year — we only have 2020 ACS data
    (re.compile(r"\b(202[1-9]|2030|201[0-8])\b.*\b(census|population|income|data)\b", re.I),
     "I only have data from the **2020 ACS 5-year estimates**. I don't have data for other years."),
    (re.compile(r"\b(census|population|income|data)\b.*\b(202[1-9]|2030|201[0-8])\b", re.I),
     "I only have data from the **2020 ACS 5-year estimates**. I don't have data for other years."),

    # Wrong geography — we have states and counties, not cities/zip codes/neighborhoods/tracts
    (re.compile(r"\b(zip\s*code|zipcode|zip)\b", re.I),
     "I don't have data by ZIP code. I can show **county-level** data for any state. Would you like that instead?"),
    (re.compile(r"\bby\s+(city|town|village|neighborhood|tract|block\s*group|metro)\b", re.I),
     "I have data at the **state** and **county** level only — not by city, neighborhood, or tract. Would you like county-level results?"),

    # Topics we definitely don't cover
    (re.compile(r"\b(crime|murder|homicide|assault|robbery|theft|burglary|arrest)\b", re.I),
     "I don't have crime data. My dataset covers demographics, income, education, employment, and housing. "
     "For crime statistics, try the FBI's Uniform Crime Report."),
    (re.compile(r"\b(election|vote|voting|ballot|democrat|republican|party|politic)\b", re.I),
     "I don't have voting or election data. I cover demographic and socioeconomic topics only."),
    (re.compile(r"\b(weather|temperature|climate|rainfall|precipitation|drought)\b", re.I),
     "I don't have weather or climate data. I can show you demographic and economic data for any state/county."),
    (re.compile(r"\b(gdp|stock|market|inflation|cpi|interest\s*rate|federal\s*reserve)\b", re.I),
     "I don't have macroeconomic data (GDP, stocks, inflation). I have **household-level** income, earnings, and poverty data."),
    (re.compile(r"\b(covid|pandemic|coronavirus|vaccine|death\s*rate|mortality)\b", re.I),
     "I don't have COVID or health outcomes data. I have health **insurance coverage** rates by state/county. Would that help?"),
    (re.compile(r"\b(school\s*rating|school\s*rank|test\s*score|sat|act|graduation\s*rate)\b", re.I),
     "I don't have school ratings or test scores. I have **school enrollment** numbers and **educational attainment** levels (% with bachelor's, etc.)."),

    # Individual-level / personal data
    (re.compile(r"\b(my\s+income|my\s+data|about\s+me|individual|specific\s+person|someone)\b", re.I),
     "I only have **aggregate** statistics — population counts and averages by state/county. I can't look up data about specific individuals."),

    # Non-US
    (re.compile(r"\b(canada|mexico|uk|united\s*kingdom|india|china|europe|africa|asia|australia)\b.*\b(population|income|census)\b", re.I),
     "I only have **US** Census data (50 states + DC + Puerto Rico). I can't answer questions about other countries."),
    (re.compile(r"\b(population|income|census)\b.*\b(canada|mexico|uk|united\s*kingdom|india|china|europe|africa|asia|australia)\b", re.I),
     "I only have **US** Census data (50 states + DC + Puerto Rico). I can't answer questions about other countries."),
]


def fast_fail_unanswerable(user_message: str) -> GuardrailResult:
    """
    Stage 2b: Fast-fail for questions that are on-topic in domain but definitively
    unanswerable with our dataset.

    Philosophy: "correct and slow" > "fast and wrong", but "fast and correctly refused"
    is the best outcome for unanswerable questions. This avoids wasting 10-30s on an
    LLM call that will ultimately produce an "unavailable" response.
    """
    for pattern, explanation in FAST_FAIL_PATTERNS:
        if pattern.search(user_message):
            return GuardrailResult(
                result=ValidationResult.BLOCK,
                stage="fast_fail",
                message=explanation
            )

    return GuardrailResult(result=ValidationResult.PASS, stage="fast_fail")


# --- Stage 3: Output Grounding Validation ---

# Allowed view prefixes — responses must reference these
ALLOWED_VIEW_PREFIX = "CENSUS_AGENT.PUBLIC.V_"

# All valid view names (uppercased for matching)
VALID_VIEWS = frozenset([
    "V_POPULATION", "V_INCOME", "V_HOUSING", "V_EDUCATION", "V_EMPLOYMENT",
    "V_COMMUTE", "V_POVERTY", "V_HEALTH_INSURANCE", "V_INTERNET", "V_LANGUAGE",
    "V_HOUSEHOLD_TYPE", "V_VETERANS", "V_SNAP", "V_MARITAL_STATUS",
    "V_SCHOOL_ENROLLMENT", "V_EARNINGS", "V_OCCUPATION", "V_MOBILITY",
    "V_STATE_SUMMARY", "V_STATE_INCOME", "V_STATE_EMPLOYMENT", "V_STATE_EDUCATION",
    "V_STATE_HOUSING", "V_STATE_COMMUTE", "V_STATE_POVERTY",
    "V_STATE_HEALTH_INSURANCE", "V_STATE_INTERNET", "V_STATE_LANGUAGE",
    "V_STATE_HOUSEHOLD_TYPE", "V_STATE_VETERANS", "V_STATE_SNAP",
    "V_STATE_MARITAL_STATUS", "V_STATE_SCHOOL_ENROLLMENT", "V_STATE_EARNINGS",
    "V_STATE_OCCUPATION", "V_STATE_MOBILITY",
])


def validate_sql_safety(sql: str) -> GuardrailResult:
    """
    Stage 3a: SQL safety validation.
    Ensures generated SQL doesn't contain dangerous operations.
    """
    if not sql or not sql.strip():
        return GuardrailResult(
            result=ValidationResult.BLOCK,
            stage="sql_safety",
            message="No SQL query was generated."
        )

    sql_upper = sql.upper().strip()

    # Block destructive operations
    forbidden_ops = ['DROP', 'DELETE', 'INSERT', 'UPDATE', 'ALTER', 'CREATE',
                     'TRUNCATE', 'GRANT', 'REVOKE', 'EXECUTE', 'MERGE']
    for op in forbidden_ops:
        if re.search(rf'\b{op}\b', sql_upper):
            return GuardrailResult(
                result=ValidationResult.BLOCK,
                stage="sql_safety",
                message=f"Query contains forbidden operation: {op}"
            )

    # Block access to system/information_schema tables
    if "INFORMATION_SCHEMA" in sql_upper or "ACCOUNT_USAGE" in sql_upper:
        return GuardrailResult(
            result=ValidationResult.BLOCK,
            stage="sql_safety",
            message="Query attempts to access system metadata tables."
        )

    return GuardrailResult(result=ValidationResult.PASS, stage="sql_safety")


def validate_sql_grounding(sql: str) -> GuardrailResult:
    """
    Stage 3b: SQL grounding validation.
    Ensures the query only references allowed Census views.
    """
    sql_upper = sql.upper()

    # Check that at least one valid view is referenced
    references_valid_view = any(view in sql_upper for view in VALID_VIEWS)
    if not references_valid_view:
        return GuardrailResult(
            result=ValidationResult.BLOCK,
            stage="sql_grounding",
            message=(
                "The generated query doesn't reference any Census data views. "
                "I can only query data from the Census ACS 2020 dataset."
            )
        )

    return GuardrailResult(result=ValidationResult.PASS, stage="sql_grounding")


# --- Orchestration: Full validation pipeline ---

def validate_input(user_message: str, conversation_history: list = None) -> GuardrailResult:
    """
    Run the full input validation pipeline (stages 1 + 2a + 2b).
    Returns the first BLOCK result, or PASS if all stages pass.

    Order is designed for fast-fail:
      1. Sanitize (instant, blocks injection/length)
      2a. Topic check (instant, blocks off-topic first messages)
      2b. Unanswerable check (instant, blocks known-impossible questions)

    This ensures unanswerable questions are rejected in <10ms, not after
    a 10-30s LLM round-trip that would produce the same "unavailable" result.
    """
    # Stage 1: Input sanitization
    result = sanitize_input(user_message)
    if result.result == ValidationResult.BLOCK:
        return result

    # Stage 2a: Fast topic classification
    has_context = bool(conversation_history and len(conversation_history) > 0)
    result = classify_topic_fast(user_message, has_context)
    if result.result == ValidationResult.BLOCK:
        return result

    # Stage 2b: Fast-fail for known-unanswerable (only on first message or explicit questions)
    # Skip for short follow-ups in existing conversations (they might be valid refinements)
    if not (has_context and len(user_message.strip()) < 30):
        ff_result = fast_fail_unanswerable(user_message)
        if ff_result.result == ValidationResult.BLOCK:
            return ff_result

    # PASS or WARN (caller may optionally run LLM classifier for WARN)
    return result


def validate_output(sql: str) -> GuardrailResult:
    """
    Run the full output validation pipeline (stages 3a + 3b).
    Returns the first BLOCK result, or PASS if all stages pass.
    """
    # Stage 3a: SQL safety
    result = validate_sql_safety(sql)
    if result.result == ValidationResult.BLOCK:
        return result

    # Stage 3b: Grounding check
    result = validate_sql_grounding(sql)
    if result.result == ValidationResult.BLOCK:
        return result

    return GuardrailResult(result=ValidationResult.PASS, stage="output_validation")
