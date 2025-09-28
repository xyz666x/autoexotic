import streamlit as st

# Hide the default Streamlit toolbar (top-right menu)
st.markdown(
    """
    <style>
    /* Hide the top-right Streamlit toolbar */
    [data-testid="stToolbar"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Sidebar content
with st.sidebar:
    st.header("Sidebar")
    st.write("Sidebar is visible")
    st.button("Sidebar Button")

# Main content
st.title("Main Page")
st.write("The top-right toolbar is hidden now.")
