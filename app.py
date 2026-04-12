import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Strategy Pro", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. API 엔진 로직 ---
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
        st.toast("🔄 할당량 초과! 다음 키로 전환합니다.")
        return True
    return False

def get_channel_id_by_handle(youtube, handle):
    handle = handle.strip()
    clean_handle = handle if handle.startswith('@') else '@' + handle
    try:
        res = youtube.channels().list(forHandle=clean_handle, part='id').execute()
        return res['items'][0]['id'] if res.get('items') else None
    except: return None

# --- 3. 세션 및 로그인 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []

def login_page():
    st.title("🔐 YouTube Analyzer")
    t1, t2 = st.tabs(["로그인", "회원가입"])
    with t1:
        e = st.text_input("이메일", key="l_email")
        p = st.text_input("비밀번호", type="password", key="l_pw")
        if st.button("로그인", key="l_btn"):
            try:
                res = supabase.auth.sign_in_with_password({"email": e, "password": p})
                st.session_state.user = res.user
                st.rerun()
            except: st.error("로그인 정보가 틀렸습니다.")
    with t2:
        ne = st.text_input("이메일", key="s_email")
        np = st.text_input("비밀번호", type="password", key="s_pw")
        if st.button("회원가입", key="s_btn"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료! 로그인 탭에서 로그인해주세요.")

# --- 4. 메인 앱 서비스 ---
def main_app():
    # 사이드바: 수집 및 API 설정
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 API Keys (엔터 구분)", 
                               value="\n".join(st.session_state.user_api_keys), height=80).split('\n')
        if st.button("키 리스트 적용"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("적용됨")
        
        st.divider()
        st.subheader("📥 채널 수집 도구")
        target_handle = st.text_input("기준 채널 핸들 (@비디오26 등)")
        custom_group = st.text_input("저장할 그룹명", value="미분류")
        
        if st.button("구독 목록 DB에 저장"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키를 입력하세요.")
            else:
                with st.spinner("수집 중..."):
                    main_id = get_channel_id_by_handle(youtube, target_handle)
                    if main_id:
                        res = youtube.subscriptions().list(channelId=main_id, part='snippet', maxResults=50).execute()
                        subs = res.get('items', [])
                        for s in subs:
                            s_id = s['snippet']['resourceId']['channelId']
                            supabase.table('channels').upsert({
                                "user_id": st.session_state.user.id,
                                "channel_id": s_id,
                                "channel_name": s['snippet']['title'],
                                "channel_url": f"https://youtube.com/channel/{s_id}",
                                "category": custom_group
                            }, on_conflict="channel_id").execute()
                        st.success(f"{len(subs)}개 채널 저장 완료!")
                    else: st.error("채널을 찾을 수 없습니다.")
        
        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # 메인 화면 구성
    st.title("🎯 알고리즘 타겟 전략 시스템")
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ 채널 및 그룹 관리"])

    # --- [탭 2] 채널 및 그룹 관리 (일괄 처리 기능 강화) ---
    with tab_manage:
        st.subheader("🛠️ 그룹 일괄 관리 도구")
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            all_cats = sorted(df_db['category'].unique())

            # 일괄 처리 섹션
            with st.expander("🚀 카테고리별 일괄 수정/삭제 (전체 선택 가능)", expanded=True):
                c1, c2 = st.columns(2)
                mode = c1.radio("작업 범위", ["카테고리별 일괄 선택", "전체 채널 선택"])
                
                if mode == "카테고리별 일괄 선택":
                    target_cats = c1.multiselect("대상 카테고리를 고르세요", options=all_cats)
                    affected_count = len(df_all_target := df_db[df_db['category'].isin(target_cats)])
                else:
                    target_cats = all_cats
                    affected_count = len(df_db)
                
                st.info(f"선택된 작업 대상: **{affected_count}개 채널**")

                st.divider()
                
                new_name = c2.text_input("변경할 새 그룹명 입력", placeholder="예: IT전략팀")
                if c2.button("🏷️ 선택 대상 그룹명 일괄 변경"):
                    if mode == "카테고리별 일괄 선택" and not target_cats:
                        st.warning("대상을 먼저 선택하세요.")
                    else:
                        with st.spinner("업데이트 중..."):
                            for cat in target_cats:
                                supabase.table('channels').update({"category": new_name}).eq("category", cat).eq("user_id", st.session_state.user.id).execute()
                        st.success("일괄 변경 완료!"); st.rerun()

                if st.button("🗑️ 선택 대상 전체 삭제 (주의!)", type="secondary"):
                    with st.spinner("삭제 중..."):
                        if mode == "카테고리별 일괄 선택":
                            for cat in target_cats:
                                supabase.table('channels').delete().eq("category", cat).eq("user_id", st.session_state.user.id).execute()
                        else:
                            supabase.table('channels').delete().eq("user_id", st.session_state.user.id).execute()
                    st.success("일괄 삭제되었습니다."); st.rerun()

            st.divider()
            st.subheader("📝 개별 채널 수정")
            edited = st.data_editor(df_db[['id', 'channel_name', 'category', 'channel_url']], use_container_width=True, key="db_edit")
            if st.button("💾 개별 수정사항 저장"):
                for _, row in edited.iterrows():
                    supabase.table('channels').update({"channel_name": row['channel_name'], "category": row['category']}).eq("id", row['id']).execute()
                st.success("저장 완료!"); st.rerun()
        else: st.info("저장된 채널이 없습니다.")

    # --- [탭 1] 콘텐츠 분석 검색 ---
    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집해주세요.")
        else:
            df_all = pd.DataFrame(res.data)
            user_groups = sorted(df_all['category'].unique())

            with st.form("scan_filter"):
                st.subheader("⚙️ 스캔 필터")
                sc1, sc2, sc3 = st.columns([2, 1, 1])
                scan_cats = sc1.multiselect("분석할 그룹 선택", options=user_groups, default=user_groups)
                v_format = sc2.selectbox("영상 포맷", ["전체", "롱폼만", "숏폼만"])
                
                time_map = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주일": 168, "한달": 720}
                t_label = sc3.selectbox("업로드 기간", list(time_map.keys()), index=4)
                
                sc4, sc5, sc6 = st.columns(3)
                min_v = sc4.number_input("최소 조회수", value=5000)
                min_s = sc5.number_input("최소 구독자", value=0)
                max_s = sc6.number_input("최대 구독자 (0=무제한)", value=0)
                
                if st.form_submit_button("🚀 분석 시작", type="primary"):
                    youtube = get_youtube_client()
                    if not youtube: st.warning("API 키를 넣으세요."); return

                    scan_list = df_all[df_all['category'].isin(scan_cats)]
                    results = []
                    bar = st.progress(0)
                    limit_h = time_map[t_label]

                    for i, ch in enumerate(scan_list.to_dict('records')):
                        try:
                            ch_res = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                            subs = int(ch_res['items'][0]['statistics'].get('subscriberCount', 0))
                            if (min_s > 0 and subs < min_s) or (max_s > 0 and subs > max_s): continue

                            v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                            v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                            
                            if v_ids:
                                d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet,contentDetails').execute()
                                for item in d_res.get('items', []):
                                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))).total_seconds() / 3600
                                    if age_h > limit_h: continue
                                    
                                    views = int(item['statistics'].get('viewCount', 0))
                                    if views < min_v: continue
                                    
                                    is_s = isodate.parse_duration(item['contentDetails']['duration']).total_seconds() <= 60
                                    if v_format == "롱폼만" and is_s: continue
                                    if v_format == "숏폼만" and not is_s: continue

                                    results.append({
                                        "썸네일": item['snippet']['thumbnails']['default']['url'],
                                        "채널": item['snippet']['channelTitle'],
                                        "구독자": subs,
                                        "제목": item['snippet']['title'],
                                        "조회수": views,
                                        "VPH": round(views / max(age_h, 0.1), 1),
                                        "포맷": "숏폼" if is_s else "롱폼",
                                        "링크": f"https://youtu.be/{item['id']}"
                                    })
                        except Exception as e: handle_api_error(e)
                        bar.progress((i + 1) / len(scan_list))

                    if results:
                        st.data_editor(pd.DataFrame(results).sort_values("VPH", ascending=False),
                                       column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()},
                                       use_container_width=True, hide_index=True)
                    else: st.warning("결과 없음")

# --- 실행 ---
if st.session_state.user is None: login_page()
else: main_app()
