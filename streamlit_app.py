# Streamlit chat UI for the US Census Agent v2 (Cortex Agent REST API)
# Co-authored with CoCo

import streamlit as st
from typing import Optional
from agent import CortexAgentClient, process_query_v2, get_jwt_debug_info

st.set_page_config(page_title="US Census Chat Agent", page_icon="📊", layout="centered")
st.title("US Census Data Chat Agent")
st.caption("Ask questions about US demographics, income, housing, education, and employment (ACS 2020)")


def _check_secrets() -> Optional[str]:
    """Validate that required secrets are configured. Returns error message or None."""
    try:
        secrets = st.secrets["snowflake"]
    except (KeyError, FileNotFoundError):
        return (
            "**Configuration missing.** No `[snowflake]` section found in secrets.\n\n"
            "Please add your Snowflake credentials to `.streamlit/secrets.toml` with:\n"
            "```toml\n[snowflake]\naccount = \"...\"\nuser = \"...\"\nprivate_key = \"...\"\n```"
        )
    missing = [k for k in ("account", "user", "private_key") if k not in secrets]
    if missing:
        return (
            f"**Configuration incomplete.** Missing secret(s): `{'`, `'.join(missing)}`\n\n"
            "These are required in `.streamlit/secrets.toml` under `[snowflake]`."
        )
    return None


@st.cache_resource
def get_agent_client():
    """Create and cache the Cortex Agent REST client."""
    return CortexAgentClient(
        account=st.secrets["snowflake"]["account"],
        user=st.secrets["snowflake"]["user"],
        private_key_pem=st.secrets["snowflake"]["private_key"],
        agent_fqn="CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT"
    )


# Check configuration before rendering UI
config_error = _check_secrets()
if config_error:
    st.error(config_error, icon="⚠️")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "parent_message_id" not in st.session_state:
    st.session_state.parent_message_id = None
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

with st.sidebar:
    st.header("About")
    st.markdown("""
    This agent answers questions about US Census data using the 
    **American Community Survey (ACS) 2020** 5-year estimates.
    
    **Topics:** Population, income, employment, education, housing,
    commuting, poverty, health insurance, internet, language,
    household type, veterans, SNAP.
    
    **Powered by:** Snowflake Cortex Agent + Semantic View
    """)
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.thread_id = None
        st.session_state.parent_message_id = None
        st.session_state.conversation_history = []
        st.rerun()
    with st.expander("Debug: JWT Info"):
        try:
            debug = get_jwt_debug_info(
                st.secrets["snowflake"]["account"],
                st.secrets["snowflake"]["user"],
                st.secrets["snowflake"]["private_key"],
            )
            st.json(debug)
        except Exception as e:
            st.error(f"Key error: {e}")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Ask about US Census data..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status_container = st.empty()
        response_container = st.empty()

        def _show_status(text):
            status_container.caption(f"⏳ {text}")

        _show_status("Validating input...")
        try:
            client = get_agent_client()
        except ValueError as e:
            response = (
                "**Authentication error.** The private key appears to be invalid or "
                "incorrectly formatted.\n\n"
                f"Detail: {str(e)[:150]}\n\n"
                "Please check that your private key in secrets.toml is a valid PEM-encoded RSA key."
            )
        except Exception as e:
            response = (
                "**Connection setup failed.** I couldn't initialize the agent client.\n\n"
                f"Detail: {str(e)[:150]}\n\n"
                "Please verify your Snowflake account identifier and credentials."
            )
        else:
            try:
                _show_status("Running topic classifier...")
                import time
                start_time = time.time()

                response, thread_id, message_id = process_query_v2(
                    client, prompt,
                    st.session_state.thread_id,
                    st.session_state.parent_message_id,
                    st.session_state.conversation_history
                )

                elapsed = time.time() - start_time
                if elapsed > 5:
                    _show_status(f"Query completed in {elapsed:.1f}s")
                    import time as t2
                    t2.sleep(0.5)

                st.session_state.thread_id = thread_id
                st.session_state.parent_message_id = message_id
                st.session_state.conversation_history.append(
                    {"role": "user", "content": prompt}
                )
                st.session_state.conversation_history.append(
                    {"role": "assistant", "content": response}
                )
            except Exception as e:
                error_str = str(e)
                if "401" in error_str or "auth" in error_str.lower():
                    response = (
                        "**Authentication failed.** The server rejected our credentials "
                        "(HTTP 401). This usually means the JWT token is invalid or the "
                        "RSA public key fingerprint doesn't match what's registered on "
                        "your Snowflake user.\n\n"
                        "Try: `DESC USER <username>` and check `RSA_PUBLIC_KEY_FP`."
                    )
                elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                    response = (
                        "**Request timed out.** The query took too long to complete "
                        "(60s limit). This can happen with complex cross-category "
                        "questions.\n\n"
                        "Try asking a simpler question or narrowing to a specific state."
                    )
                elif "connect" in error_str.lower() or "resolve" in error_str.lower():
                    response = (
                        "**Connection failed.** I couldn't reach the Snowflake service. "
                        "This could be a network issue or an incorrect account identifier.\n\n"
                        f"Account used: `{st.secrets['snowflake']['account']}`"
                    )
                else:
                    response = (
                        "**Something went wrong.** I encountered an unexpected error "
                        "while processing your question.\n\n"
                        f"Detail: {error_str[:200]}\n\n"
                        "Please try again. If the problem persists, check the app logs."
                    )
        status_container.empty()
        response_container.markdown(response)

    st.session_state.messages.append({"role": "assistant", "content": response})
