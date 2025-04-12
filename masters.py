import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests
from datetime import datetime, timedelta, timezone
import pandas as pd
import unicodedata
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase with timezone awareness
if not firebase_admin._apps:
    try:
        firebase_config = dict(st.secrets["firebase"])
        cred = credentials.Certificate(firebase_config)
    except Exception as e:
        st.error(f"Firebase config error: {str(e)}")
        cred = credentials.ApplicationDefault()
    
    try:
        firebase_admin.initialize_app(cred, {
            'projectId': 'mastersscore-2c73b',
        })
    except Exception as e:
        st.error(f"Firebase initialization failed: {str(e)}")

db = firestore.client()

# Helper functions
def normalize_name(name: str) -> str:
    return unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode().lower().strip()

def get_user_id():
    """Generate persistent user ID using UTC timestamps"""
    if 'user_id' not in st.session_state:
        user_id = st.query_params.get("user_id", None)
        if not user_id:
            user_id = f"user_{datetime.now(timezone.utc).timestamp()}"
            st.query_params["user_id"] = user_id
        st.session_state.user_id = user_id
    return st.session_state.user_id

# Data operations with UTC timezone
def load_teams(user_id):
    try:
        doc_ref = db.collection("teams").document(user_id)
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            current_time = datetime.now(timezone.utc)
            if current_time > data['expiry']:
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

# Score fetching with totalToPar
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
                        
                        # Get total tournament score
                        total_score = player.get('totalToPar', player.get('scoreToPar', 'E'))
                        
                        # Convert to integer
                        if isinstance(total_score, str):
                            total_score = total_score.replace("E", "0").strip()
                            total_score = int(total_score) if total_score else 0
                        else:
                            total_score = int(total_score)
                            
                        scores[name] = total_score
                        
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
    
    st.title("🏌️‍♂️ Masters Fantasy Golf Tracker")
    st.caption("Track your fantasy golf leaderboard live!")

    # User session management
    user_id = get_user_id()
    
    # Initialize teams
    if "teams" not in st.session_state:
        st.session_state.teams = load_teams(user_id)

    # Load scores
    live_scores = get_masters_scores() or {
        normalize_name("Scottie Scheffler"): -7,
        normalize_name("Rory McIlroy"): -3
    }

    # Leaderboard calculation
    leaderboard = []
    for team, golfers in st.session_state.teams.items():
        normalized_golfers = [normalize_name(g) for g in golfers]
        total_score = sum(live_scores.get(g, 0) for g in normalized_golfers)
        
        formatted_golfers = []
        for golfer in golfers:
            normalized = normalize_name(golfer)
            score = live_scores.get(normalized, 0)
            formatted = f"{score:+}"  # Always show +/- notation
            formatted_golfers.append(f"{golfer} ({formatted})")
        
        leaderboard.append({
            "Team": team,
            "Score": total_score,
            "Display Score": f"{total_score:+}",
            "Golfers": ", ".join(formatted_golfers)
        })

    # Display leaderboard
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

    # Team management interface
    st.header("🏌️ Assign Golfers to Teams")
    original_names = {normalize_name(k): k for k in live_scores.keys()}

    for team, golfers in st.session_state.teams.items():
        with st.form(key=f"{team}_form"):
            current_selection = [original_names.get(normalize_name(g), g) for g in golfers]
            
            selected_golfers = st.multiselect(
                f"Select golfers for {team} (Max 4):",
                options=list(original_names.values()),
                default=current_selection,
                key=f"select_{team}",
                format_func=lambda x: f"{x} ({live_scores[normalize_name(x)]:+})"
            )
            
            if st.form_submit_button("Save Selections"):
                normalized_selected = [normalize_name(g) for g in selected_golfers]
                if len(normalized_selected) > 4:
                    st.error("Maximum 4 golfers per team!")
                else:
                    st.session_state.teams[team] = [original_names[g] for g in normalized_selected if g in original_names]
                    if save_teams(user_id, st.session_state.teams):
                        st.success("Selections saved!")

    # Sidebar management
    with st.sidebar:
        st.header("👥 Manage Teams")
        new_team = st.text_input("Create New Team:")
        if st.button("Add Team") and new_team:
            if new_team.strip() and new_team not in st.session_state.teams:
                st.session_state.teams[new_team] = []
                if save_teams(user_id, st.session_state.teams):
                    st.success(f"Team '{new_team}' created!")
        
        if st.session_state.teams:
            del_team = st.selectbox("Select team to remove:", 
                                  list(st.session_state.teams.keys()))
            if st.button("Remove Team"):
                del st.session_state.teams[del_team]
                if save_teams(user_id, st.session_state.teams):
                    st.success(f"Team '{del_team}' removed!")

    st.caption(f"Last update: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

if __name__ == "__main__":
    main()
