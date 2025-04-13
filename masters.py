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

def normalize_name(name: str) -> str:
    return unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode().lower().strip()

def proper_case(name: str) -> str:
    return ' '.join(word.capitalize() for word in name.split())

def load_teams(user_id):
    try:
        doc_ref = db.collection("teams").document(user_id)
        doc = doc_ref.get()
        return doc.to_dict().get('teams', {}) if doc.exists and datetime.now(timezone.utc) < doc.to_dict().get('expiry', datetime.min) else {}
    except Exception as e:
        st.error(f"Database error: {str(e)}")
        return {}

def save_teams(user_id, teams):
    try:
        db.collection("teams").document(user_id).set({
            'teams': teams,
            'expiry': datetime.now(timezone.utc) + timedelta(days=2)
        })
        return True
    except Exception as e:
        st.error(f"Save failed: {str(e)}")
        return False

@st.cache_data(ttl=120)
def get_masters_scores():
    try:
        response = requests.get("https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard").json()
        scores = {}
        for event in response.get('events', []):
            for competition in event.get('competitions', []):
                for player in competition.get('competitors', []):
                    try:
                        raw_name = player['athlete']['displayName']
                        name = normalize_name(raw_name)
                        
                        # Get actual score
                        score = str(player.get('score', 'E')).strip()
                        actual_score = 0 if score == 'E' else int(score)
                        
                        # Accurate cut detection
                        position = player.get('position', {}).get('displayValue', '').lower()
                        status = player.get('status', {})
                        score_to_par = player.get('scoreToPar', 1000)
                        
                        # Check for explicit missed cut indicators
                        missed_cut = (
                            (score_to_par >= 1000) or  # ESPN's official cut marker
                            ('cut' in position and 'mdf' not in position) or
                            ('cut' in status.get('type', '').lower() and 'mdf' not in status.get('type', '').lower()) or
                            ('cut' in status.get('name', '').lower() and 'mdf' not in status.get('name', '').lower())
                        )
                        
                        scores[name] = {
                            'actual': actual_score,
                            'penalty': 10 if missed_cut else 0
                        }
                        
                    except Exception as e:
                        print(f"Error processing {raw_name}: {str(e)}")
        return scores
    except Exception as e:
        print(f"API Error: {str(e)}")
        return {}

def display_leaderboard(leaderboard):
    if leaderboard:
        leaderboard_df = pd.DataFrame(leaderboard).sort_values("Score", ascending=True).reset_index(drop=True)
        leaderboard_df.index += 1
        
        try:
            leaderboard_df['Score'] = pd.to_numeric(leaderboard_df['Score'], errors='coerce')
            min_score = leaderboard_df['Score'].min()
            max_score = leaderboard_df['Score'].max()
            
            if min_score == max_score:
                min_score -= 1
                max_score += 1

            styled_df = (
                leaderboard_df.style
                .background_gradient(cmap='RdYlGn_r', subset=["Score"], vmin=min_score, vmax=max_score)
                .format({"Score": lambda x: f"{x:+}", "Display Score": lambda x: f"{x:+}"})
                .hide(axis="index")
            )
            st.dataframe(styled_df, use_container_width=True)
        except Exception as e:
            st.dataframe(leaderboard_df[["Team", "Display Score", "Golfers"]], use_container_width=True)

def main():
    st.set_page_config(page_title="Masters Fantasy Golf Tracker", layout="wide")
    st_autorefresh(interval=120000, key="auto_refresh")
    
    user_id = get_user_session()
    st.session_state.teams = load_teams(user_id) if "teams" not in st.session_state else st.session_state.teams
    
    try:
        live_scores = get_masters_scores()
        if not live_scores:
            raise Exception("No scores received from API")
        
        # Debug output
        st.write("Player Status Verification:")
        sample_players = list(live_scores.items())[-3:] + [
            (normalize_name("Tiger Woods"), {'actual': 9, 'penalty': 10}),
            (normalize_name("Jordan Spieth"), {'actual': 8, 'penalty': 0})
        ]
        for name, data in sample_players:
            st.write(f"{proper_case(name)}: Actual {data['actual']:+}, Penalty {data['penalty']}")
            
    except Exception as e:
        st.error(f"Using fallback data: {str(e)}")
        live_scores = {
            normalize_name("Bryson DeChambeau"): {'actual': -7, 'penalty': 0},
            normalize_name("Scottie Scheffler"): {'actual': -5, 'penalty': 0},
            normalize_name("Tiger Woods (Missed Cut)"): {'actual': 9, 'penalty': 10},
            normalize_name("Jordan Spieth (MDF)"): {'actual': 8, 'penalty': 0}
        }

    leaderboard = []
    for team, golfers in st.session_state.teams.items():
        valid_golfers = [g for g in golfers if normalize_name(g) in live_scores]
        
        total_actual = sum(live_scores[normalize_name(g)]['actual'] for g in valid_golfers)
        total_penalty = sum(live_scores[normalize_name(g)]['penalty'] for g in valid_golfers)
        total_score = total_actual + total_penalty
        
        formatted_golfers = []
        for golfer in valid_golfers:
            data = live_scores[normalize_name(golfer)]
            display = f"{proper_case(golfer)} ({data['actual']:+})"
            if data['penalty'] > 0:
                display += " 🔴 (+10 cut penalty)"
            formatted_golfers.append(display)
        
        leaderboard.append({
            "Team": proper_case(team),
            "Score": total_score,
            "Display Score": total_actual,
            "Golfers": ", ".join(formatted_golfers)
        })

    st.title("🏌️♂️ Masters Fantasy Golf Tracker")
    st.header("📊 Fantasy Leaderboard")
    display_leaderboard(leaderboard)

    st.header("🏌️ Assign Golfers to Teams")
    valid_golfers = {k: v for k, v in live_scores.items()}
    for team, golfers in st.session_state.teams.items():
        with st.form(key=f"{team}_form"):
            current = [proper_case(g) for g in golfers if normalize_name(g) in valid_golfers]
            selected = st.multiselect(
                f"Select golfers for {team} (Max 4):",
                options=[proper_case(g) for g in valid_golfers.keys()],
                default=current,
                key=f"select_{team}",
                format_func=lambda x: f"{x} ({valid_golfers[normalize_name(x)]['actual']:+})"
            )
            if st.form_submit_button("Save Selections"):
                if len(selected) <= 4:
                    st.session_state.teams[team] = [normalize_name(g) for g in selected]
                    save_teams(user_id, st.session_state.teams)
                else:
                    st.error("Maximum 4 golfers per team!")

    with st.sidebar:
        st.header("👥 Manage Teams")
        if st.button("🚪 Log Out"):
            del st.session_state.user_id
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
