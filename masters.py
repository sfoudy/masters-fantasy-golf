import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests
from datetime import datetime, timedelta, timezone
import pandas as pd
import unicodedata
import firebase_admin
from firebase_admin import credentials, firestore, auth

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

# Authentication functions
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
        user = auth.create_user(
            email=email,
            password=password
        )
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
        if response.status_code == 200:
            return response.json()['localId']
        st.error("Invalid email or password")
        return None
    except Exception as e:
        st.error(f"Authentication failed: {str(e)}")
        return None

# User session management
def get_user_session():
    if 'user_id' not in st.session_state:
        with st.container():
            st.header("🔒 Login / Register")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            
            with st.expander("Forgot Password?"):
                reset_email = st.text_input("Enter your email to reset password", key="reset_email")
                if st.button("Send Reset Link"):
                    if "@" in reset_email and "." in reset_email:
                        send_password_reset_email(reset_email)
                    else:
                        st.error("Please enter a valid email address")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Sign In"):
                    if email and password:
                        user_id = authenticate_user(email, password)
                        if user_id:
                            st.session_state.user_id = user_id
                            st.rerun()
            with col2:
                if st.button("Create Account"):
                    if email and password:
                        if len(password) < 8:
                            st.error("Password must be at least 8 characters")
                        else:
                            user_id = create_user(email, password)
                            if user_id:
                                st.success("Account created! Please sign in.")
            st.stop()
    return st.session_state.user_id

# Helper functions
def normalize_name(name: str) -> str:
    return name.strip().lower()

def proper_case(name: str) -> str:
    return ' '.join(word.capitalize() for word in name.split())

# Data operations
def load_teams(user_id):
    try:
        doc_ref = db.collection("teams").document(user_id)
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            if datetime.now(timezone.utc) > data['expiry']:
                doc_ref.delete()
                return {}
            return data.get('teams', {})
        return {}
    except Exception as e:
        st.error(f"Database error: {str(e)}")
        return {}

def save_teams(user_id, teams):
    try:
        expiry = datetime.now(timezone.utc) + timedelta(days=2)
        doc_ref = db.collection("teams").document(user_id)
        doc_ref.set({
            'teams': teams,
            'expiry': expiry
        })
        return True
    except Exception as e:
        st.error(f"Save failed: {str(e)}")
        return False

# Verified score extraction
@st.cache_data(ttl=120)
def get_masters_scores():
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
        response = requests.get(url).json()
        
        scores = {}
        for event in response.get('events', []):
            for competition in event.get('competitions', []):
                for player in competition.get('competitors', []):
                    try:
                        raw_name = player['athlete']['displayName']
                        name = normalize_name(raw_name)
                        score = str(player.get('score', 'E')).strip()
                        scores[name] = int(score) if score.replace('E', '0').isdigit() else 0
                    except Exception as e:
                        st.warning(f"Error processing {raw_name}: {str(e)}")
        return scores
    except Exception as e:
        st.error(f"API Error: {str(e)}")
        return {}

