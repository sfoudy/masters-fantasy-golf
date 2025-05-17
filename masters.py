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

def get_text_color(bg_hex):
    # Convert hex to RGB
    bg_hex = bg_hex.lstrip('#')
    r, g, b = tuple(int(bg_hex[i:i+2], 16) for i in (0, 2, 4))
    # Calculate luminance
    luminance = (0.299*r + 0.587*g + 0.114*b)/255
    # If luminance is high, use black text; else use white
    return 'black' if luminance > 0.5 else 'white'



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
        # Try to auto-login if credentials/token are stored
        if 'firebase_id_token' in st.session_state:
            try:
                decoded_token = auth.verify_id_token(st.session_state.firebase_id_token)
                st.session_state.user_id = decoded_token['uid']
                return st.session_state.user_id
            except Exception as e:
                if 'firebase_id_token' in st.session_state:
                    del st.session_state['firebase_id_token']

        # Show login form if no valid token exists
        with st.container():
            st.header("🔒 Login/Register")
            # Pre-fill email if previously saved in session_state
            default_email = st.session_state.get('saved_email', '')
            # Use autocomplete attributes for browser autofill
            email = st.text_input("Email", value=default_email, autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            
            with st.expander("Forgot Password?"):
                reset_email = st.text_input("Enter email to reset password", key="reset_email")
                if st.button("Send Reset Link") and "@" in reset_email and "." in reset_email:
                    send_password_reset_email(reset_email)
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Sign In") and email and password:
                    response = requests.post(
                        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}",
                        json={"email": email, "password": password, "returnSecureToken": True}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        st.session_state.user_id = data['localId']
                        st.session_state.firebase_id_token = data['idToken']
                        st.session_state.saved_email = email  # Save email for autofill
                        st.rerun()
                    else:
                        st.error("Invalid email or password.")
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
        "dead_heat": "no",
        "odds_format": "percent",
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
    import matplotlib.colors as mcolors

    st.set_page_config(page_title="PGA Championship Fantasy Golf Tracker", layout="wide")
    st.title("🏌️ PGA Championship Fantasy Golf Tracker")

    st_autorefresh(interval=60000, key="auto_refresh")
    
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

    # Fetch live model data (free API: use "data" key)
    try:
        live_model_data = get_datagolf_live_model()
        players = live_model_data["data"]  # <-- Use this for the free API

        # Get current round from API
        current_round = live_model_data.get("info", {}).get("current_round", 1)

        # Build normalized name -> player data lookup
        live_scores = {}
        for pdata in players:
            name = pdata["player_name"]  # e.g., "Scheffler, Scottie"
            parts = name.split(", ")
            if len(parts) == 2:
                full_name = f"{parts[1]} {parts[0]}"
            else:
                full_name = name
            norm_name = normalize_name(full_name)
            live_scores[norm_name] = pdata

        if not live_scores:
            raise Exception("No scores received from API")
    except Exception as e:
        st.error(f"Using fallback data: {str(e)}")
        current_round = 1  # fallback
        # live_scores and field_df are already empty

    # --- Penalty Selection Widget ---
    penalty_mode = st.radio(
        "How should the missed cut penalty be applied?",
        [
            "Add 10 to actual score (total score + 10)",
            "Replace score with 10 (ignore actual score)"
        ]
    )

    # Build mapping: normalized name -> Proper Case Name
    name_map = {}
    for norm, pdata in live_scores.items():
        # Use "First Last" for display
        name_map[norm] = pdata["player_name"]
    reverse_name_map = {v: k for k, v in name_map.items()}

    # --- Leaderboard Section ---
    st.header("📊 Fantasy Leaderboard")

    # Manual refresh button
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    leaderboard = []

    for team, golfers in st.session_state.teams.items():
        total_score = 0
        display_score_no_penalty = 0
        formatted_golfers = []

        for golfer in golfers:
            norm_name = normalize_name(golfer)
            if norm_name in live_scores:
                pdata = live_scores[norm_name]
                name = pdata["player_name"]
                score = pdata.get("current_score", 0)
                make_cut = pdata.get("make_cut", 1)
                player_round = pdata.get("round", 0)
                current_pos = pdata.get("current_pos", "")

                # Penalty logic: apply as soon as player is CUT after round 2
                missed_cut = (make_cut == 0 and player_round >= 2 and current_pos.upper() == "CUT")
                if missed_cut:
                    if penalty_mode == "Add 10 to actual score (total score + 10)":
                        penalized_score = score + 10
                    else:  # "Replace score with 10 (ignore actual score)"
                        penalized_score = 10
                    total_score += penalized_score
                    formatted_golfers.append(f"{name} (MC): +10 (actual: {score:+})")
                else:
                    total_score += score
                    formatted_golfers.append(f"{name}: {score:+}")
                display_score_no_penalty += score
            else:
                formatted_golfers.append(f"{golfer}: No score found")

        leaderboard.append({
            "Team": team,
            "Score": total_score,
            "Display Score (No Penalty)": display_score_no_penalty,
            "Golfers": ", ".join(formatted_golfers)
        })

    # Create DataFrame and add leaderboard position
    df = pd.DataFrame(leaderboard)
    df = df.sort_values("Score", ascending=True).reset_index(drop=True)
    df.index += 1  # Start positions at 1
    df.insert(0, "Position", df.index)

    # Format the Score and Display Score columns with + sign for positive/zero
    def plus_format(x):
        return f"+{x}" if x >= 0 else f"{x}"

    # Save numeric scores for coloring, but don't show this column
    df["Score_numeric"] = df["Score"]
    df["Score"] = df["Score"].apply(plus_format)
    df["Display Score (No Penalty)"] = df["Display Score (No Penalty)"].apply(plus_format)

    # Create a custom diverging colormap from green (-20) to white (0) to dark red (+20)
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "custom_cmap",
        [(0, "green"), (0.5, "white"), (1, "darkred")]
    )
    norm = mcolors.Normalize(vmin=-20, vmax=20)

    def color_score(val):
        rgba = cmap(norm(val))
        bg_hex = mcolors.to_hex(rgba)
        text_color = get_text_color(bg_hex)
        return f"background-color: {bg_hex}; color: {text_color};"

    # Apply the style to the Score column using Score_numeric for coloring
    styled_df = df.style.apply(
        lambda s: [color_score(v) for v in df["Score_numeric"]], subset=["Score"]
    ).format({
        "Score": lambda x: x,
        "Display Score (No Penalty)": lambda x: x
    })

    # Display only the desired columns (hide Score_numeric)
    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        column_order=["Position", "Team", "Score", "Display Score (No Penalty)", "Golfers"]
    )

    # --- Player Selection Section ---
    st.header("🏌️ Assign Golfers to Teams")
    valid_golfers = {k: v for k, v in live_scores.items()}

    for team, golfers in st.session_state.teams.items():
        with st.form(key=f"{team}_form"):
            current = [name_map[g] for g in golfers if g in name_map]
            options = [name_map[g] for g in valid_golfers.keys() if g in name_map]
            selected = st.multiselect(
                f"Select golfers for {team} (Max 4):",
                options=options,
                default=current,
                format_func=lambda x: f"{x} ({valid_golfers[reverse_name_map[x]]['current_score']:+})" if reverse_name_map.get(x) in valid_golfers else x
            )

            save_disabled = len(selected) > 4
            if save_disabled:
                st.warning("You can select a maximum of 4 golfers.")

            if st.form_submit_button("Save Selections", disabled=save_disabled):
                # Store normalized names for consistency
                st.session_state.teams[team] = [reverse_name_map[g] for g in selected]
                save_teams(user_id, st.session_state.teams)

    # --- Sidebar ---
    with st.sidebar:
        st.header("👥 Manage Teams")
        if st.button("🚪 Log Out"):
            keys_to_remove = ['user_id', 'teams', 'firebase_id_token']
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
