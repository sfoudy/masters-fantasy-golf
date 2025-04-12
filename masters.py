import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests
from datetime import datetime
import pandas as pd
import json
import os

DATA_FILE = "teams_data.json"

def normalize_name(name: str) -> str:
    return name.strip().lower()

def proper_case(name: str) -> str:
    return ' '.join(word.capitalize() for word in name.split())

@st.cache_data(ttl=120)
def get_espn_scores():
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
                        
                        # CORRECTED: Use direct score extraction from search results
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

@st.cache_data
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

st.set_page_config(
    page_title="Masters Fantasy Golf Tracker",
    layout="wide",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": None,
    },
)

st_autorefresh(interval=2 * 60 * 1000, key="auto_refresh")

st.title("🏌️‍♂️ Masters Fantasy Golf Tracker")
st.caption("Track your fantasy golf leaderboard live!")

if "teams" not in st.session_state:
    st.session_state.teams = load_data()

live_scores = get_espn_scores()

if not live_scores:
    st.error("Using fallback data - API fetch failed")
    live_scores = {
        normalize_name("Bryson DeChambeau"): -7,
        normalize_name("Scottie Scheffler"): -5,
        normalize_name("Ludvig Åberg"): -4
    }

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

st.header("📊 Fantasy Leaderboard")
if leaderboard:
    leaderboard_df = (
        pd.DataFrame(leaderboard)
        .sort_values("Score", ascending=True)
        .reset_index(drop=True)
    )
    leaderboard_df.index += 1
    
    try:
        styled_df = leaderboard_df.style.background_gradient(cmap="viridis", subset=["Score"])
        st.dataframe(styled_df.hide(axis="index"), use_container_width=True)
    except Exception as e:
        st.dataframe(leaderboard_df[["Team", "Display Score", "Golfers"]], use_container_width=True)
else:
    st.warning("No teams or golfers assigned yet!")

st.header("🏌️ Assign Golfers to Teams")
valid_golfers = {k: v for k, v in live_scores.items()}

for team, golfers in st.session_state.teams.items():
    with st.form(key=f"{team}_form"):
        valid_defaults = [g for g in golfers if normalize_name(g) in valid_golfers]
        
        if len(valid_defaults) != len(golfers):
            st.session_state.teams[team] = valid_defaults
            save_data(st.session_state.teams)
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
                save_data(st.session_state.teams)

with st.sidebar:
    st.header("👥 Manage Teams")
    new_team = st.text_input("Create New Team:")
    if st.button("Add Team") and new_team:
        if proper_case(new_team) not in [proper_case(t) for t in st.session_state.teams.keys()]:
            st.session_state.teams[new_team] = []
            save_data(st.session_state.teams)
    
    if st.session_state.teams:
        del_team = st.selectbox("Select team to remove:", 
                              [proper_case(team) for team in st.session_state.teams.keys()])
        if st.button("Remove Team"):
            original_case_team = [team for team in st.session_state.teams.keys() 
                                if proper_case(team) == del_team][0]
            del st.session_state.teams[original_case_team]
            save_data(st.session_state.teams)

st.caption(f"Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
