import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import re

# --- 1. Supabase 초기화 ---
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_connection()

# --- 2. 유튜브 API 엔진 (유저 개별 키 사용) ---
def get_youtube_client():
    api_key = st.session_state.get("user_api_key", "")
    if not api_key:
        return None
    try:
        return build('youtube', 'v3', developerKey=api_key, cache_discovery=False)
    except:
        return None

# --- 3. 유틸리티 함수 (핸들 인식 및 VPH 계산) ---
def get_channel_id(youtube, url_or_handle):
    """URL이나 @핸들에서 채널 ID를 추출합니다."""
    if 'youtube.com/channel/' in url_or_handle:
        return url_or_handle.split('/')[-1].split('?')[0]
    
    handle = url_or_handle.split('/')[-1].split('?')[0]
    if not handle.startswith('@'):
        handle = '@' + handle
        
    res = youtube.search().list(q=handle, type='channel', part='id', maxResults=1).execute()
    if res.get('items'):
        return res['items'][0]['id']['channelId']
    return None

def calculate_vph(views, published_at):
    """현재 시간과 게시 시간을 비교하여 시간당 조회수(VPH)를 계산합니다."""
    pub_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    diff = now - pub_date
    hours = max(diff.total_seconds() / 3600, 0.1) # 최소 0.1시간으로 계산
    return round(views / hours, 1)

# --- 4. 세션 상태 관리 ---
if 'user' not in st.session_state:
    st.session_state.user = None
if 'user_api_key' not in st.session_state:
    st.session_state.user_api_key = ""

# --- 5. 로그인 / 회원가입 UI ---
def login_page():
    st.title("🔐 YouTube Analyzer 로그인")
    tab1, tab2 = st.tabs(["로그인", "회원가입"])
    with tab1:
        email = st.text_input("이메일", key="login_email")
        password = st.text_input("비밀번호", type="password", key="login_password")
        if st.button("로그인 실행"):
            try:
                res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.user = res.user
                st.rerun()
            except:
                st.error("로그인 실패!")
    with tab2:
        new_email = st.text_input("이메일", key="signup_email")
        new_pw = st.text_input("비밀번호", type="password", key="signup_pw")
        new_pw_c = st.text_input("비밀번호 확인", type="password", key="signup_pw_c")
        if st.button("회원가입 실행"):
            if new_pw == new_pw_c:
                try:
                    supabase.auth.sign_up({"email": new_email, "password": new_pw})
                    st.success("가입 성공! 로그인해주세요.")
                except Exception as e: st.error(f"실패: {e}")
            else: st.error("비밀번호 불일치")

# --- 6. 메인 앱 (모든 기능 복원) ---
def main_app():
    # 사이드바 설정
    st.sidebar.title("👤 내 정보")
    st.sidebar.write(f"계정: {st.session_state.user.email}")
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔑 유튜브 API 키")
    api_input = st.sidebar.text_input("API Key 입력", type="password", value=st.session_state.user_api_key)
    if st.sidebar.button("적용하기"):
        st.session_state.user_api_key = api_input
        st.sidebar.success("적용 완료!")
    if st.sidebar.button("로그아웃"):
        supabase.auth.sign_out()
        st.session_state.user = None
        st.rerun()

    st.title("🎯 YouTube 알고리즘 타겟 분석기")

    tab1, tab2 = st.tabs(["📊 영상 분석", "⚙️ DB 관리"])

    # [DB 관리 탭]
    with tab2:
        st.subheader("➕ 새 채널 추가")
        with st.form("add_form"):
            c1, c2, c3 = st.columns([2,3,2])
            name = c1.text_input("채널명")
            url = c2.text_input("URL 또는 @핸들")
            cat = c3.selectbox("카테고리", ["경제", "예능", "테크", "정보", "기타"])
            if st.form_submit_button("저장"):
                supabase.table('channels').insert({"user_id": st.session_state.user.id, "channel_name": name, "channel_url": url, "category": cat}).execute()
                st.success("저장 완료!")
        
        st.divider()
        res = supabase.table('channels').select("*").execute()
        if res.data:
            st.dataframe(pd.DataFrame(res.data)[['channel_name', 'channel_url', 'category']], use_container_width=True)

    # [영상 분석 탭]
    with tab1:
        col1, col2, col3 = st.columns(3)
        days = col1.number_input("분석 기간 (일)", value=7, min_value=1)
        min_v = col2.number_input("최소 조회수", value=1000, step=1000)
        v_type = col3.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])

        if st.button("🚀 전 채널 분석 시작", type="primary"):
            if not st.session_state.user_api_key:
                st.warning("왼쪽에서 API 키를 먼저 적용해주세요!")
                return
            
            youtube = get_youtube_client()
            res = supabase.table('channels').select("*").execute()
            channels = res.data
            
            if not channels:
                st.info("DB에 등록된 채널이 없습니다.")
                return

            results = []
            progress = st.progress(0)
            
            for i, ch in enumerate(channels):
                try:
                    ch_id = get_channel_id(youtube, ch['channel_url'])
                    if not ch_id: continue
                    
                    # 최근 영상 가져오기
                    v_res = youtube.search().list(channelId=ch_id, part='snippet', maxResults=10, order='date', type='video').execute()
                    
                    video_ids = [item['id']['videoId'] for item in v_res.get('items', [])]
                    if not video_ids: continue
                    
                    # 영상 상세 정보 (조회수, 길이 등)
                    d_res = youtube.videos().list(id=','.join(video_ids), part='statistics,contentDetails,snippet').execute()
                    
                    for item in d_res.get('items', []):
                        # 게시일 필터링
                        pub_at = item['snippet']['publishedAt']
                        pub_date = datetime.fromisoformat(pub_at.replace('Z', '+00:00'))
                        if datetime.now(timezone.utc) - pub_date > timedelta(days=days): continue
                        
                        # 조회수 필터링
                        views = int(item['statistics'].get('viewCount', 0))
                        if views < min_v: continue
                        
                        # 길이 필터링 (ISO 8601 duration parsing)
                        dur_str = item['contentDetails']['duration']
                        duration = isodate.parse_duration(dur_str).total_seconds()
                        
                        is_short = duration <= 60
                        if v_type == "롱폼만" and is_short: continue
                        if v_type == "숏폼만" and not is_short: continue
                        
                        results.append({
                            "채널": ch['channel_name'],
                            "카테고리": ch['category'],
                            "제목": item['snippet']['title'],
                            "조회수": views,
                            "VPH": calculate_vph(views, pub_at),
                            "포맷": "숏폼" if is_short else "롱폼",
                            "링크": f"https://youtu.be/{item['id']}"
                        })
                except Exception as e:
                    st.error(f"{ch['channel_name']} 분석 중 오류: {e}")
                
                progress.progress((i + 1) / len(channels))

            if results:
                df = pd.DataFrame(results).sort_values("VPH", ascending=False)
                st.success(f"총 {len(results)}개의 영상을 찾았습니다!")
                # 클릭 가능한 링크로 만들기
                st.data_editor(df, column_config={"링크": st.column_config.LinkColumn()}, use_container_width=True)
            else:
                st.warning("조건에 맞는 영상이 없습니다.")

# --- 7. 실행 제어 ---
if st.session_state.user is None:
    login_page()
else:
    main_app()
