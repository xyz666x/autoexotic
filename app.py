import streamlit as st

# Initialize session state for sidebar visibility
if "sidebar_visible" not in st.session_state:
    st.session_state.sidebar_visible = True

# Toggle button to show/hide sidebar
if st.button("Toggle Sidebar"):
    st.session_state.sidebar_visible = not st.session_state.sidebar_visible

# Layout: sidebar and main content
cols = st.columns([0.25, 0.75]) if st.session_state.sidebar_visible else st.columns([0, 1])

# Sidebar content
if st.session_state.sidebar_visible:
    with cols[0]:
        st.header("Sidebar")
        st.write("This is your custom sidebar content.")
        st.button("Another sidebar button")

# Main content
with cols[1]:
    st.title("Main Page")
    st.write("Use the toggle button above to show/hide the sidebar.")
