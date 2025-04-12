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

# Authentication functions
def authenticate_user(email, password):
    try:
        user = auth.get_user_by_email(email)
        return user.uid
    except auth.UserNotFoundError:
        st.error("User not found")
    except auth.AuthError:
        st.error("Authentication failed")
    return None

# User session management
def get_user_session():
    if 'user_id' not in st.session_state:
        with st.container():
            st.header("🔒 Login / Register")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Sign In"):
                    user_id = authenticate_user(email, password)
                    if user_id:
                        st.session_state.user_id = user_id
                        st.rerun()
            with col2:
                if st.button("Create Account"):
                    try:
                        user = auth.create_user(email=email)
                        st.success("Account created! Please sign in.")
                    except Exception as e:
                        st.error(f"Creation failed: {str(e)}")
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

# Verified score extraction from working version
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
                        
                        # Working score extraction method
                        score = str(player.get('score', 'E')).strip()
                        if score == 'E':
                            score_val = 0
                        else:
                            try:
                                score_val = int(score)
                            except ValueError:
                                score_val = 0
                                
                        scores[name] = score_val
                        
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
            styled_df = leaderboard_df.style.background_gradient(
                cmap="viridis", 
                subset=["Score"]
            )
            st.dataframe(styled_df.hide(axis="index"), use_container_width=True)
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
            
            if len(valid_defaults) != len(golfers):
                st.session_state.teams[team] = valid_defaults
                save_teams(user_id, st.session_state.teams)
                st.rerun()
            
            selected_golfers = st.multiselect(
                f"Select golfers for {team} (Max 4):",
                options=[proper_case(g) for g in valid_golfers.keys()],
                default=[proper_case(g) for g in valid_defaults],
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
