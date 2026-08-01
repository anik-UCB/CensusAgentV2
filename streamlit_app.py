# Streamlit chat UI for the US Census Agent v2 (Cortex Agent REST API)
# Co-authored with CoCo

import streamlit as st
from agent import CortexAgentClient, process_query_v2

st.set_page_config(page_title="US Census Chat Agent", page_icon="📊", layout="centered")
st.title("US Census Data Chat Agent")
st.caption("Ask questions about US demographics, income, housing, education, and employment (ACS 2020)")


@st.cache_resource
def get_agent_client():
    """Create and cache the Cortex Agent REST client."""
    return CortexAgentClient(
        account=st.secrets["snowflake"]["account"],
        user=st.secrets["snowflake"]["user"],
        private_key_pem=st.secrets["snowflake"]["private_key"],
        agent_fqn="CENSUS_AGENT.PUBLIC.CENSUS_CHAT_AGENT"
    )


if "messages" not in st.session_state:
    st.session_state.messages = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "parent_message_id" not in st.session_state:
    st.session_state.parent_message_id = None

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
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Ask about US Census data..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            client = get_agent_client()
            response, thread_id, message_id = process_query_v2(
                client, prompt,
                st.session_state.thread_id,
                st.session_state.parent_message_id
            )
            st.session_state.thread_id = thread_id
            st.session_state.parent_message_id = message_id
        st.markdown(response)

    st.session_state.messages.append({"role": "assistant", "content": response})
