import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import time

# --- 1. VERIFIED CONFIG & AUTH ---
USER, PASS = "andyhunt196@gmail.com", "NXo3j6z8zusIAiVG"
SK = 11234  

st.set_page_config(page_title="AUS 26 | MASTER", layout="wide", initial_sidebar_state="collapsed")

if 'token' not in st.session_state:
    try:
        r = requests.post("https://api.openf1.org/token", data={"username":USER,"password":PASS}, timeout=5)
        st.session_state.token = r.json().get("access_token")
    except:
        st.error("📡 AUTHENTICATION TIMEOUT - CHECK CONNECTION")
        st.stop()

cookies = {"f1_access_token": st.session_state.token}

# --- 2. IPAD PRO OPTIMIZED CSS (FROM SOURCE) ---
st.markdown("""
    <style>
    .stApp { background-color: #06060a; color: #e0e0e0; font-family: 'Inter', sans-serif; }
    .top-banner { padding: 14px; text-align: center; font-weight: 900; font-size: 16px; border-radius: 10px; margin-bottom: 20px; text-transform: uppercase; letter-spacing: 1px; }
    .banner-green { background: linear-gradient(90deg, #00d21d, #008a13); color: black; box-shadow: 0 4px 15px rgba(0, 210, 29, 0.3); }
    .banner-yellow { background: linear-gradient(90deg, #ffff00, #b8b800); color: black; box-shadow: 0 4px 15px rgba(255, 255, 0, 0.3); }
    .banner-red { background: linear-gradient(90deg, #ff0000, #a10000); color: white; box-shadow: 0 4px 15px rgba(255, 0, 0, 0.3); }
    .tower-row { border-bottom: 1px solid #1c1c2b; padding: 15px 5px; display: flex; align-items: center; }
    .team-bar { width: 6px; height: 38px; border-radius: 3px; margin-right: 20px; }
    .aero-badge { background: #00ff41; color: black; font-size: 10px; font-weight: 900; padding: 3px 8px; border-radius: 4px; margin-left: 12px; }
    .aero-z { background: #ffea00; }
    .boost-badge { background: #ff00ff; color: white; font-size: 10px; font-weight: 900; padding: 3px 8px; border-radius: 4px; margin-left: 8px; }
    .gap-val { font-family: 'JetBrains Mono', 'Courier New', monospace; color: #00ff41; font-weight: 800; margin-left: auto; font-size: 22px; }
    .stale-pit { color: #666 !important; font-style: italic; font-weight: 400; }
    .tyre-dot { width: 26px; height: 26px; border-radius: 50%; border: 3px solid; display: inline-block; text-align: center; font-size: 13px; line-height: 20px; margin-left: 25px; font-weight: 900; }
    .speed-val { font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 800; color: #fff; width: 80px; text-align: right; margin-left: 15px; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. CIRCUIT BREAKER DATA ENGINE (COMBINED SOURCE LOGIC) ---
@st.cache_data(ttl=5)
def fetch_verified_state(t_iso):
    try:
        l = requests.get(f"https://api.openf1.org/v1/laps?session_key={SK}&date_start<={t_iso}", cookies=cookies, timeout=5).json()
        d = requests.get(f"https://api.openf1.org/v1/drivers?session_key={SK}", cookies=cookies, timeout=5).json()
        s = requests.get(f"https://api.openf1.org/v1/stints?session_key={SK}", cookies=cookies, timeout=5).json()
        rc = requests.get(f"https://api.openf1.org/v1/race_control?session_key={SK}", cookies=cookies, timeout=5).json()
        
        if not all(isinstance(x, list) for x in [l, d, s, rc]):
            return None, None, None, None
        return l, {str(dr['driver_number']): dr for dr in d}, s, rc
    except:
        return None, None, None, None

@st.cache_data(ttl=5)
def fetch_telemetry_chunk(t_start_iso, t_end_iso, driver_num):
    # 2-second lookback to ensure we grab the right frame
    try:
        c = requests.get(f"https://api.openf1.org/v1/car_data?session_key={SK}&driver_number={driver_num}&date>={t_start_iso}&date<={t_end_iso}", cookies=cookies, timeout=5).json()
        if not isinstance(c, list) or len(c) == 0: return None
        return c[-1] # Return only the latest point in that window
    except:
        return None

# --- 4. REPLAY CONTROLS & STATE ---
START_TIME = datetime.fromisoformat("2026-03-08T04:15:00+00:00")

# Initialize session state for the moving clock
if 'current_time' not in st.session_state:
    st.session_state.current_time = START_TIME
if 'is_playing' not in st.session_state:
    st.session_state.is_playing = False

col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
with col1:
    v_clock = st.slider("Race Timeline", START_TIME, START_TIME + timedelta(hours=1.5), st.session_state.current_time, format="HH:mm:ss")
with col2:
    speed_mult = st.selectbox("Speed", [1, 2, 5], index=2)
with col3:
    if st.button("▶️ PLAY / ⏸️ PAUSE"):
        st.session_state.is_playing = not st.session_state.is_playing
with col4:
    # If the user scrubs the slider manually, update the internal clock
    if v_clock != st.session_state.current_time and not st.session_state.is_playing:
        st.session_state.current_time = v_clock

t_iso = st.session_state.current_time.isoformat()
t_start_iso = (st.session_state.current_time - timedelta(seconds=2)).isoformat()

l_raw, d_map, s_raw, rc_raw = fetch_verified_state(t_iso)

# Main UI Container
ui_placeholder = st.empty()

with ui_placeholder.container():
    # --- 5. LOGIC BRIDGES (FROM SOURCE) ---
    if l_raw and d_map:
        # A. FLAG PRIORITY BRIDGE
        p_map = {"RED": 1, "SAFETY CAR": 2, "VSC": 3, "YELLOW": 4, "BLUE": 5, "GREEN": 6}
        status, b_class = "TRACK CLEAR", "banner-green"
        if rc_raw:
            active = []
            for m in rc_raw:
                msg = m.get('message', '').upper()
                for flag, rank in p_map.items():
                    if flag in msg: active.append((rank, msg))
            if active:
                top = min(active, key=lambda x: x[0])
                status, b_class = top[1], "banner-red" if top[0]==1 else "banner-yellow" if top[0]<6 else "banner-green"
        
        st.markdown(f'<div class="top-banner {b_class}">{status}</div>', unsafe_allow_html=True)

        # B. FULL TOWER LOGIC (Restored from your app.py)
        latest = {str(lap['driver_number']): lap for lap in l_raw if isinstance(lap, dict) and 'driver_number' in lap}
        sorted_nums = sorted(latest.keys(), key=lambda x: (latest[x]['lap_number'], latest[x]['date_start']), reverse=True)
        
        lead_num = sorted_nums[0] if sorted_nums else None
        lead_dt = datetime.fromisoformat(latest[lead_num]['date_start'].replace('Z', '+00:00')) if lead_num else st.session_state.current_time
        
        for i, num in enumerate(sorted_nums):
            d, lap = d_map.get(num, {}), latest[num]
            curr_dt = datetime.fromisoformat(lap['date_start'].replace('Z', '+00:00'))
            
            # GAPS
            gap_sec = (curr_dt - lead_dt).total_seconds()
            is_pit = (st.session_state.current_time - curr_dt).total_seconds() > 150 or lap.get('is_pit_out_lap')
            gap_display = "PIT" if is_pit else ("LEADER" if i == 0 else f"+{abs(gap_sec):.3f}")
            gap_css = "stale-pit" if is_pit else ""

            # TYRES
            stint = [s for s in s_raw if str(s.get('driver_number')) == num]
            comp = stint[-1].get('compound', 'U') if stint else 'U'
            t_color = {"HARD": "#ffffff", "MEDIUM": "#ffea00", "SOFT": "#ff0000"}.get(comp, "#444")

            # TELEMETRY INTEGRATION (Aero, Boost, Speed)
            tel_data = fetch_telemetry_chunk(t_start_iso, t_iso, num)
            
            aero_html, boost_html, speed = "", "", "--"
            if tel_data:
                speed = tel_data.get('speed', 0)
                aero_val = tel_data.get('drs', 0)
                aero_html = "<span class='aero-badge'>X-MODE</span>" if aero_val == 14 else "<span class='aero-badge aero-z'>Z-MODE</span>"
                boost_val = tel_data.get('mgu_k_mode', 0)
                boost_html = "<span class='boost-badge'>⚡ BOOST</span>" if boost_val == 7 else ""

            # RENDER ROW
            st.markdown(f"""
                <div class="tower-row">
                    <div style="width:35px; color:#444; font-size:14px; font-weight:800;">{i+1}</div>
                    <div class="team-bar" style="background:#{d.get('team_colour', '333')};"></div>
                    <div style="font-size:24px; font-weight:900; letter-spacing:-0.5px;">{d.get('name_acronym', num)}</div>
                    {aero_html}{boost_html}
                    <div class="speed-val">{speed} <span style="font-size:10px; color:#888;">km/h</span></div>
                    <div class="gap-val {gap_css}">{gap_display}</div>
                    <div class="tyre-dot" style="border-color:{t_color}; color:{t_color};">{comp[0]}</div>
                </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="top-banner banner-yellow">📡 SEARCHING FOR TELEMETRY SIGNAL...</div>', unsafe_allow_html=True)

# --- REPLAY ENGINE TICKER ---
if st.session_state.is_playing:
    # Increment clock based on speed multiplier. 
    # Standard gap between loops is approx 0.5s due to API latency.
    step = timedelta(seconds=1 * speed_mult)
    st.session_state.current_time += step
    time.sleep(0.5) 
    st.rerun()
