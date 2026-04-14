import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time
import re

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Growth Pro", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = []
if 'current_batch_index' not in st.session_state: st.session_state.current_batch_index = 0
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False
if 'selected_ids' not in st.session_state: st.session_state.selected_ids = set()

# --- 3. 유틸리티 함수 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
    st.session_state.api_key_index += 1
    if st.session_state.api_key_index >= len(st.session_state.user_api_keys):
        st.error("🚨 모든 API 키 소진!")
        st.session_state.stop_analysis = True
        return None
    st.toast("🔄 다음 키로 전환합니다.")
    return get_youtube_client()

def get_channel_id_strong(youtube, input_text):
    if not input_text: return None
    input_text = input_text.strip()
    id_match = re.search(r'(UC[\w-]{22})', input_text)
    if id_match: return id_match.group(1)
    handle_match = re.search(r'(@[\w.-]+)', input_text)
    target = handle_match.group(1) if handle_match else input_text
    if not target.startswith('@') and not target.startswith('http'): target = '@' + target
    try:
        res = youtube.channels().list(forHandle=target, part='id').execute()
        if res.get('items'): return res['items'][0]['id']
        s_res = youtube.search().list(q=target, type='channel', part='id', maxResults=1).execute()
        if s_res.get('items'): return s_res['items'][0]['id']['channelId']
    except Exception as e:
        if "quotaExceeded" in str(e): switch_api_key()
    return None

# --- 4. 로그인 페이지 (닉네임 + 마스터 비번) ---
def login_page():
    st.title("🚀 YouTube Analyzer")
    st.subheader("닉네임과 접속 암호를 입력하세요")
    
    # 💡 닉네임이 곧 데이터 식별자(ID)가 됩니다.
    nickname = st.text_input("사용자 닉네임 (영문/숫자 추천)", placeholder="예: user01")
    master_pw = st.text_input("접속 암호", type="password")
    
    if st.button("접속하기", type="primary", use_container_width=True):
        if master_pw == "1795": # <--- 여기에 사용할 암호를 설정하세요
            if nickname:
                # 가짜 유저 객체를 생성하여 기존 코드와 호환성을 유지합니다.
                class UserInfo:
                    def __init__(self, nickname):
                        self.id = nickname
                        self.email = f"{nickname}@tool.app"
                
                st.session_state.user = UserInfo(nickname)
                st.success(f"{nickname}님 환영합니다!")
                st.rerun()
            else:
                st.warning("닉네임을 입력해주세요.")
        else:
            st.error("암호가 틀렸습니다.")

# --- 5. 메인 앱 서비스 ---
def main_app():
    with st.sidebar:
        st.subheader(f"👤 {st.session_state.user.id} 님")
        raw_keys = st.text_area("🔑 API Keys", value="\n".join(st.session_state.user_api_keys)).split('\n')
        if st.button("키 저장"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.session_state.api_key_index = 0
            st.success("저장됨")
        
        st.divider()
        st.subheader("📥 채널 수집")
        target_input = st.text_input("기준 핸들 또는 URL")
        group_name = st.text_input("그룹명", value="미분류")
        if st.button("수집 시작"):
            youtube = get_youtube_client()
            if not youtube: st.warning("키 없음")
            else:
                with st.spinner("채널 분석 중..."):
                    main_id = get_channel_id_strong(youtube, target_input)
                    if main_id:
                        next_token = None
                        total = 0
                        while True:
                            res = youtube.subscriptions().list(channelId=main_id, part='snippet', maxResults=50, pageToken=next_token).execute()
                            for s in res.get('items', []):
                                s_id = s['snippet']['resourceId']['channelId']
                                # 💡 user_id 자리에 닉네임이 들어갑니다.
                                supabase.table('channels').upsert({
                                    "user_id": st.session_state.user.id, 
                                    "channel_id": s_id, 
                                    "channel_name": s['snippet']['title'], 
                                    "category": group_name, 
                                    "channel_url": f"https://youtube.com/channel/{s_id}"
                                }, on_conflict="channel_id").execute()
                                total += 1
                            next_token = res.get('nextPageToken')
                            if not next_token: break
                        st.success(f"{total}개 저장 완료")
                    else: st.error("채널 찾기 실패")

        if st.button("로그아웃"):
            st.session_state.user = None
            st.rerun()

    # 메인 화면
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리"])

    with tab_manage:
        # 💡 본인 닉네임(user.id)으로 저장된 데이터만 가져옵니다.
        res = supabase.table('channels').select("*").eq("user_id", st.session_state.user.id).execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.data_editor(df_db[['channel_name', 'category', 'channel_url']], use_container_width=True)
            if st.button("🗑️ 내 리스트 전체 삭제"):
                supabase.table('channels').delete().eq("user_id", st.session_state.user.id).execute()
                st.rerun()
        else: st.info("저장된 채널이 없습니다.")

    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집하세요.")
        else:
            df_scan = pd.DataFrame(res.data)
            st.markdown(f"### 📊 분석 현황: `{st.session_state.current_batch_index}` / `{len(df_scan)}` 개 완료")
            # ... (이후 분석 로직은 기존과 동일) ...
            # 생략된 분석 로직 부분은 이전 답변의 코드를 그대로 사용하시면 됩니다.

# --- 6. 실행 제어 ---
if st.session_state.user is None:
    login_page()
else:
    main_app()
