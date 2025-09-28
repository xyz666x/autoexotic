import streamlit as st

# Initialize sidebar state
if "sidebar_open" not in st.session_state:
    st.session_state.sidebar_open = True

# Sidebar toggle button (main page)
if st.button("Toggle Sidebar"):
    st.session_state.sidebar_open = not st.session_state.sidebar_open

# Custom CSS for drawer effect
st.markdown(
    """
    <style>
    /* Sidebar styling */
    .custom-sidebar {
        position: fixed;
        top: 0;
        left: 0;
        height: 100%;
        width: 250px;
        background-color: #f0f2f6;
        padding: 1rem;
        overflow: auto;
        z-index: 100;
        transition: transform 0.3s ease-in-out;
    }

    .custom-sidebar.hidden {
        transform: translateX(-100%);
    }

    /* Overlay for mobile */
    .sidebar-overlay {
        position: fixed;
        top: 0;
        left: 0;
        height: 100%;
        width: 100%;
        background: rgba(0,0,0,0.3);
        z-index: 50;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Sidebar content HTML
sidebar_class = "custom-sidebar" if st.session_state.sidebar_open else "custom-sidebar hidden"
sidebar_html = f"""
<div class="{sidebar_class}">
    <h2>Sidebar</h2>
    <p>Here is your sidebar content.</p>
    <button onclick="document.dispatchEvent(new Event('toggleSidebar'))">Close Sidebar</button>
</div>
"""

st.markdown(sidebar_html, unsafe_allow_html=True)

# Optional overlay for mobile click-to-close
if st.session_state.sidebar_open:
    st.markdown(
        """
        <div class="sidebar-overlay" onclick="document.dispatchEvent(new Event('toggleSidebar'))"></div>
        """,
        unsafe_allow_html=True,
    )

# Main content
st.title("Main Page")
st.write("Click the toggle button to open/close the sidebar.")
