import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time
import re

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Growth Intelligence", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 관리 ---
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
    """할당량 초과 시 다음 키로 교체 및 알림"""
    st.session_state.api_key_index += 1
    if st.session_state.api_key_index >= len(st.session_state.user_api_keys):
        st.error("🚨 [할당량 소진] 등록된 모든 API 키의 할당량이 끝났습니다! 내일 다시 시도하거나 새 키를 추가하세요.")
        st.session_state.stop_analysis = True
        return None
    st.toast(f"🔄 키 교체 중... ({st.session_state.api_key_index + 1}번째 키)")
    return get_youtube_client()

def get_channel_id_strong(youtube, input_text):
    if not input_text: return None
    input_text = input_text.strip()
    id_match = re.search(r'(UC[\w-]{22})', input_text)
    if id_match: return id_match.group(1)
    handle_match = re.search(r'(@[\w.-]+)', input_text)
    target = handle_match.group(1) if handle_match else input_text
    if not target.startswith('@') and not target.startswith('http'): target = '@' + target
    try:
        res = youtube.channels().list(forHandle=target, part='id').execute()
        if res.get('items'): return res['items'][0]['id']
        s_res = youtube.search().list(q=target, type='channel', part='id', maxResults=1).execute()
        if s_res.get('items'): return s_res['items'][0]['id']['channelId']
    except Exception as e:
        if "quotaExceeded" in str(e): switch_api_key()
    return None

