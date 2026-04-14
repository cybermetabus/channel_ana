import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Growth Manager", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 (데이터 보존의 핵심) ---
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = []
if 'current_batch_index' not in st.session_state: st.session_state.current_batch_index = 0
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False

# --- 3. API 엔진 로직 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
    st.session_state.api_key_index += 1
    st.toast(f"🔄 할당량 초과! {st.session_state.api_key_index + 1}번째 키로 전환.")
    return get_youtube_client()

# --- 4. 메인 앱 ---
def main_app():
    # 사이드바 (API/수집)
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 API Keys", value="\n".join(st.session_state.user_api_keys)).split('\n')
        if st.button("키 저장"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("적용됨")
        
        st.divider()
        st.subheader("📥 채널 수집")
        target_handle = st.text_input("기준 핸들 (@...)")
        group_name = st.text_input("그룹명", value="미분류")
        if st.button("모든 구독 리스트 저장"):
            youtube = get_youtube_client()
            main_id = youtube.channels().list(forHandle=target_handle, part='id').execute()['items'][0]['id']
            next_token = None
            while True:
                res = youtube.subscriptions().list(channelId=main_id, part='snippet', maxResults=50, pageToken=next_token).execute()
                for s in res.get('items', []):
                    s_id = s['snippet']['resourceId']['channelId']
                    supabase.table('channels').upsert({"user_id": st.session_state.user.id, "channel_id": s_id, "channel_name": s['snippet']['title'], "category": group_name}, on_conflict="channel_id").execute()
                next_token = res.get('nextPageToken')
                if not next_token: break
            st.success("수집 완료")

    # 메인 화면
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석", "⚙️ DB 관리"])

    with tab_manage:
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.subheader(f"등록된 채널: {len(df_db)}개")
            # 일괄 삭제/수정 로직 (생략 - 기존과 동일)

    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집하세요.")
        else:
            df_all = pd.DataFrame(res.data)
            # 검색 조건 설정 (st.form을 쓰지 않아야 '연속 분석' 시 상태 유지가 더 쉽습니다)
            st.subheader("⚙️ 분석 조건 설정")
            c1, c2, c3 = st.columns([2, 1, 1])
            target_cats = c1.multiselect("분석 그룹", options=sorted(df_all['category'].unique()), default=sorted(df_all['category'].unique()))
            v_format = c2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
            time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주": 168, "한달": 720, "전체": 999999}
            t_limit = c3.selectbox("업로드 기간", list(time_opts.keys()), index=4)
            
            sc1, sc2, sc3 = st.columns(3)
            min_v = sc1.number_input("최소 조회수", value=5000)
            min_s = sc2.number_input("최소 구독자", value=0)
            max_s = sc3.number_input("최대 구독자 (0=무제한)", value=0)

            # --- 제어 버튼 영역 ---
            btn_c1, btn_c2, btn_c3 = st.columns(3)
            start_batch = btn_c1.button("🚀 다음 50개 분석 시작", type="primary")
            stop_btn = btn_c2.button("🛑 분석 중단")
            clear_btn = btn_c3.button("🧹 결과 전체 초기화")

            if clear_btn:
                st.session_state.analysis_results = []
                st.session_state.current_batch_index = 0
                st.rerun()

            if stop_btn: st.session_state.stop_analysis = True

            # --- 분석 로직 ---
            if start_batch:
                st.session_state.stop_analysis = False
                youtube = get_youtube_client()
                full_list = df_all[df_all['category'].isin(target_groups if 'target_groups' in locals() else target_cats)].to_dict('records')
                
                start_idx = st.session_state.current_batch_index
                end_idx = min(start_idx + 50, len(full_list))
                current_batch = full_list[start_idx:end_idx]

                if not current_batch:
                    st.success("모든 채널 분석이 끝났습니다!")
                else:
                    bar = st.progress(0)
                    status = st.empty()
                    batch_data = []

                    for i, ch in enumerate(current_batch):
                        if st.session_state.stop_analysis: break
                        status.text(f"분석 중: {ch['channel_name']} ({start_idx + i + 1}/{len(full_list)})")
                        
                        try:
                            # 채널 및 영상 스캔 (최대 50개)
                            v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                            v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                            
                            if v_ids:
                                d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet,contentDetails').execute()
                                for item in d_res.get('items', []):
                                    # 필터링 로직
                                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))).total_seconds() / 3600
                                    if age_h > time_opts[t_limit]: continue
                                    views = int(item['statistics'].get('viewCount', 0))
                                    if views < min_v: continue
                                    
                                    dur = isodate.parse_duration(item['contentDetails']['duration']).total_seconds()
                                    is_s = dur <= 60
                                    if v_format == "롱폼만" and is_s: continue
                                    if v_format == "숏폼만" and not is_s: continue

                                    batch_data.append({
                                        "썸네일": item['snippet']['thumbnails']['default']['url'],
                                        "채널": item['snippet']['channelTitle'],
                                        "제목": item['snippet']['title'],
                                        "조회수": views,
                                        "VPH": round(views / max(age_h, 0.1), 1),
                                        "링크": f"https://youtu.be/{item['id']}"
                                    })
                        except Exception as e:
                            if "quotaExceeded" in str(e): youtube = switch_api_key()
                        bar.progress((i + 1) / len(current_batch))

                    # 💡 핵심: 기존 결과에 새로운 배치를 누적함
                    st.session_state.analysis_results.extend(batch_data)
                    st.session_state.current_batch_index = end_idx
                    st.rerun() # 화면을 새로고침하여 누적된 표를 보여줌

            # --- 결과 출력 영역 (분석 중이 아닐 때도 항상 보임) ---
            if st.session_state.analysis_results:
                st.subheader(f"📊 누적 분석 결과 ({len(st.session_state.analysis_results)}건)")
                st.info(f"현재 {st.session_state.current_batch_index}개 채널까지 분석 완료되었습니다.")
                
                # 데이터프레임 생성 및 중복 제거 (혹시 모를 중복 방지)
                df_res = pd.DataFrame(st.session_state.analysis_results).drop_duplicates(subset=['링크'])
                
                # 💡 사용자가 정렬하기 편하게 기본적으로 VPH 내림차순 정렬
                df_res = df_res.sort_values("VPH", ascending=False)
                
                st.data_editor(
                    df_res,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(),
                        "링크": st.column_config.LinkColumn()
                    },
                    use_container_width=True,
                    hide_index=True,
                    key="final_table" # 고유 키를 주어 정렬 상태 유지 도움
                )

# --- 로그인 체크 후 실행 ---
if st.session_state.user is None:
    from supabase import create_client
    # (로그인 페이지 로직 생략 - 기존과 동일)
    login_page()
else:
    main_app()
