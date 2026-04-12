import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate

# --- 1. 초기 설정 ---
st.set_page_config(page_title="YouTube Intelligence Tool", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. API 엔진 & 유틸리티 ---
if 'api_key_index' not in st.session_state:
    st.session_state.api_key_index = 0

def get_youtube_client():
    keys = st.session_state.get("user_api_keys", [])
    if not keys: return None
    current_key = keys[st.session_state.api_key_index % len(keys)]
    return build('youtube', 'v3', developerKey=current_key, cache_discovery=False)

def handle_api_error(e):
    if "quotaExceeded" in str(e):
        st.session_state.api_key_index += 1
        st.toast("🔄 할당량 초과로 다음 API 키로 전환합니다.")
        return True
    return False

def get_channel_id_by_handle(youtube, handle):
    """핸들을 통해 정확한 채널 ID만 추출 (할당량 1점)"""
    handle = handle.strip()
    clean_handle = handle if handle.startswith('@') else '@' + handle
    try:
        res = youtube.channels().list(forHandle=clean_handle, part='id').execute()
        return res['items'][0]['id'] if res.get('items') else None
    except: return None

# --- 3. 세션 및 유저 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []

def login_page():
    st.title("🔐 YouTube Analyzer Login")
    t1, t2 = st.tabs(["로그인", "회원가입"])
    with t1:
        e = st.text_input("이메일", key="l_email")
        p = st.text_input("비밀번호", type="password", key="l_pw")
        if st.button("로그인", key="l_btn"):
            try:
                res = supabase.auth.sign_in_with_password({"email": e, "password": p})
                st.session_state.user = res.user
                st.rerun()
            except: st.error("계정 정보를 확인해주세요.")
    with t2:
        ne = st.text_input("이메일", key="s_email")
        np = st.text_input("비밀번호", type="password", key="s_pw")
        if st.button("회원가입", key="s_btn"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료! 로그인 탭을 이용해주세요.")

# --- 4. 메인 어플리케이션 ---
def main_app():
    # 사이드바: API 설정 및 구독 수집 전용창
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 다중 API 키 (엔터로 구분)", 
                               value="\n".join(st.session_state.user_api_keys), height=100).split('\n')
        if st.button("API 키 적용"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("적용 완료")
        
        st.divider()
        st.subheader("📥 구독 리스트 수집기")
        target_handle = st.text_input("기준 채널 핸들 (@비디오26 등)")
        custom_group = st.text_input("저장할 그룹명(카테고리)", value="내 그룹1")
        
        if st.button("정확한 구독 목록만 가져와 저장"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키를 입력하세요.")
            else:
                with st.spinner("구독 정보 추출 중..."):
                    main_id = get_channel_id_by_handle(youtube, target_handle)
                    if main_id:
                        try:
                            # ⚠️ STRICT: 오직 공개된 구독 목록만 가져옴
                            res = youtube.subscriptions().list(channelId=main_id, part='snippet', maxResults=50).execute()
                            subs = res.get('items', [])
                            if not subs:
                                st.warning("구독 목록이 비공개이거나 구독 중인 채널이 없습니다.")
                            else:
                                for s in subs:
                                    s_id = s['snippet']['resourceId']['channelId']
                                    supabase.table('channels').upsert({
                                        "user_id": st.session_state.user.id,
                                        "channel_id": s_id,
                                        "channel_name": s['snippet']['title'],
                                        "channel_url": f"https://youtube.com/channel/{s_id}",
                                        "category": custom_group
                                    }, on_conflict="channel_id").execute()
                                st.success(f"'{custom_group}'에 {len(subs)}개 채널 저장 완료!")
                        except Exception as e: st.error(f"오류: {e}")
                    else: st.error("해당 핸들의 채널을 찾을 수 없습니다.")

        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # 메인 탭 구성
    st.title("🎯 알고리즘 타겟 콘텐츠 분석")
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ 채널 및 그룹 관리"])

    # [채널 관리 탭] - 자유로운 카테고리 수정
    with tab_manage:
        st.subheader("📝 내 채널 리스트 및 그룹 수정")
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            edited = st.data_editor(
                df_db[['id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True, num_rows="dynamic", key="editor"
            )
            if st.button("💾 모든 변경사항 일괄 저장"):
                for _, row in edited.iterrows():
                    supabase.table('channels').update({"channel_name": row['channel_name'], "category": row['category']}).eq("id", row['id']).execute()
                st.success("데이터베이스 업데이트 완료!"); st.rerun()
        else: st.info("수집된 채널이 없습니다.")

    # [콘텐츠 검색 탭] - 필터링 및 포맷(롱/숏) 분석
    with tab_scan:
        res = supabase.table('channels').select("*").execute()
        if not res.data: st.warning("사이드바에서 먼저 채널을 수집하세요.")
        else:
            df_all = pd.DataFrame(res.data)
            groups = sorted(df_all['category'].unique())

            with st.form("filter_form"):
                st.subheader("⚙️ 스캔 필터 설정")
                c1, c2, c3 = st.columns([2, 1, 1])
                target_groups = c1.multiselect("분석할 카테고리(그룹) 선택", options=groups, default=groups)
                v_format = c2.selectbox("영상 포맷", ["전체", "롱폼만", "숏폼만"])
                
                time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주일": 168, "한달": 720}
                t_label = c3.selectbox("업로드 기간", list(time_opts.keys()), index=4)
                
                c4, c5, c6 = st.columns(3)
                min_v = c4.number_input("최소 조회수", value=5000)
                min_s = c5.number_input("최소 구독자수", value=0)
                max_s = c6.number_input("최대 구독자수 (0=무제한)", value=0)
                
                run_btn = st.form_submit_button("🚀 설정 조건으로 분석 시작", type="primary")

            if run_btn:
                youtube = get_youtube_client()
                if not youtube: st.warning("API 키가 없습니다."); return

                scan_list = df_all[df_all['category'].isin(target_groups)]
                st.info(f"선택한 {len(scan_list)}개 채널에서 조건에 맞는 영상을 찾습니다...")
                
                results = []
                bar = st.progress(0)
                limit_h = time_opts[t_label]

                for i, ch in enumerate(scan_list.to_dict('records')):
                    try:
                        # 1. 구독자수 필터
                        ch_info = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                        s_count = int(ch_info['items'][0]['statistics'].get('subscriberCount', 0))
                        if (min_s > 0 and s_count < min_s) or (max_s > 0 and s_count > max_s): continue

                        # 2. 최신 영상 수집
                        v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                        v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                        
                        if v_ids:
                            d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet,contentDetails').execute()
                            for item in d_res.get('items', []):
                                # 시간 필터
                                pub_at = item['snippet']['publishedAt']
                                dt_pub = datetime.fromisoformat(pub_at.replace('Z', '+00:00'))
                                age_h = (datetime.now(timezone.utc) - dt_pub).total_seconds() / 3600
                                if age_h > limit_h: continue
                                
                                # 조회수 필터
                                views = int(item['statistics'].get('viewCount', 0))
                                if views < min_v: continue
                                
                                # 롱폼/숏폼 구분
                                dur = isodate.parse_duration(item['contentDetails']['duration']).total_seconds()
                                is_s = dur <= 60
                                if v_format == "롱폼만" and is_s: continue
                                if v_format == "숏폼만" and not is_s: continue

                                results.append({
                                    "썸네일": item['snippet']['thumbnails']['default']['url'],
                                    "채널": item['snippet']['channelTitle'],
                                    "구독자": s_count,
                                    "제목": item['snippet']['title'],
                                    "조회수": views,
                                    "VPH": round(views / max(age_h, 0.1), 1),
                                    "포맷": "숏폼" if is_s else "롱폼",
                                    "링크": f"https://youtu.be/{item['id']}"
                                })
                    except Exception as e: handle_api_error(e)
                    bar.progress((i + 1) / len(scan_list))

                if results:
                    st.subheader(f"📊 스캔 결과 ({len(results)}건)")
                    st.data_editor(
                        pd.DataFrame(results).sort_values("VPH", ascending=False),
                        column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()},
                        use_container_width=True, hide_index=True
                    )
                else: st.warning("조건에 맞는 영상이 없습니다.")

# --- 실행 제어 ---
if st.session_state.user is None: login_page()
else: main_app()