# --- 4. 화면 구성용 함수 ---
def login_page():
    st.title("🔐 YouTube Analyzer")
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
        ne = st.text_input("가입용 이메일", key="s_email")
        np = st.text_input("가입용 비밀번호", type="password", key="s_pw")
        if st.button("회원가입"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료!")

def main_app():
    # 사이드바
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        # 할당량 상태 표시
        if st.session_state.user_api_keys:
            cur = (st.session_state.api_key_index % len(st.session_state.user_api_keys)) + 1
            tot = len(st.session_state.user_api_keys)
            st.caption(f"📡 API 상태: {tot}개 중 {cur}번째 사용 중")
        
        raw_keys = st.text_area("🔑 API Keys", value="\n".join(st.session_state.user_api_keys)).split('\n')
        if st.button("키 저장"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.session_state.api_key_index = 0
            st.success("저장됨")
        
        st.divider()
        st.subheader("📥 채널 수집")
        target_input = st.text_input("기준 핸들 또는 URL")
        group_name = st.text_input("그룹명", value="미분류")
        if st.button("수집 시작"):
            youtube = get_youtube_client()
            if not youtube: st.warning("키 없음")
            else:
                with st.spinner("채널 조회 중..."):
                    main_id = get_channel_id_strong(youtube, target_input)
                    if main_id:
                        next_token = None
                        total = 0
                        while True:
                            res = youtube.subscriptions().list(channelId=main_id, part='snippet', maxResults=50, pageToken=next_token).execute()
                            for s in res.get('items', []):
                                s_id = s['snippet']['resourceId']['channelId']
                                supabase.table('channels').upsert({"user_id": st.session_state.user.id, "channel_id": s_id, "channel_name": s['snippet']['title'], "category": group_name, "channel_url": f"https://youtube.com/channel/{s_id}"}, on_conflict="channel_id").execute()
                                total += 1
                            next_token = res.get('nextPageToken')
                            if not next_token: break
                        st.success(f"{total}개 저장 완료")
                    else: st.error("채널 찾기 실패 (할당량 소진 가능성)")

        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # 메인 화면
    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리 및 리스트 수정"])

    with tab_manage:
        res = supabase.table('channels').select("*").eq("user_id", st.session_state.user.id).execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            all_cats = sorted(df_db['category'].unique().tolist())
            
            mc1, mc2 = st.columns([2, 3])
            if mc1.button("✅ 전체 선택"): 
                st.session_state.selected_ids = set(df_db['id'].tolist())
                st.rerun()
            if mc1.button("❌ 전체 해제"): 
                st.session_state.selected_ids = set()
                st.rerun()
            
            cat_sel = mc2.selectbox("그룹별 선택", ["직접 고르세요"] + all_cats)
            if mc2.button("🎯 그룹 전체 선택"):
                if cat_sel != "직접 고르세요":
                    st.session_state.selected_ids.update(set(df_db[df_db['category'] == cat_sel]['id'].tolist()))
                    st.rerun()

            df_db['선택'] = df_db['id'].apply(lambda x: x in st.session_state.selected_ids)
            # 💡 에러 수정 포인트: 변수 이름 통일 (final_df)
            final_df = st.data_editor(
                df_db[['선택', 'id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True, hide_index=True,
                column_config={"선택": st.column_config.CheckboxColumn("선택"), "id": None},
                key="manage_editor_vFINAL"
            )
            st.session_state.selected_ids = set(final_df[final_df['선택'] == True]['id'].tolist())
            
            st.subheader("🚀 일괄 처리")
            new_cat = st.text_input("새 그룹명")
            if st.button("🏷️ 카테고리 일괄 수정"):
                for i in st.session_state.selected_ids:
                    supabase.table('channels').update({"category": new_cat}).eq("id", i).execute()
                st.success("변경 완료"); st.session_state.selected_ids = set(); st.rerun()
            if st.button("🗑️ 선택 일괄 삭제"):
                for i in st.session_state.selected_ids:
                    supabase.table('channels').delete().eq("id", i).execute()
                st.success("삭제 완료"); st.session_state.selected_ids = set(); st.rerun()
        else: st.info("데이터 없음")

    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집하세요.")
        else:
            df_scan = pd.DataFrame(res.data)
            with st.form("scan_form"):
                f1, f2, f3 = st.columns([2, 1, 1])
                scan_cats = f1.multiselect("분석 그룹", options=sorted(df_scan['category'].unique()), default=sorted(df_scan['category'].unique()))
                v_format = f2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주": 168, "전체": 99999}
                t_label = f3.selectbox("기간", list(time_opts.keys()), index=1)
                min_v = st.number_input("최소 조회수", value=5000)
                max_s = st.number_input("최대 구독자 (0=무제한)", value=30000)
                run_btn = st.form_submit_button("🚀 분석 시작 (50개 단위)", type="primary")

            c_btn1, c_btn2 = st.columns(2)
            if c_btn1.button("🛑 중단"): st.session_state.stop_analysis = True
            if c_btn2.button("🧹 초기화"):
                st.session_state.analysis_results = []; st.session_state.current_batch_index = 0; st.rerun()

            if run_btn:
                st.session_state.stop_analysis = False
                youtube = get_youtube_client()
                full_list = df_scan[df_scan['category'].isin(scan_cats)].to_dict('records')
                start_idx = st.session_state.current_batch_index
                end_idx = min(start_idx + 50, len(full_list))
                batch = full_list[start_idx:end_idx]

                if not batch: st.success("모든 분석 완료!"); return

                bar = st.progress(0)
                results = []
                for i, ch in enumerate(batch):
                    if st.session_state.stop_analysis: break
                    try:
                        c_res = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                        items = c_res.get('items', [])
                        subs = int(items[0].get('statistics', {}).get('subscriberCount', 0)) if items else 0
                        if subs > max_s and max_s > 0: continue # 3만명 초과 필터링

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
                                results.append({"썸네일": item['snippet']['thumbnails']['default']['url'], "채널": item['snippet']['channelTitle'], "구독자": subs, "제목": item['snippet']['title'], "조회수": views, "VPH": round(views / max(age_h, 0.1), 1), "링크": f"https://youtu.be/{item['id']}"})
                    except Exception as e:
                        if "quotaExceeded" in str(e): youtube = switch_api_key()
                        if st.session_state.stop_analysis: break
                    bar.progress((i + 1) / len(batch))

                st.session_state.analysis_results.extend(results)
                st.session_state.current_batch_index = end_idx
                st.rerun()

            if st.session_state.analysis_results:
                df_res = pd.DataFrame(st.session_state.analysis_results).drop_duplicates(subset=['링크']).sort_values("VPH", ascending=False)
                st.data_editor(df_res, column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)

# --- 6. 실행 제어 ---
if st.session_state.user is None: login_page()
else: main_app()
