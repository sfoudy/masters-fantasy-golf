import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests
from datetime import datetime, timedelta, timezone
import pandas as pd
import unicodedata
import firebase_admin
from firebase_admin import credentials, firestore, auth

DG_API_KEY = "45323a83fa8d25b3c3d745bd63d9"  # Replace with your DataGolf API key


# Initialize Firebase
if not firebase_admin._apps:
    try:
        firebase_config = dict(st.secrets["firebase"])
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Firebase initialization error: {str(e)}")
        st.stop()

db = firestore.client()
FIREBASE_WEB_API_KEY = st.secrets["firebase_auth"]["web_api_key"]

def normalize_name(name: str) -> str:
    import re
    return re.sub(r'[^a-z]', '', str(name).lower())


def send_password_reset_email(email: str):
    try:
        response = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}",
            json={"requestType": "PASSWORD_RESET", "email": email}
        )
        if response.status_code == 200:
            st.success("Password reset email sent! Check your inbox.")
        else:
            st.error("Failed to send reset email. Check if email is registered.")
    except Exception as e:
        st.error(f"Error sending reset email: {str(e)}")

def create_user(email: str, password: str):
    try:
        user = auth.create_user(email=email, password=password)
        return user.uid
    except Exception as e:
        st.error(f"Account creation failed: {str(e)}")
        return None

def authenticate_user(email: str, password: str):
    try:
        response = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}",
            json={"email": email, "password": password, "returnSecureToken": True}
        )
        return response.json()['localId'] if response.status_code == 200 else None
    except Exception as e:
        st.error(f"Authentication failed: {str(e)}")
        return None

def get_user_session():
    if 'user_id' not in st.session_state:
        with st.container():
            st.header("🔒 Login/Register")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            
            with st.expander("Forgot Password?"):
                reset_email = st.text_input("Enter email to reset password", key="reset_email")
                if st.button("Send Reset Link") and "@" in reset_email and "." in reset_email:
                    send_password_reset_email(reset_email)
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Sign In") and email and password:
                    if user_id := authenticate_user(email, password):
                        st.session_state.user_id = user_id
                        st.rerun()
            with col2:
                if st.button("Create Account") and email and password:
                    if len(password) < 8:
                        st.error("Password must be ≥8 characters")
                    elif user_id := create_user(email, password):
                        st.success("Account created! Please sign in.")
            st.stop()
    return st.session_state.user_id



def proper_case(name: str) -> str:
    return ' '.join(word.capitalize() for word in name.split())

def load_teams(user_id):
    try:
        doc_ref = db.collection("teams").document(user_id)
        doc = doc_ref.get()
        return doc.to_dict().get('teams', {}) if doc.exists else {}
    except Exception as e:
        st.error(f"Database error: {str(e)}")
        return {}

def save_teams(user_id, teams):
    try:
        db.collection("teams").document(user_id).set({
            'teams': teams,
            'expiry': datetime.now(timezone.utc) + timedelta(days=7)
        })
        return True
    except Exception as e:
        st.error(f"Save failed: {str(e)}")
        return False

