import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time
import re

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Target Analyzer Pro", layout="wide")

@st.cache_resource
def init_connection():
    # Streamlit Secrets에 SUPABASE_URL과 SUPABASE_KEY가 설정되어 있어야 합니다.
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 관리 (데이터 보존의 핵심) ---
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = []
if 'current_batch_index' not in st.session_state: st.session_state.current_batch_index = 0
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False
if 'selected_ids' not in st.session_state: st.session_state.selected_ids = set()

# --- 3. API 엔진 및 유틸리티 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
    st.session_state.api_key_index += 1
    st.toast(f"🔄 API 할당량 초과! {st.session_state.api_key_index + 1}번째 키로 전환합니다.")
    return get_youtube_client()

def get_channel_id_strong(youtube, input_text):
    """핸들(@), 채널ID(UC...), URL 주소 모두 인식하는 강력한 함수"""
    if not input_text: return None
    input_text = input_text.strip()

    # 1. 주소 내 UC... ID 직접 추출 (가장 확실함)
    id_match = re.search(r'(UC[\w-]{22})', input_text)
    if id_match:
        ch_id = id_match.group(1)
        try:
            res = youtube.channels().list(id=ch_id, part='id').execute()
            if res.get('items'): return res['items'][0]['id']
        except: pass

    # 2. 핸들(@) 추출 및 조회
    handle_match = re.search(r'(@[\w.-]+)', input_text)
    target_handle = handle_match.group(1) if handle_match else (input_text if input_text.startswith('@') else '@' + input_text)
    
    try:
        res = youtube.channels().list(forHandle=target_handle, part='id').execute()
        if res.get('items'): return res['items'][0]['id']
    except: pass

    # 3. 최후의 수단: 검색 기능을 이용한 채널 찾기
    try:
        s_res = youtube.search().list(q=input_text, type='channel', part='id', maxResults=1).execute()
        if s_res.get('items'): return s_res['items'][0]['id']['channelId']
    except: pass
    return None

