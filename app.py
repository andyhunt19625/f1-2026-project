import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta

# --- 1. VERIFIED CONFIG & AUTH (FROM SOURCE) ---
USER, PASS = "andyhunt196@gmail.com", "NXo3j6z8zusIAiVG"
SK = 11234

st.set_page_config(page_title="AUS 26 | MASTER REPLAY", layout="wide", initial_sidebar_state="collapsed")

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
    .tower-row { border-bottom: 1px solid #1c1c2b; padding: 15px 5px; display: flex; align-items: center; justify-content: space-between;}
    .team-bar { width: 6px; height: 38px; border-radius: 3px; margin-right: 20px; }
    .aero-badge { background: #00ff41; color: black; font-size: 12px; font-weight: 900; padding: 4px 10px; border-radius: 4px; }
    .aero-z { background: #ffea00; }
    .boost-badge { background: #ff00ff; color: white; font-size: 12px; font-weight: 900; padding: 4px 10px; border-radius: 4px; margin-left: 10px; }
    .speed-val { font-family: 'JetBrains Mono', monospace; font-size: 24px; font-weight: 800; color: #fff; width: 100px; text-align: right; }
    .sub-info { text-align: center; color: #888; font-weight: 800; font-size: 14px; margin-bottom: 20px; letter-spacing: 0.5px; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. CIRCUIT BREAKER DATA ENGINE (UPDATED WITH ALL MOVING PARTS) ---
@st.cache_data(ttl=5)
def fetch_master_state(t_iso):
    try:
        # Pulling arrays up to t_iso exactly as proven in app.py
        l = requests.get(f"https://api.openf1.org/v1/laps?session_key={SK}&date_start<={t_iso}", cookies=cookies, timeout=5).json()
        d = requests.get(f"https://api.openf1.org/v1/drivers?session_key={SK}", cookies=cookies, timeout=5).json()
        w = requests.get(f"https://api.openf1.org/v1/weather?session_key={SK}&date<={t_iso}", cookies=cookies, timeout=5).json()
        rc = requests.get(f"https://api.openf1.org/v1/race_control?session_key={SK}&date<={t_iso}", cookies=cookies, timeout=5).json()

        if not all(isinstance(x, list) for x in [l, d, w, rc]):
            return None, None, None, None
        return l, {str(dr['driver_number']): dr for dr in d}, w, rc
    except:
        return None, None, None, None

@st.cache_data(ttl=5)
def fetch_car_data(t_start_iso, t_end_iso, driver_num):
    # TRIPLE CHECK FIX: 2-Second Lookback Window prevents fetching the wrong end of the array
    try:
        c = requests.get(f"https://api.openf1.org/v1/car_data?session_key={SK}&driver_number={driver_num}&date>={t_start_iso}&date<={t_end_iso}", cookies=cookies, timeout=5).json()
        if not isinstance(c, list): return None
        return c
    except:
        return None

# --- 4. REPLAY CLOCK (FROM SOURCE) ---
START_TIME = datetime.fromisoformat("2026-03-08T04:15:00+00:00")
v_clock = st.sidebar.slider("Timeline", START_TIME, START_TIME + timedelta(minutes=20), START_TIME, format="HH:mm:ss.f")

t_iso = v_clock.isoformat()
t_start_iso = (v_clock - timedelta(seconds=2)).isoformat() # 2-second window

l_raw, d_map, w_raw, rc_raw = fetch_master_state(t_iso)

# --- 5. LOGIC BRIDGES (STRICTLY FROM SOURCE) ---
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

    # B. WEATHER & LAP COUNT BRIDGE
    current_lap = max([lap.get('lap_number', 0) for lap in l_raw]) if l_raw else 0
    weather_str = "WEATHER: WAITING SYNC..."

    if w_raw:
        latest_w = w_raw[-1]
        air_t = latest_w.get('air_temperature', '--')
        trk_t = latest_w.get('track_temperature', '--')
        rain = "YES" if latest_w.get('rainfall') else "NO"
        weather_str = f"LAP {current_lap} | AIR: {air_t}°C | TRACK: {trk_t}°C | RAIN: {rain}"

    st.markdown(f'<div class="sub-info">{weather_str}</div>', unsafe_allow_html=True)

    # C. TELEMETRY TOWER LOGIC (Russell & Bottas)
    for num in [63, 77]:
        car_data = fetch_car_data(t_start_iso, t_iso, num)
        d = d_map.get(str(num), {})

        if car_data and len(car_data) > 0:
            latest_data = car_data[-1] # Grab the most recent point in the 2s window
            speed = latest_data.get('speed', 0)

            # 2026 AERO & BOOST BRIDGE
            aero_val = latest_data.get('drs', 0)
            aero_html = "<span class='aero-badge'>X-MODE</span>" if aero_val == 14 else "<span class='aero-badge aero-z'>Z-MODE</span>"

            boost_val = latest_data.get('mgu_k_mode', 0)
            boost_html = "<span class='boost-badge'>⚡ OVERRIDE</span>" if boost_val == 7 else ""

            team_color = d.get('team_colour', '444')
            acronym = d.get('name_acronym', str(num))

            st.markdown(f"""
                <div class="tower-row">
                    <div style="display:flex; align-items:center; width: 120px;">
                        <div class="team-bar" style="background:#{team_color};"></div>
                        <div style="font-size:24px; font-weight:900;">{acronym}</div>
                    </div>
                    <div style="flex-grow:1; text-align:left; padding-left:20px;">{aero_html}{boost_html}</div>
                    <div class="speed-val">{speed} <span style="font-size:12px; color:#888;">km/h</span></div>
                </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='tower-row' style='color:#666;'>#{num} - Awaiting Data Sync...</div>", unsafe_allow_html=True)
else:
    st.markdown('<div class="top-banner banner-yellow">📡 SEARCHING FOR RACE SIGNAL...</div>', unsafe_allow_html=True)