# Streamlit app
def main():
    st.set_page_config(
        page_title="Masters Fantasy Golf Tracker",
        layout="wide",
        menu_items={
            "Get Help": None,
            "Report a bug": None,
            "About": None,
        }
    )
    
    st_autorefresh(interval=2 * 60 * 1000, key="auto_refresh")
    
    # Authentication
    user_id = get_user_session()
    
    # Initialize teams
    if "teams" not in st.session_state:
        st.session_state.teams = load_teams(user_id)

    # Load scores
    live_scores = get_masters_scores() or {
        normalize_name("Bryson DeChambeau"): -7,
        normalize_name("Scottie Scheffler"): -5,
        normalize_name("Ludvig Åberg"): -4
    }

    # Leaderboard calculation
    leaderboard = []
    for team, golfers in st.session_state.teams.items():
        valid_golfers = [g for g in golfers if normalize_name(g) in live_scores]
        total_score = sum(live_scores[normalize_name(g)] for g in valid_golfers)
        
        formatted_golfers = [
            f"{proper_case(g)} ({live_scores[normalize_name(g)]:+})" 
            for g in valid_golfers
        ]
        
        leaderboard.append({
            "Team": proper_case(team),
            "Score": total_score,
            "Display Score": f"{total_score:+}" if total_score != 0 else "E",
            "Golfers": ", ".join(formatted_golfers)
        })

    # Display interface
    st.title("🏌️‍♂️ Masters Fantasy Golf Tracker")
    st.header("📊 Fantasy Leaderboard")
    
    if leaderboard:
        leaderboard_df = (
            pd.DataFrame(leaderboard)
            .sort_values("Score", ascending=True)
            .reset_index(drop=True)
        )
        leaderboard_df.index += 1
        
        try:
            # Position-based coloring
            leaderboard_df['Rank'] = leaderboard_df.index
            styled_df = (
                leaderboard_df.style
                .background_gradient(
                    cmap='RdYlGn_r',
                    subset=["Rank"],
                    vmin=1,
                    vmax=len(leaderboard_df)
                )
                .set_properties(**{
                    'color': 'white',
                    'border': '1px solid grey',
                    'background-color': 'black'
                }, subset=["Score"])
                .format({"Score": lambda x: f"{x:+}"})
                .hide(axis="index")
                .hide(columns=["Rank"])
            )
            st.dataframe(styled_df, use_container_width=True)
        except Exception as e:
            st.dataframe(leaderboard_df[["Team", "Display Score", "Golfers"]], 
                       use_container_width=True)
    else:
        st.warning("No teams or golfers assigned yet!")

    # Team management
    st.header("🏌️ Assign Golfers to Teams")
    valid_golfers = {k: v for k, v in live_scores.items()}

    for team, golfers in st.session_state.teams.items():
        with st.form(key=f"{team}_form"):
            valid_defaults = [g for g in golfers if normalize_name(g) in valid_golfers]
            
            # Process defaults to match option format
            options = [proper_case(g) for g in valid_golfers.keys()]
            processed_defaults = [
                proper_case(g) for g in valid_defaults 
                if proper_case(g) in options
            ]
            
            selected_golfers = st.multiselect(
                f"Select golfers for {team} (Max 4):",
                options=options,
                default=processed_defaults,
                key=f"select_{team}",
                format_func=lambda x: f"{x} ({valid_golfers[normalize_name(x)]:+})"
            )
            
            if st.form_submit_button("Save Selections"):
                normalized_selected = [normalize_name(g) for g in selected_golfers]
                if len(normalized_selected) > 4:
                    st.error("Maximum 4 golfers per team!")
                else:
                    st.session_state.teams[team] = normalized_selected
                    if save_teams(user_id, st.session_state.teams):
                        st.success("Selections saved!")

    # Sidebar controls
    with st.sidebar:
        st.header("👥 Manage Teams")
        
        if st.button("🚪 Log Out"):
            del st.session_state.user_id
            st.rerun()
        
        new_team = st.text_input("Create New Team:")
        if st.button("Add Team") and new_team:
            if new_team.strip() and proper_case(new_team) not in [proper_case(t) for t in st.session_state.teams]:
                st.session_state.teams[new_team] = []
                if save_teams(user_id, st.session_state.teams):
                    st.success(f"Team '{new_team}' created!")
        
        if st.session_state.teams:
            del_team = st.selectbox("Select team to remove:", 
                                  [proper_case(team) for team in st.session_state.teams])
            if st.button("Remove Team"):
                original_case_team = [team for team in st.session_state.teams 
                                    if proper_case(team) == del_team][0]
                del st.session_state.teams[original_case_team]
                if save_teams(user_id, st.session_state.teams):
                    st.success(f"Team '{original_case_team}' removed!")

    st.caption(f"Last update: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

if __name__ == "__main__":
    main()
