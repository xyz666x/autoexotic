import streamlit as st

# Hide the default sidebar collapse arrow (desktop only)
st.markdown(
    """
    <style>
    @media (min-width: 768px){
        [data-testid="collapsed-control"] { 
            display: none !important; 
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Sidebar content
with st.sidebar:
    st.header("Sidebar")
    st.write("This sidebar will always stay visible.")
    st.button("Sidebar Button 1")
    st.button("Sidebar Button 2")

# Main content
st.title("Main Page")
st.write("The default sidebar cannot be collapsed now, so it always appears after refresh.")
