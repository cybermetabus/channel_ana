import streamlit as st
from supabase import create_client, Client

# --- 1. Supabase 초기화 및 연결 ---
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_connection()

# --- 2. 세션 상태(로그인 유지) 관리 ---
if 'user' not in st.session_state:
    st.session_state.user = None

# --- 3. 로그인 / 회원가입 UI ---
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
                st.rerun() # 화면 새로고침하여 메인 앱으로 이동
            except Exception as e:
                st.error("로그인 실패: 이메일이나 비밀번호를 확인해주세요.")

    with tab2:
        st.subheader("새 계정 만들기")
        new_email = st.text_input("이메일", key="signup_email")
        new_password = st.text_input("비밀번호 (6자리 이상)", type="password", key="signup_password")
        if st.button("회원가입 실행"):
            try:
                response = supabase.auth.sign_up({"email": new_email, "password": new_password})
                st.success("회원가입 성공! 이제 로그인 탭에서 로그인해주세요.")
            except Exception as e:
                st.error(f"회원가입 실패: {e}")

# --- 4. 메인 대시보드 (로그인한 유저만 볼 수 있음) ---
def main_app():
    st.sidebar.title("👤 내 정보")
    st.sidebar.write(f"접속 계정: {st.session_state.user.email}")
    if st.sidebar.button("로그아웃"):
        supabase.auth.sign_out()
        st.session_state.user = None
        st.rerun()

    st.title("🎯 내 알고리즘 분석기 (Cloud 버전)")
    
    # [데이터 추가 파트]
    st.markdown("### ➕ 새 채널 추가하기")
    with st.form("add_channel_form"):
        col1, col2 = st.columns(2)
        channel_name = col1.text_input("채널 이름 (예: 슈카월드)")
        channel_url = col2.text_input("채널 URL")
        category = st.selectbox("카테고리 지정", ["경제", "예능", "테크", "기타"])
        
        submitted = st.form_submit_button("DB에 저장")
        if submitted and channel_name and channel_url:
            try:
                # 💡 핵심: 현재 로그인한 유저의 ID를 함께 저장!
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
        pass # 버튼을 누르면 화면이 다시 그려지면서 최신 DB를 불러옴
        
    try:
        # 💡 핵심: DB에서 채널을 불러옴 (RLS 보안 규칙 때문에 자동으로 '내 데이터'만 불러와짐)
        response = supabase.table('channels').select("*").execute()
        channels_data = response.data
        
        if channels_data:
            st.dataframe(channels_data, use_container_width=True)
        else:
            st.info("아직 저장된 채널이 없습니다. 위에서 채널을 추가해보세요!")
    except Exception as e:
        st.error(f"데이터를 불러오는 중 오류 발생: {e}")

# --- 5. 프로그램 흐름 제어 ---
# 유저 정보가 없으면 로그인 화면을, 있으면 메인 앱을 보여줌
if st.session_state.user is None:
    login_page()
else:
    main_app()