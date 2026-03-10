import streamlit as st
import requests
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

# --- 2. PROVEN CSS (Fixed widths to prevent column collapse) ---
st.markdown("""
    <style>
    .stApp { background-color: #06060a; color: #e0e0e0; font-family: 'Inter', sans-serif; }
    .top-banner { padding: 14px; text-align: center; font-weight: 900; font-size: 16px; border-radius: 10px; margin-bottom: 20px; text-transform: uppercase; letter-spacing: 1px; }
    .banner-green { background: linear-gradient(90deg, #00d21d, #008a13); color: black; box-shadow: 0 4px 15px rgba(0, 210, 29, 0.3); }
    .banner-yellow { background: linear-gradient(90deg, #ffff00, #b8b800); color: black; box-shadow: 0 4px 15px rgba(255, 255, 0, 0.3); }
    .banner-red { background: linear-gradient(90deg, #ff0000, #a10000); color: white; box-shadow: 0 4px 15px rgba(255, 0, 0, 0.3); }
    .tower-row { border-bottom: 1px solid #1c1c2b; padding: 15px 5px; display: flex; align-items: center; }
    .team-bar { width: 6px; height: 38px; border-radius: 3px; margin-right: 20px; }
    .aero-badge { background: #00ff41; color: black; font-size: 11px; font-weight: 900; padding: 3px 8px; border-radius: 4px; }
    .aero-z { background: #ffea00; }
    .gap-val { font-family: 'JetBrains Mono', 'Courier New', monospace; color: #00ff41; font-weight: 800; margin-left: auto; font-size: 22px; }
    .stale-pit { color: #666 !important; font-style: italic; font-weight: 400; }
    .tyre-dot { width: 26px; height: 26px; border-radius: 50%; border: 3px solid; display: inline-block; text-align: center; font-size: 13px; line-height: 20px; margin-left: 25px; font-weight: 900; }
    .speed-val { font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 800; color: #fff; text-align: right; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. PROVEN DIRECTORY PULLS ONLY ---
@st.cache_data(ttl=5)
def fetch_verified_state(t_iso):
    try:
        l = requests.get(f"https://api.openf1.org/v1/laps?session_key={SK}&date_start<={t_iso}", cookies=cookies, timeout=5).json()
        d = requests.get(f"https://api.openf1.org/v1/drivers?session_key={SK}", cookies=cookies, timeout=5).json()
        s = requests.get(f"https://api.openf1.org/v1/stints?session_key={SK}", cookies=cookies, timeout=5).json()
        rc = requests.get(f"https://api.openf1.org/v1/race_control?session_key={SK}&date<={t_iso}", cookies=cookies, timeout=5).json()
        
        # Circuit Breaker to prevent AttributeError
        if not all(isinstance(x, list) for x in [l, d, s, rc]):
            return None, None, None, None
        return l, {str(dr['driver_number']): dr for dr in d}, s, rc
    except:
        return None, None, None, None

# --- 4. REPLAY CONTROLS & STATE ---
START_TIME = datetime.fromisoformat("2026-03-08T04:10:00+00:00")

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
    if v_clock != st.session_state.current_time and not st.session_state.is_playing:
        st.session_state.current_time = v_clock

t_iso = st.session_state.current_time.isoformat()
l_raw, d_map, s_raw, rc_raw = fetch_verified_state(t_iso)

ui_placeholder = st.empty()

with ui_placeholder.container():
    if l_raw and d_map:
        # A. PROVEN FLAG LOGIC
        status, b_class = "TRACK CLEAR", "banner-green"
        if rc_raw:
            rc_sorted = sorted(rc_raw, key=lambda x: x.get('date', '') if isinstance(x, dict) else '', reverse=True)
            for m in rc_sorted:
                if not isinstance(m, dict): continue
                msg = m.get('message', '').upper()
                flag = m.get('flag', '')
                flag_str = flag.upper() if flag else ""
                
                if "CHEQUERED" in msg or flag_str == "CHEQUERED":
                    status, b_class = "CHEQUERED FLAG", "banner-green"
                    break
                elif "RED" in msg or flag_str == "RED":
                    status, b_class = "RED FLAG", "banner-red"
                    break
                elif "SAFETY CAR" in msg or "VSC" in msg:
                    status, b_class = "SAFETY CAR", "banner-yellow"
                    break
                elif "YELLOW" in msg or flag_str == "YELLOW":
                    status, b_class = "YELLOW FLAG", "banner-yellow"
                    break
                elif "GREEN" in msg or "CLEAR" in msg or flag_str == "GREEN" or flag_str == "CLEAR":
                    status, b_class = "TRACK CLEAR", "banner-green"
                    break

        st.markdown(f'<div class="top-banner {b_class}">{status}</div>', unsafe_allow_html=True)

        # B. TOWER LOGIC & MATH
        latest = {str(lap['driver_number']): lap for lap in l_raw if isinstance(lap, dict) and 'driver_number' in lap}
        
        def lap_sort_key(num):
            lap = latest[num]
            try:
                dt = datetime.fromisoformat(lap['date_start'].replace('Z', '+00:00')).timestamp()
            except:
                dt = 0
            return (lap.get('lap_number', 0), -dt)

        sorted_nums = sorted(latest.keys(), key=lap_sort_key, reverse=True)
        lead_num = sorted_nums[0] if sorted_nums else None
        lead_dt = datetime.fromisoformat(latest[lead_num]['date_start'].replace('Z', '+00:00')) if lead_num else st.session_state.current_time
        
        for i, num in enumerate(sorted_nums):
            d, lap = d_map.get(num, {}), latest[num]
            curr_dt = datetime.fromisoformat(lap['date_start'].replace('Z', '+00:00'))
            
            # GAPS & TYRES
            gap_sec = (curr_dt - lead_dt).total_seconds()
            is_pit = (st.session_state.current_time - curr_dt).total_seconds() > 150 or lap.get('is_pit_out_lap')
            gap_display = "PIT" if is_pit else ("LEADER" if i == 0 else f"+{abs(gap_sec):.3f}")
            gap_css = "stale-pit" if is_pit else ""

            stint = [s for s in s_raw if isinstance(s, dict) and str(s.get('driver_number')) == num]
            comp = stint[-1].get('compound', 'U') if stint else 'U'
            t_color = {"HARD": "#ffffff", "MEDIUM": "#ffea00", "SOFT": "#ff0000"}.get(comp, "#444")

            # --- OFFICIAL SOURCE AERO BRIDGE (FROM LAPS) ---
            st_speed = lap.get('st_speed') or 0
            
            aero_html = ""
            if st_speed > 310:
                aero_html = "<span class='aero-badge'>X-MODE</span>"
            elif st_speed > 0 and st_speed <= 310:
                aero_html = "<span class='aero-badge aero-z'>Z-MODE</span>"
            
            speed_display = f"{int(st_speed)}" if st_speed else "--"

            # RENDER ROW
            st.markdown(f"""
                <div class="tower-row">
                    <div style="width:35px; color:#444; font-size:14px; font-weight:800;">{i+1}</div>
                    <div class="team-bar" style="background:#{d.get('team_colour', '333')};"></div>
                    <div style="width:70px; font-size:24px; font-weight:900; letter-spacing:-0.5px;">{d.get('name_acronym', num)}</div>
                    
                    <div style="width:120px; display:flex; align-items:center;">
                        {aero_html}
                    </div>
                    
                    <div class="speed-val" style="width:100px;">{speed_display} <span style="font-size:10px; color:#888;">km/h</span></div>
                    
                    <div class="gap-val {gap_css}">{gap_display}</div>
                    <div class="tyre-dot" style="border-color:{t_color}; color:{t_color};">{comp[0]}</div>
                </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="top-banner banner-yellow">📡 SEARCHING FOR RACE SIGNAL...</div>', unsafe_allow_html=True)

# --- REPLAY TICKER ---
if st.session_state.is_playing:
    step = timedelta(seconds=1 * speed_mult)
    st.session_state.current_time += step
    time.sleep(0.3) 
    st.rerun()
