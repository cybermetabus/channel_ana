import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone
import isodate

# --- 1. Supabase 초기화 ---
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_connection()

# --- 2. 유튜브 API 클라이언트 (💡 유저 개별 API 키 사용으로 변경) ---
def get_youtube_client():
    # 이제 secrets.toml이 아니라 현재 유저가 입력한 session_state에서 키를 가져옵니다.
    api_key = st.session_state.get("user_api_key", "")
    
    if not api_key:
        st.error("⚠️ 왼쪽 사이드바에 본인의 유튜브 API 키를 먼저 입력해주세요!")
        return None
        
    try:
        return build('youtube', 'v3', developerKey=api_key, cache_discovery=False)
    except Exception as e:
        st.error("🚨 API 키가 유효하지 않거나 연결에 실패했습니다.")
        return None

# --- 3. 세션 상태 관리 ---
if 'user' not in st.session_state:
    st.session_state.user = None
if 'user_api_key' not in st.session_state:
    st.session_state.user_api_key = ""

# --- 4. 로그인 / 회원가입 UI (💡 비밀번호 확인 기능 추가) ---
def login_page():
    st.title("🔐 YouTube Analyzer 로그인")
    
    tab1, tab2 = st.tabs(["로그인", "회원가입"])
    
    with tab1:
        st.subheader("로그인")
        email = st.text_input("이메일", key="login_email")
        password = st.text_input("비밀번호", type="password", key="login_password")
        if st.button("로그인 실행"):
            try:
                response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.user = response.user
                st.success("로그인 성공!")
                st.rerun() 
            except Exception as e:
                st.error("로그인 실패: 이메일이나 비밀번호를 확인해주세요.")

    with tab2:
        st.subheader("새 계정 만들기")
        new_email = st.text_input("이메일", key="signup_email")
        new_password = st.text_input("비밀번호 (6자리 이상)", type="password", key="signup_password")
        
        # 💡 추가된 비밀번호 확인 로직
        new_password_confirm = st.text_input("비밀번호 확인", type="password", key="signup_password_confirm")
        
        if st.button("회원가입 실행"):
            if new_password != new_password_confirm:
                st.error("⚠️ 비밀번호가 서로 일치하지 않습니다. 다시 확인해주세요.")
            elif len(new_password) < 6:
                st.error("⚠️ 비밀번호는 6자리 이상이어야 합니다.")
            else:
                try:
                    response = supabase.auth.sign_up({"email": new_email, "password": new_password})
                    st.success("🎉 회원가입 성공! 이제 로그인 탭에서 로그인해주세요.")
                except Exception as e:
                    st.error(f"회원가입 실패: {e}")

# --- 5. 메인 대시보드 ---
def main_app():
    # --- 사이드바 영역 ---
    st.sidebar.title("👤 내 정보")
    st.sidebar.write(f"접속 계정: {st.session_state.user.email}")
    
    st.sidebar.markdown("---")
    
    # 💡 유저별 개인 API 키 입력란 추가
    st.sidebar.subheader("🔑 내 유튜브 API 키 설정")
    input_api_key = st.sidebar.text_input("YouTube Data API v3 Key", type="password", value=st.session_state.user_api_key, placeholder="AIzaSy...")
    
    if st.sidebar.button("API 키 적용하기"):
        st.session_state.user_api_key = input_api_key
        st.sidebar.success("✅ API 키가 적용되었습니다!")
        
    st.sidebar.markdown("---")
    
    if st.sidebar.button("로그아웃"):
        supabase.auth.sign_out()
        st.session_state.user = None
        st.session_state.user_api_key = "" # 로그아웃 시 API 키도 초기화
        st.rerun()

    # --- 메인 화면 영역 ---
    st.title("🎯 내 알고리즘 분석기 (Cloud 버전)")
    
    # [데이터 추가 파트]
    st.markdown("### ➕ 새 채널 추가하기")
    with st.form("add_channel_form"):
        col1, col2 = st.columns(2)
        channel_name = col1.text_input("채널 이름 (예: 슈카월드)")
        channel_url = col2.text_input("채널 URL 또는 핸들(@)")
        category = st.selectbox("카테고리 지정", ["경제", "예능", "테크", "정보", "기타"])
        
        submitted = st.form_submit_button("DB에 저장")
        if submitted and channel_name and channel_url:
            try:
                data, count = supabase.table('channels').insert({
                    "user_id": st.session_state.user.id,
                    "channel_name": channel_name,
                    "channel_url": channel_url,
                    "category": category
                }).execute()
                st.success(f"'{channel_name}' 채널이 안전하게 저장되었습니다!")
            except Exception as e:
                st.error(f"저장 실패: {e}")

    st.divider()

    # [데이터 불러오기 파트]
    st.markdown("### 📊 내가 수집한 채널 목록")
    if st.button("새로고침 🔄"):
        pass 
        
    try:
        response = supabase.table('channels').select("*").execute()
        channels_data = response.data
        
        if channels_data:
            df_channels = pd.DataFrame(channels_data)
            st.dataframe(df_channels[['channel_name', 'channel_url', 'category', 'created_at']], use_container_width=True)
            
            st.divider()
            st.markdown("### 🚀 VPH 알고리즘 분석기")
            
            col_a, col_b = st.columns(2)
            max_days = col_a.number_input("최근 몇 일 이내의 영상을 분석할까요?", min_value=1, value=7)
            min_views = col_b.number_input("최소 조회수 필터", min_value=0, value=10000, step=10000)
            
            if st.button("내 채널 DB 전체 분석 시작!", type="primary"):
                # 💡 API 키가 없으면 분석을 아예 시작하지 않음
                if not st.session_state.user_api_key:
                    st.warning("👈 왼쪽 사이드바에 YouTube API 키를 먼저 입력하고 적용 버튼을 눌러주세요!")
                else:
                    st.info(f"총 {len(channels_data)}개 채널의 최신 영상을 수집합니다. 잠시만 기다려주세요...")
                    youtube = get_youtube_client()
                    
                    if youtube:
                        analyzed_videos = []
                        
                        # (임시 더미 데이터 로직)
                        for ch in channels_data:
                            ch_name = ch['channel_name']
                            analyzed_videos.append({
                                "채널명": ch_name,
                                "영상 제목": f"{ch_name}의 최신 영상 (API 작동 테스트)",
                                "조회수": 50000,
                                "게시일": "2일 전",
                                "VPH (시간당조회수)": 1041,
                                "분류": ch['category']
                            })
                        
                        if analyzed_videos:
                            df_result = pd.DataFrame(analyzed_videos)
                            df_result = df_result.sort_values(by="VPH (시간당조회수)", ascending=False)
                            st.success("✅ 분석 완료!")
                            st.dataframe(df_result, use_container_width=True)

        else:
            st.info("아직 저장된 채널이 없습니다. 위에서 채널을 추가해보세요!")
    except Exception as e:
        st.error(f"데이터를 불러오는 중 오류 발생: {e}")

# --- 6. 프로그램 흐름 제어 ---
if st.session_state.user is None:
    login_page()
else:
    main_app()
