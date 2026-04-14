import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Intelligence Tool", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 관리 (정지 및 페이징용) ---
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False
if 'next_page_token' not in st.session_state: st.session_state.next_page_token = None

# --- 3. API 엔진 및 자동 전환 로직 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    # 인덱스가 키 개수를 넘지 않도록 관리
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
    """할당량 초과 시 다음 키로 인덱스 이동"""
    st.session_state.api_key_index += 1
    st.toast(f"🔄 할당량 초과! {st.session_state.api_key_index + 1}번째 키로 전환합니다.")
    return get_youtube_client()

def get_channel_id_by_handle(youtube, handle):
    handle = handle.strip()
    clean_handle = handle if handle.startswith('@') else '@' + handle
    try:
        res = youtube.channels().list(forHandle=clean_handle, part='id').execute()
        return res['items'][0]['id'] if res.get('items') else None
    except: return None

# --- 4. 로그인 화면 ---
def login_page():
    st.title("🔐 YouTube Analyzer Login")
    t1, t2 = st.tabs(["로그인", "회원가입"])
    with t1:
        e = st.text_input("이메일", key="l_email")
        p = st.text_input("비밀번호", type="password", key="l_pw")
        if st.button("로그인"):
            try:
                res = supabase.auth.sign_in_with_password({"email": e, "password": p})
                st.session_state.user = res.user
                st.rerun()
            except: st.error("로그인 실패")
    with t2:
        ne = st.text_input("이메일", key="s_email")
        np = st.text_input("비밀번호", type="password", key="s_pw")
        if st.button("회원가입"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료!")

# --- 5. 메인 앱 ---
def main_app():
    # 사이드바
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 API 키 리스트 (줄바꿈 구분)", 
                               value="\n".join(st.session_state.user_api_keys), height=100).split('\n')
        if st.button("키 저장"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.session_state.api_key_index = 0
            st.success("저장 완료")
        
        st.divider()
        st.subheader("📥 구독 채널 50개씩 수집")
        target_handle = st.text_input("기준 채널 핸들 (@...)")
        group_name = st.text_input("그룹명", value="수집그룹")
        
        col_fetch1, col_fetch2 = st.columns(2)
        if col_fetch1.button("처음부터 50개"):
            st.session_state.next_page_token = None
            st.session_state.trigger_fetch = True
            
        if st.session_state.next_page_token and col_fetch2.button("다음 50개 가져오기"):
            st.session_state.trigger_fetch = True

        # 수집 실행 로직
        if st.session_state.get('trigger_fetch'):
            youtube = get_youtube_client()
            main_id = get_channel_id_by_handle(youtube, target_handle)
            if main_id:
                try:
                    res = youtube.subscriptions().list(
                        channelId=main_id, part='snippet', maxResults=50,
                        pageToken=st.session_state.next_page_token
                    ).execute()
                    
                    for s in res.get('items', []):
                        s_id = s['snippet']['resourceId']['channelId']
                        supabase.table('channels').upsert({
                            "user_id": st.session_state.user.id, "channel_id": s_id,
                            "channel_name": s['snippet']['title'], "category": group_name
                        }, on_conflict="channel_id").execute()
                    
                    st.session_state.next_page_token = res.get('nextPageToken')
                    st.sidebar.success(f"50개 저장 완료! (남은 페이지 토큰 존재)")
                except Exception as e: st.sidebar.error(f"오류: {e}")
            st.session_state.trigger_fetch = False

    # 메인 화면
    st.title("🎯 타겟 콘텐츠 정밀 분석기")
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리"])

    with tab_manage:
        # (기존 일괄 수정/삭제 로직 유지)
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.subheader(f"현재 등록된 채널: {len(df_db)}개")
            if st.button("🗑️ 전체 삭제"):
                supabase.table('channels').delete().eq("user_id", st.session_state.user.id).execute()
                st.rerun()
            st.data_editor(df_db[['channel_name', 'category']], use_container_width=True)

    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집하세요.")
        else:
            df_all = pd.DataFrame(res.data)
            with st.form("scan_form"):
                cats = st.multiselect("분석 그룹", options=sorted(df_all['category'].unique()), default=sorted(df_all['category'].unique()))
                v_format = st.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                time_map = {"12시간": 12, "24시간": 24, "48시간": 48, "1주일": 168}
                t_limit = st.selectbox("기간", list(time_map.keys()), index=1)
                min_v = st.number_input("최소 조회수", value=5000)
                start_btn = st.form_submit_button("🚀 분석 시작", type="primary")

            # 💡 정지 버튼 (폼 외부에 배치)
            if st.button("🛑 분석 중단 (정지)"):
                st.session_state.stop_analysis = True

            if start_btn:
                st.session_state.stop_analysis = False
                youtube = get_youtube_client()
                scan_list = df_all[df_all['category'].isin(cats)]
                results = []
                progress_text = st.empty()
                bar = st.progress(0)

                for i, ch in enumerate(scan_list.to_dict('records')):
                    # 💡 정지 체크
                    if st.session_state.stop_analysis:
                        st.warning("분석이 사용자에 의해 중단되었습니다.")
                        break

                    progress_text.text(f"분석 중: {ch['channel_name']} ({i+1}/{len(scan_list)})")
                    
                    # API 자동 전환 루프 (최대 API 키 개수만큼 재시도)
                    retry_count = 0
                    while retry_count < len(st.session_state.user_api_keys):
                        try:
                            v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                            v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                            
                            if v_ids:
                                d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet,contentDetails').execute()
                                for item in d_res.get('items', []):
                                    # ... (조회수, 시간, 포맷 필터링 로직 동일) ...
                                    pub_at = item['snippet']['publishedAt']
                                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(pub_at.replace('Z', '+00:00'))).total_seconds() / 3600
                                    if age_h > time_map[t_limit]: continue
                                    views = int(item['statistics'].get('viewCount', 0))
                                    if views < min_v: continue
                                    
                                    dur = isodate.parse_duration(item['contentDetails']['duration']).total_seconds()
                                    is_s = dur <= 60
                                    if v_format == "롱폼만" and is_s: continue
                                    if v_format == "숏폼만" and not is_s: continue

                                    results.append({
                                        "썸네일": item['snippet']['thumbnails']['default']['url'],
                                        "채널": item['snippet']['channelTitle'],
                                        "제목": item['snippet']['title'],
                                        "조회수": views,
                                        "VPH": round(views / max(age_h, 0.1), 1),
                                        "링크": f"https://youtu.be/{item['id']}"
                                    })
                            break # 성공 시 루프 탈출
                        except Exception as e:
                            if "quotaExceeded" in str(e):
                                youtube = switch_api_key()
                                retry_count += 1
                            else:
                                st.error(f"에러: {e}")
                                break
                    
                    bar.progress((i + 1) / len(scan_list))

                if results:
                    st.write(f"결과: {len(results)}건")
                    st.data_editor(pd.DataFrame(results).sort_values("VPH", ascending=False),
                                   column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()},
                                   use_container_width=True, hide_index=True)

if st.session_state.user is None: login_page()
else: main_app()
