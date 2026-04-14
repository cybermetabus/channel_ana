import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Target Analyzer", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 관리 ---
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = []
if 'current_batch_index' not in st.session_state: st.session_state.current_batch_index = 0

# --- 3. API 엔진 및 유틸리티 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
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

# --- 4. 로그인 / 회원가입 ---
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
            except: st.error("로그인 정보를 확인해주세요.")
    with t2:
        ne = st.text_input("가입 이메일", key="s_email")
        np = st.text_input("가입 비밀번호", type="password", key="s_pw")
        if st.button("가입하기"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료! 로그인 해주세요.")

# --- 5. 메인 앱 ---
def main_app():
    # 사이드바: API 설정 및 무제한 구독 수집
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 API 키 (엔터 구분)", 
                               value="\n".join(st.session_state.user_api_keys), height=100).split('\n')
        if st.button("API 키 저장"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("적용됨")
        
        st.divider()
        st.subheader("📥 전체 구독 채널 수집")
        target_handle = st.text_input("기준 채널 핸들 (@...)")
        group_name = st.text_input("저장할 그룹명", value="수집그룹")
        
        if st.button("모든 구독 리스트 한 번에 저장"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키가 없습니다.")
            else:
                with st.spinner("모든 리스트를 긁어오는 중..."):
                    main_id = get_channel_id_by_handle(youtube, target_handle)
                    if main_id:
                        next_token = None
                        total_count = 0
                        while True:
                            res = youtube.subscriptions().list(
                                channelId=main_id, part='snippet', maxResults=50, pageToken=next_token
                            ).execute()
                            for s in res.get('items', []):
                                s_id = s['snippet']['resourceId']['channelId']
                                supabase.table('channels').upsert({
                                    "user_id": st.session_state.user.id, "channel_id": s_id,
                                    "channel_name": s['snippet']['title'], "category": group_name,
                                    "channel_url": f"https://youtube.com/channel/{s_id}"
                                }, on_conflict="channel_id").execute()
                                total_count += 1
                            next_token = res.get('nextPageToken')
                            if not next_token: break
                        st.success(f"총 {total_count}개 채널 저장 완료!")
                    else: st.error("채널을 찾을 수 없습니다.")

        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # 메인 화면
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리"])

    # DB 관리 탭 (생략 - 기존 일괄 수정/삭제 로직 유지)
    with tab_manage:
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.subheader(f"등록된 채널: {len(df_db)}개")
            st.data_editor(df_db[['channel_name', 'category']], use_container_width=True)

    # 콘텐츠 분석 검색 탭 (핵심 수정)
    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집해주세요.")
        else:
            df_all = pd.DataFrame(res.data)
            with st.form("filter_form"):
                st.subheader("⚙️ 분석 조건 설정")
                c1, c2, c3 = st.columns([2, 1, 1])
                target_cats = c1.multiselect("분석 그룹", options=sorted(df_all['category'].unique()), default=sorted(df_all['category'].unique()))
                v_format = c2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                
                # 기간 옵션 정밀화
                time_opts = {
                    "12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, 
                    "1주": 168, "2주": 336, "3주": 504, "한달": 720, "전체": 999999
                }
                t_label = c3.selectbox("업로드 기간", list(time_opts.keys()), index=4)
                
                c4, c5, c6 = st.columns(3)
                min_view = c4.number_input("최소 조회수", value=5000)
                min_sub = c5.number_input("최소 구독자수", value=0)
                max_sub = c6.number_input("최대 구독자수 (0=무제한)", value=0)
                
                # 분석 시작 버튼
                start_analysis = st.form_submit_button("🚀 검색 및 50개 단위 분석 시작", type="primary")

            col_btn1, col_btn2 = st.columns(2)
            if col_btn1.button("🛑 분석 중단"):
                st.session_state.stop_analysis = True
            if col_btn2.button("🧹 결과 초기화"):
                st.session_state.analysis_results = []
                st.session_state.current_batch_index = 0
                st.rerun()

            if start_analysis:
                st.session_state.stop_analysis = False
                # 선택된 카테고리의 전체 채널 리스트
                full_scan_list = df_all[df_all['category'].isin(target_cats)].to_dict('records')
                
                # 💡 50개씩 쪼개기 로직
                start_idx = st.session_state.current_batch_index
                end_idx = min(start_idx + 50, len(full_scan_list))
                current_batch = full_scan_list[start_idx:end_idx]

                if not current_batch:
                    st.success("모든 리스트 분석이 완료되었습니다!")
                else:
                    youtube = get_youtube_client()
                    st.info(f"분석 진행: {start_idx + 1} ~ {end_idx} (총 {len(full_scan_list)}개 중)")
                    
                    bar = st.progress(0)
                    batch_results = []

                    for i, ch in enumerate(current_batch):
                        if st.session_state.stop_analysis: break
                        
                        try:
                            # 1. 채널 정보 및 구독자 필터
                            ch_res = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                            subs = int(ch_res['items'][0]['statistics'].get('subscriberCount', 0))
                            
                            if (min_sub > 0 and subs < min_sub) or (max_sub > 0 and subs > max_sub): continue

                            # 2. 채널당 최대 50개 영상 스캔
                            v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                            v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                            
                            if v_ids:
                                d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet,contentDetails').execute()
                                for item in d_res.get('items', []):
                                    # 시간 필터
                                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))).total_seconds() / 3600
                                    if age_h > time_opts[t_label]: continue
                                    # 조회수 필터
                                    views = int(item['statistics'].get('viewCount', 0))
                                    if views < min_view: continue
                                    # 포맷 필터
                                    dur = isodate.parse_duration(item['contentDetails']['duration']).total_seconds()
                                    is_s = dur <= 60
                                    if v_format == "롱폼만" and is_s: continue
                                    if v_format == "숏폼만" and not is_s: continue

                                    batch_results.append({
                                        "썸네일": item['snippet']['thumbnails']['default']['url'],
                                        "채널": item['snippet']['channelTitle'],
                                        "구독자": subs,
                                        "제목": item['snippet']['title'],
                                        "조회수": views,
                                        "VPH": round(views / max(age_h, 0.1), 1),
                                        "링크": f"https://youtu.be/{item['id']}"
                                    })
                        except Exception as e:
                            if "quotaExceeded" in str(e): youtube = switch_api_key()
                        
                        bar.progress((i + 1) / len(current_batch))

                    # 결과 합치기 및 인덱스 업데이트
                    st.session_state.analysis_results.extend(batch_results)
                    st.session_state.current_batch_index = end_idx
                    
                    if not st.session_state.stop_analysis:
                        st.success(f"현재 배치({start_idx+1}~{end_idx}) 완료! 다시 버튼을 누르면 다음 50개를 분석합니다.")

            # 전체 누적 결과 출력
            if st.session_state.analysis_results:
                st.subheader(f"📊 누적 분석 결과 ({len(st.session_state.analysis_results)}건)")
                df_res = pd.DataFrame(st.session_state.analysis_results).sort_values("VPH", ascending=False)
                st.data_editor(df_res, column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)

if st.session_state.user is None: login_page()
else: main_app()