# --- 4. 로그인 / 회원가입 화면 ---
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
            except: st.error("계정 정보를 다시 확인해주세요.")
    with t2:
        ne = st.text_input("가입용 이메일", key="s_email")
        np = st.text_input("가입용 비밀번호", type="password", key="s_pw")
        if st.button("회원가입", key="s_btn"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("회원가입 성공! 로그인 탭에서 접속하세요.")

# --- 5. 메인 앱 서비스 ---
def main_app():
    # [사이드바]
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("🔑 API 키 (엔터로 구분)", value="\n".join(st.session_state.user_api_keys), height=80).split('\n')
        if st.button("API 키 저장/갱신"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("키 리스트가 저장되었습니다.")
        
        st.divider()
        st.subheader("📥 채널 수집 도구")
        target_input = st.text_input("기준 핸들 또는 URL (@...)")
        group_name = st.text_input("저장할 그룹명", value="미분류")
        
        if st.button("모든 구독 채널 리스트 저장"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키를 먼저 설정하세요.")
            else:
                with st.spinner("채널 고유 ID 분석 중..."):
                    main_id = get_channel_id_strong(youtube, target_input)
                    if main_id:
                        st.sidebar.info(f"✅ 채널 확인: {main_id}")
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
                        st.success(f"총 {total}개 채널이 '{group_name}' 그룹에 저장되었습니다.")
                    else: st.error("채널을 찾을 수 없습니다. 주소나 핸들을 확인하세요.")

        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # [메인 화면 탭]
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리 및 일괄 수정"])

    # ⚙️ DB 관리 탭
    with tab_manage:
        st.subheader("⚙️ 채널 리스트 정밀 관리")
        res = supabase.table('channels').select("*").eq("user_id", st.session_state.user.id).execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            all_cats = sorted(df_db['category'].unique().tolist())

            c_db1, c_db2 = st.columns([2, 3])
            with c_db1:
                st.write("**범위 선택**")
                sel_b1, sel_b2 = st.columns(2)
                if sel_b1.button("✅ 모든 데이터 선택"):
                    st.session_state.selected_ids = set(df_db['id'].tolist())
                    st.rerun()
                if sel_b2.button("❌ 선택 전체 해제"):
                    st.session_state.selected_ids = set()
                    st.rerun()
            
            with c_db2:
                st.write("**카테고리별 일괄 선택**")
                bulk_cat = st.selectbox("카테고리 선택", ["직접 고르세요"] + all_cats, label_visibility="collapsed")
                if st.button("🎯 해당 카테고리 모두 선택"):
                    if bulk_cat != "직접 고르세요":
                        st.session_state.selected_ids.update(set(df_db[df_db['category'] == bulk_cat]['id'].tolist()))
                        st.rerun()

            st.divider()
            df_db['선택'] = df_db['id'].apply(lambda x: x in st.session_state.selected_ids)
            edited_db = st.data_editor(
                df_db[['선택', 'id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True, hide_index=True,
                column_config={"선택": st.column_config.CheckboxColumn("선택"), "id": None, "channel_url": st.column_config.LinkColumn("링크")},
                key="manage_editor_v6"
            )
            st.session_state.selected_ids = set(edited_db[edited_df['선택'] == True]['id'].tolist())
            st.write(f"현재 **{len(st.session_state.selected_ids)}개** 채널 선택됨")

            st.subheader("🚀 선택 항목 작업")
            work1, work2 = st.columns(2)
            with work1:
                new_cat_name = st.text_input("변경할 새 카테고리명")
                if st.button("🏷️ 카테고리 일괄 변경"):
                    if st.session_state.selected_ids and new_cat_name:
                        for i in st.session_state.selected_ids:
                            supabase.table('channels').update({"category": new_cat_name}).eq("id", i).execute()
                        st.success("변경 완료!"); st.session_state.selected_ids = set(); st.rerun()
            with work2:
                st.write("---")
                if st.button("🗑️ 선택 항목 일괄 삭제", type="secondary"):
                    if st.session_state.selected_ids:
                        for i in st.session_state.selected_ids:
                            supabase.table('channels').delete().eq("id", i).execute()
                        st.success("삭제 완료!"); st.session_state.selected_ids = set(); st.rerun()
        else: st.info("등록된 데이터가 없습니다.")

    # 🔍 콘텐츠 분석 검색 탭
    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집해주세요.")
        else:
            df_scan = pd.DataFrame(res.data)
            st.subheader("⚙️ 정밀 분석 필터")
            with st.form("filter_form"):
                f1, f2, f3 = st.columns([2, 1, 1])
                scan_cats = f1.multiselect("분석 그룹", options=sorted(df_scan['category'].unique()), default=sorted(df_scan['category'].unique()))
                v_format = f2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주": 168, "2주": 336, "3주": 504, "한달": 720, "전체": 999999}
                t_label = f3.selectbox("업로드 기간", list(time_opts.keys()), index=4)
                
                f4, f5, f6 = st.columns(3)
                min_v = f4.number_input("최소 조회수", value=5000)
                min_s = f5.number_input("최소 구독자", value=0)
                max_s = f6.number_input("최대 구독자 (0=무제한)", value=30000)
                
                run_btn = st.form_submit_button("🚀 검색 및 50개 단위 분석 시작", type="primary")

            c_btn1, c_btn2 = st.columns(2)
            if c_btn1.button("🛑 분석 중단"): st.session_state.stop_analysis = True
            if c_btn2.button("🧹 결과 전체 초기화"):
                st.session_state.analysis_results = []
                st.session_state.current_batch_index = 0
                st.rerun()

            if run_btn:
                st.session_state.stop_analysis = False
                youtube = get_youtube_client()
                if not youtube: st.warning("API 키를 입력하세요."); return

                full_scan_list = df_scan[df_scan['category'].isin(scan_cats)].to_dict('records')
                start_idx = st.session_state.current_batch_index
                end_idx = min(start_idx + 50, len(full_scan_list))
                current_batch = full_scan_list[start_idx:end_idx]

                if not current_batch: st.success("모든 리스트 분석 완료!"); return

                batch_results = []
                bar = st.progress(0)
                status = st.empty()
                limit_h = time_opts[t_label]

                for i, ch in enumerate(current_batch):
                    if st.session_state.stop_analysis: break
                    status.text(f"분석 중: {ch['channel_name']} ({start_idx + i + 1}/{len(full_scan_list)})")
                    
                    try:
                        # 1. 구독자 정보 및 None 처리 (대형 채널 방어)
                        ch_res = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                        items = ch_res.get('items', [])
                        if items:
                            stats = items[0].get('statistics', {})
                            subs_str = stats.get('subscriberCount')
                            # ⚠️ None(대형 채널)일 경우 max_s 조건에 걸리도록 큰 숫자 부여
                            subs = int(subs_str) if subs_str is not None else 999999999
                        else: subs = 0

                        if (min_s > 0 and subs < min_s) or (max_s > 0 and subs > max_s):
                            bar.progress((i + 1) / len(current_batch)); continue

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

                st.session_state.analysis_results.extend(batch_results)
                st.session_state.current_batch_index = end_idx
                st.rerun()

            if st.session_state.analysis_results:
                st.subheader(f"📊 누적 분석 결과 ({len(st.session_state.analysis_results)}건)")
                df_res = pd.DataFrame(st.session_state.analysis_results).drop_duplicates(subset=['링크'])
                df_res = df_res.sort_values("VPH", ascending=False)
                st.data_editor(df_res, column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)

# --- 6. 실행 제어 ---
if st.session_state.user is None: login_page()
else: main_app()
