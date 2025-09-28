import streamlit as st

# Initialize session state for sidebar visibility
if "sidebar_visible" not in st.session_state:
    st.session_state.sidebar_visible = True

# Toggle button
if st.button("Toggle Sidebar"):
    st.session_state.sidebar_visible = not st.session_state.sidebar_visible

# Columns: sidebar + main content
if st.session_state.sidebar_visible:
    cols = st.columns([0.25, 0.75])
else:
    cols = st.columns([0.001, 0.999])  # use tiny width instead of 0

# Sidebar content
if st.session_state.sidebar_visible:
    with cols[0]:
        st.header("Sidebar")
        st.write("Sidebar content here")
        st.button("Another button")

# Main content
with cols[1]:
    st.title("Main Page")
    st.write("Main content goes here")
