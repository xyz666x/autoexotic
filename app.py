import streamlit as st

# Hide specific element by exact class
st.markdown(
    """
    <style>
    .st-emotion-cache-scp8yw.e3g0k5y6 {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.write("This element is hidden now.")
