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

# --- 2. API 및 세션 상태 초기화 ---
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'df_manage' not in st.session_state: st.session_state.df_manage = pd.DataFrame()
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = []
if 'current_batch_index' not in st.session_state: st.session_state.current_batch_index = 0
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False

# --- 3. API 엔진 및 유틸리티 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
    """할당량 초과 시 자동으로 다음 키로 교체"""
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

# --- 5. 메인 앱 서비스 ---
def main_app():
    # [사이드바] API 키 및 무제한 구독 수집
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
                with st.spinner("모든 구독 채널 긁어오는 중..."):
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
                        st.session_state.df_manage = pd.DataFrame() # 관리 데이터 초기화
                    else: st.error("채널을 찾을 수 없습니다.")
        
        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # [메인 화면]
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리 및 리스트 수정"])

    # --- [탭 2] DB 관리 (일괄 선택 및 제어 강화) ---
    with tab_manage:
        st.subheader("⚙️ 채널 리스트 정밀 관리")
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            
            # 관리용 데이터프레임 초기화 및 동기화
            if st.session_state.df_manage.empty or len(st.session_state.df_manage) != len(df_db):
                df_db.insert(0, "선택", False)
                st.session_state.df_manage = df_db

            # --- 선택 컨트롤 레이아웃 ---
            c1, c2 = st.columns([2, 3])
            with c1:
                st.write("**범위 선택**")
                col_s1, col_s2 = st.columns(2)
                if col_s1.button("✅ 전체 선택"):
                    st.session_state.df_manage["선택"] = True
                    st.rerun()
                if col_s2.button("❌ 전체 해제"):
                    st.session_state.df_manage["선택"] = False
                    st.rerun()
            
            with c2:
                st.write("**카테고리별 선택**")
                all_cats = sorted(st.session_state.df_manage['category'].unique().tolist())
                target_cat = st.selectbox("카테고리 지정", ["그룹을 선택하세요"] + all_cats, label_visibility="collapsed")
                if st.button("🎯 해당 그룹 모두 선택"):
                    if target_cat != "그룹을 선택하세요":
                        st.session_state.df_manage.loc[st.session_state.df_manage['category'] == target_cat, "선택"] = True
                        st.rerun()

            # 데이터 에디터
            edited_df = st.data_editor(
                st.session_state.df_manage[['선택', 'id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True, hide_index=True,
                column_config={"선택": st.column_config.CheckboxColumn("선택"), "id": None, "channel_url": st.column_config.LinkColumn("링크")},
                key="manage_editor"
            )
            st.session_state.df_manage = edited_df
            selected_rows = edited_df[edited_df["선택"] == True]
            st.write(f"현재 **{len(selected_rows)}개** 채널이 선택되었습니다.")

            # --- 일괄 처리 버튼 ---
            st.divider()
            bc1, bc2 = st.columns(2)
            with bc1:
                new_cat = st.text_input("변경할 카테고리명 입력", placeholder="예: 벤치마킹_A팀")
                if st.button("🏷️ 선택 항목 카테고리 일괄 수정"):
                    if not selected_rows.empty and new_cat:
                        ids = selected_rows['id'].tolist()
                        for i in ids: supabase.table('channels').update({"category": new_cat}).eq("id", i).execute()
                        st.success("변경 완료!")
                        st.session_state.df_manage = pd.DataFrame()
                        st.rerun()
            with bc2:
                st.write("---")
                if st.button("🗑️ 선택 항목 일괄 삭제", type="secondary"):
                    if not selected_rows.empty:
                        ids = selected_rows['id'].tolist()
                        for i in ids: supabase.table('channels').delete().eq("id", i).execute()
                        st.success("삭제 완료!")
                        st.session_state.df_manage = pd.DataFrame()
                        st.rerun()
        else: st.info("데이터가 없습니다.")

    # --- [탭 1] 콘텐츠 분석 검색 (배치 분석 및 정지 기능) ---
    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집해주세요.")
        else:
            df_all = pd.DataFrame(res.data)
            with st.form("filter_form"):
                st.subheader("⚙️ 분석 조건 설정")
                f1, f2, f3 = st.columns([2, 1, 1])
                target_groups = f1.multiselect("분석 그룹", options=sorted(df_all['category'].unique()), default=sorted(df_all['category'].unique()))
                v_format = f2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                
                time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주": 168, "2주": 336, "3주": 504, "한달": 720, "전체": 999999}
                t_label = f3.selectbox("업로드 기간", list(time_opts.keys()), index=4)
                
                f4, f5, f6 = st.columns(3)
                min_v = f4.number_input("최소 조회수", value=5000)
                min_s = f5.number_input("최소 구독자", value=0)
                max_s = f6.number_input("최대 구독자 (0=무제한)", value=0)
                
                run_btn = st.form_submit_button("🚀 분석 시작 (50개 단위)", type="primary")

            # 제어 버튼
            c_btn1, c_btn2 = st.columns(2)
            if c_btn1.button("🛑 분석 중단"): st.session_state.stop_analysis = True
            if c_btn2.button("🧹 결과 초기화"):
                st.session_state.analysis_results = []
                st.session_state.current_batch_index = 0
                st.rerun()

            if run_btn:
                st.session_state.stop_analysis = False
                youtube = get_youtube_client()
                if not youtube: st.warning("API 키를 입력하세요."); return

                full_list = df_all[df_all['category'].isin(target_groups)].to_dict('records')
                start_idx = st.session_state.current_batch_index
                end_idx = min(start_idx + 50, len(full_list))
                current_batch = full_list[start_idx:end_idx]

                if not current_batch: st.success("모든 리스트 분석 완료!"); return

                results = []
                bar = st.progress(0)
                status = st.empty()
                limit_h = time_opts[t_label]

                for i, ch in enumerate(current_batch):
                    if st.session_state.stop_analysis: 
                        status.warning("사용자에 의해 분석이 중단되었습니다.")
                        break
                    
                    status.text(f"분석 중: {ch['channel_name']} ({start_idx + i + 1}/{len(full_list)})")
                    
                    try:
                        # 1. 채널 정보 및 구독자 필터
                        ch_res = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                        subs = int(ch_res['items'][0]['statistics'].get('subscriberCount', 0))
                        if (min_s > 0 and subs < min_s) or (max_s > 0 and subs > max_s): continue

                        # 2. 영상 스캔 (최대 50개)
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
                                    "링크": f"https://youtu.be/{item['id']}"
                                })
                    except Exception as e:
                        if "quotaExceeded" in str(e): youtube = switch_api_key()
                    bar.progress((i + 1) / len(current_batch))

                st.session_state.analysis_results.extend(results)
                st.session_state.current_batch_index = end_idx
                st.rerun() # 결과 반영을 위한 새로고침

            # 결과 출력
            if st.session_state.analysis_results:
                st.subheader(f"📊 분석 결과 ({len(st.session_state.analysis_results)}건)")
                df_res = pd.DataFrame(st.session_state.analysis_results).sort_values("VPH", ascending=False)
                st.data_editor(df_res, column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)

if st.session_state.user is None: login_page()
else: main_app()
