import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Growth Manager", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. API 및 세션 상태 관리 ---
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'df_manage' not in st.session_state: st.session_state.df_manage = pd.DataFrame()

def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def get_channel_id_by_handle(youtube, handle):
    handle = handle.strip()
    clean_handle = handle if handle.startswith('@') else '@' + handle
    try:
        res = youtube.channels().list(forHandle=clean_handle, part='id').execute()
        return res['items'][0]['id'] if res.get('items') else None
    except: return None

# --- 3. 로그인 / 회원가입 ---
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
            except: st.error("로그인 정보를 확인해주세요.")
    with t2:
        ne = st.text_input("이메일", key="s_email")
        np = st.text_input("비밀번호", type="password", key="s_pw")
        if st.button("회원가입", key="s_btn"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료! 로그인 탭을 이용해주세요.")

# --- 4. 메인 앱 ---
def main_app():
    # [사이드바] API 키 및 구독 수집
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 API Keys (엔터 구분)", value="\n".join(st.session_state.user_api_keys), height=80).split('\n')
        if st.button("API 키 저장"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("적용됨")
        
        st.divider()
        st.subheader("📥 채널 수집 도구")
        target_handle = st.text_input("기준 채널 핸들 (@...)")
        group_name = st.text_input("저장할 그룹명", value="미분류")
        
        if st.button("모든 구독 리스트 DB 저장"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키를 입력하세요.")
            else:
                with st.spinner("수집 중..."):
                    main_id = get_channel_id_by_handle(youtube, target_handle)
                    if main_id:
                        next_token = None
                        total = 0
                        while True:
                            res = youtube.subscriptions().list(channelId=main_id, part='snippet', maxResults=50, pageToken=next_token).execute()
                            for s in res.get('items', []):
                                s_id = s['snippet']['resourceId']['channelId']
                                supabase.table('channels').upsert({
                                    "user_id": st.session_state.user.id, "channel_id": s_id,
                                    "channel_name": s['snippet']['title'], "category": group_name,
                                    "channel_url": f"https://youtube.com/channel/{s_id}"
                                }, on_conflict="channel_id").execute()
                                total += 1
                            next_token = res.get('nextPageToken')
                            if not next_token: break
                        st.success(f"총 {total}개 채널 저장 완료!")
                        if 'df_manage' in st.session_state: del st.session_state.df_manage
                    else: st.error("채널을 찾을 수 없습니다.")
        
        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # [메인 화면]
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리 및 리스트 수정"])

    # ⚙️ DB 관리 탭 (핵심 수정 부분)
    with tab_manage:
        st.subheader("⚙️ 채널 리스트 정밀 관리")
        
        # 최신 데이터 불러오기 및 세션 상태 동기화
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            
            if st.session_state.df_manage.empty or len(st.session_state.df_manage) != len(df_db):
                df_db.insert(0, "선택", False)
                st.session_state.df_manage = df_db

            # --- 선택 컨트롤 바 ---
            c1, c2, c3 = st.columns([1.5, 2.5, 1])
            with c1:
                st.write("**선택 도구**")
                col_sel1, col_sel2 = st.columns(2)
                if col_sel1.button("✅ 전체 선택"):
                    st.session_state.df_manage["선택"] = True
                    st.rerun()
                if col_sel2.button("❌ 전체 해제"):
                    st.session_state.df_manage["선택"] = False
                    st.rerun()
            
            with c2:
                st.write("**그룹별 선택**")
                all_cats = sorted(st.session_state.df_manage['category'].unique().tolist())
                target_cat = st.selectbox("카테고리 지정", ["선택하세요"] + all_cats, label_visibility="collapsed")
                if st.button("🎯 해당 그룹 모두 선택"):
                    if target_cat != "선택하세요":
                        st.session_state.df_manage.loc[st.session_state.df_manage['category'] == target_cat, "선택"] = True
                        st.rerun()
            
            # --- 데이터 에디터 ---
            edited_df = st.data_editor(
                st.session_state.df_manage[['선택', 'id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "선택": st.column_config.CheckboxColumn("선택", default=False),
                    "id": None, # ID 숨김
                    "channel_url": st.column_config.LinkColumn("링크")
                },
                key="db_editor_v3"
            )
            st.session_state.df_manage = edited_df # 상호작용 반영
            
            selected_rows = edited_df[edited_df["선택"] == True]
            st.write(f"현재 **{len(selected_rows)}개** 채널이 선택되었습니다.")

            st.divider()

            # --- 일괄 작업 섹션 ---
            st.subheader("🚀 선택 항목 일괄 작업")
            bc1, bc2 = st.columns(2)
            
            with bc1:
                new_cat_input = st.text_input("변경할 새 카테고리명", placeholder="예: 우수_벤치마킹")
                if st.button("🏷️ 선택 항목 카테고리 일괄 변경"):
                    if not selected_rows.empty and new_cat_input:
                        ids = selected_rows['id'].tolist()
                        with st.spinner("변경 중..."):
                            for i in ids:
                                supabase.table('channels').update({"category": new_cat_input}).eq("id", i).execute()
                        st.success("변경 완료!")
                        if 'df_manage' in st.session_state: del st.session_state.df_manage
                        st.rerun()
            
            with bc2:
                st.write("---") # 높이 맞춤용
                if st.button("🗑️ 선택 항목 일괄 삭제", type="secondary"):
                    if not selected_rows.empty:
                        ids = selected_rows['id'].tolist()
                        with st.spinner("삭제 중..."):
                            for i in ids:
                                supabase.table('channels').delete().eq("id", i).execute()
                        st.success("삭제 완료!")
                        if 'df_manage' in st.session_state: del st.session_state.df_manage
                        st.rerun()
        else:
            st.info("저장된 채널이 없습니다.")

    # 🔍 콘텐츠 분석 검색 탭
    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집해주세요.")
        else:
            df_all = pd.DataFrame(res.data)
            with st.form("scan_filter"):
                st.subheader("⚙️ 분석 조건 설정")
                sc1, sc2, sc3 = st.columns([2, 1, 1])
                target_cats = sc1.multiselect("분석 그룹", options=sorted(df_all['category'].unique()), default=sorted(df_all['category'].unique()))
                v_format = sc2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주": 168, "2주": 336, "3주": 504, "한달": 720, "전체": 999999}
                t_label = sc3.selectbox("업로드 기간", list(time_opts.keys()), index=4)
                
                sc4, sc5, sc6 = st.columns(3)
                min_v = sc4.number_input("최소 조회수", value=5000)
                min_s = sc5.number_input("최소 구독자", value=0)
                max_s = sc6.number_input("최대 구독자 (0=무제한)", value=0)
                
                if st.form_submit_button("🚀 분석 시작 (50개 단위)", type="primary"):
                    youtube = get_youtube_client()
                    if not youtube: st.warning("API 키를 넣으세요."); return
                    
                    # 50개씩 끊어서 분석하는 로직 (기존 동일)
                    scan_list = df_all[df_all['category'].isin(target_cats)].head(50) 
                    results = []
                    bar = st.progress(0)
                    for i, ch in enumerate(scan_list.to_dict('records')):
                        try:
                            v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                            v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                            if v_ids:
                                d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet,contentDetails').execute()
                                for item in d_res.get('items', []):
                                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(item['snippet']['publishedAt'].replace('Z', '+00:00'))).total_seconds() / 3600
                                    if age_h > time_opts[t_label]: continue
                                    views = int(item['statistics'].get('viewCount', 0))
                                    if views < min_v: continue
                                    is_s = isodate.parse_duration(item['contentDetails']['duration']).total_seconds() <= 60
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
                        except: pass
                        bar.progress((i + 1) / len(scan_list))
                    
                    if results:
                        st.data_editor(pd.DataFrame(results).sort_values("VPH", ascending=False),
                                       column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()},
                                       use_container_width=True, hide_index=True)

# --- 실행 ---
if st.session_state.user is None: login_page()
else: main_app()
