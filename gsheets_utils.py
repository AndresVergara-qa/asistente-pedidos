import streamlit as st
from streamlit_gsheets import GSheetsConnection


@st.cache_resource
def get_gsheets_connection():
    return st.connection("gsheets", type=GSheetsConnection)
