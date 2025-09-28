import streamlit as st

# Initialize sidebar state
if "sidebar_open" not in st.session_state:
    st.session_state.sidebar_open = True

# Main page toggle button
toggle_label = "Close Sidebar" if st.session_state.sidebar_open else "Open Sidebar"
if st.button(toggle_label):
    st.session_state.sidebar_open = not st.session_state.sidebar_open

# Layout: sidebar + main content
cols = st.columns([0.25, 0.75]) if st.session_state.sidebar_open else st.columns([0, 1])

# Sidebar column
if st.session_state.sidebar_open:
    with cols[0]:
        st.markdown("### Sidebar")
        st.write("This is your fully controllable sidebar content.")
        st.button("Another sidebar button")

# Main content column
with cols[1]:
    st.markdown("# Main Page")
    st.write("Use the toggle button above to collapse or expand the sidebar anytime.")
