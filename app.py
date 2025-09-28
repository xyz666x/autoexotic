import streamlit as st

# Initialize sidebar state
if "sidebar_open" not in st.session_state:
    st.session_state.sidebar_open = True

# Toggle button
if st.button("Toggle Sidebar"):
    st.session_state.sidebar_open = not st.session_state.sidebar_open

# Layout
if st.session_state.sidebar_open:
    cols = st.columns([0.25, 0.75])
else:
    cols = st.columns([0, 1])

# Sidebar column
if st.session_state.sidebar_open:
    with cols[0]:
        st.write("Custom sidebar content")
        st.button("Another button")

# Main content column
with cols[1]:
    st.write("Main page content goes here")