@st.cache_data(ttl=300)
def get_datagolf_live_model():
    url = "https://feeds.datagolf.com/preds/in-play"
    params = {
        "tour": "pga",
        "file_format": "json",
        "key": DG_API_KEY
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    return data


def normalize_name(name):
    import re
    return re.sub(r'[^a-z]', '', str(name).lower())

def get_scores_from_field_df(field_df):
    scores = {}
    for _, row in field_df.iterrows():
        name = normalize_name(row['player_name'])
        status = row.get('status', '').lower()
        score_str = str(row.get('score', 'E')).strip()
        # Handle different statuses
        if status in ['wd', 'dq']:
            actual_score = 0
            penalty = 10
        elif status == 'cut':
            actual_score = 0
            penalty = 10
        else:
            try:
                actual_score = int(score_str) if score_str not in ['E', ''] else 0
                penalty = 0
            except ValueError:
                actual_score = 0
                penalty = 0
        scores[name] = {
            'actual': actual_score,
            'penalty': penalty,
            'display': score_str if score_str else 'E'
        }
    return scores



def display_leaderboard(leaderboard):
    if leaderboard:
        leaderboard_df = pd.DataFrame(leaderboard).sort_values("Score", ascending=True)
        leaderboard_df.index += 1
        
        try:
            leaderboard_df['Score'] = pd.to_numeric(leaderboard_df['Score'])
            leaderboard_df['Display Score (No Penalty)'] = pd.to_numeric(leaderboard_df['Display Score (No Penalty)'])
            
            min_score = leaderboard_df['Score'].min()
            max_score = leaderboard_df['Score'].max()
            
            styled_df = (
                leaderboard_df.style
                .background_gradient(cmap='RdYlGn_r', subset=["Score"], vmin=min_score-1, vmax=max_score+1)
                .format({
                    "Score": lambda x: f"{x:+}",
                    "Display Score (No Penalty)": lambda x: f"{x:+}"
                })
            )
            st.dataframe(styled_df, use_container_width=True)
        except Exception as e:
            st.dataframe(leaderboard_df)

def main():
    st.set_page_config(page_title="PGA Championship Fantasy Golf Tracker", layout="wide")
    st.title("🏌️ PGA Championship Fantasy Golf Tracker")

    st_autorefresh(interval=300000, key="auto_refresh")
    
    user_id = get_user_session()

    # Ensure teams is initialized in session state
    if 'teams' not in st.session_state:
        st.session_state.teams = {}

    # Load teams for current user
    try:
        st.session_state.teams = load_teams(user_id)
    except Exception:
        st.session_state.teams = {}

    # Defensive: always define these
    live_scores = {}
    field_df = pd.DataFrame()

    # Fetch live model data
    try:
        live_model_data = get_datagolf_live_model()
        st.write(live_model_data) 
        players = live_model_data["players"]

        # Build normalized name -> player data lookup
        for player_id, pdata in players.items():
            name = pdata["display_name"]
            norm_name = normalize_name(name)
            live_scores[norm_name] = pdata

        if not live_scores:
            raise Exception("No scores received from API")
    except Exception as e:
        st.error(f"Using fallback data: {str(e)}")
        # live_scores and field_df are already empty

    # Build mapping: normalized name -> Proper Case Name (if you have field_df)
    name_map = {}
    if not field_df.empty:
        for _, row in field_df.iterrows():
            norm = normalize_name(row['player_name'])
            name_map[norm] = proper_case(row['player_name'])
    else:
        # fallback: build name_map from live_scores
        for norm, pdata in live_scores.items():
            name_map[norm] = pdata["display_name"]

    st.header("🏌️ Assign Golfers to Teams")
    valid_golfers = {k: v for k, v in live_scores.items()}
    reverse_name_map = {v: k for k, v in name_map.items()}

    # Team selection forms
    for team, golfers in st.session_state.teams.items():
        with st.form(key=f"{team}_form"):
            current = [name_map[g] for g in golfers if g in name_map]
            options = [name_map[g] for g in valid_golfers.keys() if g in name_map]
            selected = st.multiselect(
                f"Select golfers for {team} (Max 4):",
                options=options,
                default=current,
                format_func=lambda x: f"{x} ({valid_golfers[reverse_name_map[x]]['score']:+})" if reverse_name_map.get(x) in valid_golfers else x
            )

            save_disabled = len(selected) > 4
            if save_disabled:
                st.warning("You can select a maximum of 4 golfers.")

            if st.form_submit_button("Save Selections", disabled=save_disabled):
                # Store normalized names for consistency
                st.session_state.teams[team] = [reverse_name_map[g] for g in selected]
                save_teams(user_id, st.session_state.teams)

    # --- Leaderboard Section ---
    st.header("📊 Fantasy Leaderboard")
    leaderboard = []

    for team, golfers in st.session_state.teams.items():
        total_score = 0
        formatted_golfers = []

        for golfer in golfers:
            norm_name = normalize_name(golfer)
            if norm_name in live_scores:
                pdata = live_scores[norm_name]
                name = pdata["display_name"]
                score = pdata["score"]
                status = pdata["status"]

                # 10-shot penalty for missed cut
                if status == "mc":
                    total_score += 10
                    formatted_golfers.append(f"{name}: +10 (MC, actual: {score:+})")
                else:
                    total_score += score
                    formatted_golfers.append(f"{name}: {score:+}")
            else:
                formatted_golfers.append(f"{golfer}: No score found")

        leaderboard.append({
            "Team": team,
            "Score": total_score,
            "Golfers": ", ".join(formatted_golfers)
        })

    display_leaderboard(leaderboard)

    # --- Sidebar ---
    with st.sidebar:
        st.header("👥 Manage Teams")
        if st.button("🚪 Log Out"):
            keys_to_remove = ['user_id', 'teams']
            for key in keys_to_remove:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
        
        new_team = st.text_input("Create New Team:")
        if st.button("Add Team") and new_team.strip():
            st.session_state.teams[new_team] = []
            save_teams(user_id, st.session_state.teams)
        
        if st.session_state.teams:
            del_team = st.selectbox("Select team to remove:", [proper_case(t) for t in st.session_state.teams])
            if st.button("Remove Team"):
                original = [t for t in st.session_state.teams if proper_case(t) == del_team][0]
                del st.session_state.teams[original]
                save_teams(user_id, st.session_state.teams)

    st.caption(f"Last update: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")



if __name__ == "__main__":
    main()
