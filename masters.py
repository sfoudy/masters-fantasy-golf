import streamlit as st

def get_user_session():
    # Simulate a successful login for testing
    if 'user_id' not in st.session_state:
        st.session_state.user_id = "testuser"
    return st.session_state.user_id

def main():
    st.title("Test Penalty Mode Selector")
    user_id = get_user_session()
    st.write(f"Debug: User ID = {user_id}")

    penalty_mode = st.radio(
        "How should the missed cut penalty be applied?",
        [
            "Add 10 to actual score (total score + 10)",
            "Replace score with 10 (ignore actual score)"
        ],
        key="penalty_mode"
    )
    st.write("Selected penalty mode:", penalty_mode)

if __name__ == "__main__":
    main()
