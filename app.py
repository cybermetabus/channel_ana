import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time
import re

# --- 1. 초기 설정 및 DB 연결 ---
st.set_page_config(page_title="YouTube Growth Pro", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 세션 상태 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'api_key_index' not in st.session_state: st.session_state.api_key_index = 0
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []
if 'analysis_results' not in st.session_state: st.session_state.analysis_results = []
if 'current_batch_index' not in st.session_state: st.session_state.current_batch_index = 0
if 'stop_analysis' not in st.session_state: st.session_state.stop_analysis = False
if 'selected_ids' not in st.session_state: st.session_state.selected_ids = set()

# --- 3. 유틸리티 함수 ---
def get_youtube_client():
    keys = st.session_state.user_api_keys
    if not keys: return None
    idx = st.session_state.api_key_index % len(keys)
    return build('youtube', 'v3', developerKey=keys[idx], cache_discovery=False)

def switch_api_key():
    st.session_state.api_key_index += 1
    if st.session_state.api_key_index >= len(st.session_state.user_api_keys):
        st.error("🚨 모든 API 키 소진! 내일 다시 시도하세요.")
        st.session_state.stop_analysis = True
        return None
    st.toast("🔄 다음 키로 전환합니다.")
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

# --- 4. 로그인 페이지 ---
def login_page():
    st.title("🚀 YouTube Analyzer")
    st.subheader("닉네임과 접속 암호를 입력하세요")
    nickname = st.text_input("사용자 닉네임", placeholder="예: user01")
    master_pw = st.text_input("접속 암호", type="password")
    if st.button("접속하기", type="primary", use_container_width=True):
        if master_pw == "1795":
            if nickname:
                class UserInfo:
                    def __init__(self, nickname):
                        self.id = nickname
                        self.email = f"{nickname}@tool.app"
                st.session_state.user = UserInfo(nickname)
                st.rerun()
            else: st.warning("닉네임을 입력해주세요.")
        else: st.error("암호가 틀렸습니다.")

# --- 5. 메인 앱 서비스 ---
def main_app():
    with st.sidebar:
        st.subheader(f"👤 {st.session_state.user.id} 님")
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
                with st.spinner("수집 중..."):
                    main_id = get_channel_id_strong(youtube, target_input)
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
                        st.success(f"{total}개 저장 완료")
                    else: st.error("채널 찾기 실패")

        if st.button("로그아웃"):
            st.session_state.user = None
            st.rerun()

    tab_scan, tab_manage = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ DB 관리"])

    with tab_manage:
        res = supabase.table('channels').select("*").eq("user_id", st.session_state.user.id).execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.data_editor(df_db[['channel_name', 'category', 'channel_url']], use_container_width=True)
            if st.button("🗑️ 내 리스트 전체 삭제"):
                supabase.table('channels').delete().eq("user_id", st.session_state.user.id).execute()
                st.rerun()
        else: st.info("데이터 없음")

    # 💡 이 부분이 사라졌던 필터 기능입니다!
    with tab_scan:
        if not res.data: st.warning("채널을 먼저 수집하세요.")
        else:
            df_scan = pd.DataFrame(res.data)
            st.markdown(f"### 📊 분석 현황: `{st.session_state.current_batch_index}` / `{len(df_scan)}` 개 완료")
            
            with st.form("scan_form"):
                f1, f2, f3 = st.columns([2, 1, 1])
                scan_cats = f1.multiselect("분석 그룹", options=sorted(df_scan['category'].unique()), default=sorted(df_scan['category'].unique()))
                v_format = f2.selectbox("포맷", ["전체", "롱폼만", "숏폼만"])
                time_opts = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주": 168, "전체": 99999}
                t_label = f3.selectbox("기간", list(time_opts.keys()), index=1)
                
                f4, f5, f6 = st.columns(3)
                min_v = f4.number_input("최소 조회수", value=5000)
                min_s = f5.number_input("최소 구독자", value=0)
                max_s = f6.number_input("최대 구독자 (0=무제한)", value=30000)
                
                run_btn = st.form_submit_button("🚀 분석 시작 (50개 단위)", type="primary")

            c_btns = st.columns(2)
            if c_btns[0].button("🛑 중단"): st.session_state.stop_analysis = True
            if c_btns[1].button("🧹 결과 초기화"):
                st.session_state.analysis_results = []; st.session_state.current_batch_index = 0; st.rerun()

            if run_btn:
                st.session_state.stop_analysis = False
                youtube = get_youtube_client()
                if not youtube: st.warning("API 키를 입력하세요."); return

                target_list = df_scan[df_scan['category'].isin(scan_cats)].to_dict('records')
                start_idx = st.session_state.current_batch_index
                batch = target_list[start_idx : start_idx + 50]

                if not batch: st.success("분석 완료!"); return

                status_box = st.empty()
                p_bar = st.progress(0)
                results = []
                limit_h = time_opts[t_label]

                for i, ch in enumerate(batch):
                    if st.session_state.stop_analysis: break
                    status_box.info(f"📡 분석 중: {ch['channel_name']} ({start_idx + i + 1} / {len(target_list)})")
                    
                    try:
                        c_info = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                        items = c_info.get('items', [])
                        subs = int(items[0]['statistics'].get('subscriberCount', 0)) if items else 0
                        
                        if (min_s > 0 and subs < min_s) or (max_s > 0 and subs > max_s):
                            p_bar.progress((i+1)/len(batch)); continue

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
                    p_bar.progress((i + 1) / len(batch))

                st.session_state.analysis_results.extend(results)
                st.session_state.current_batch_index += len(batch)
                st.rerun()

            if st.session_state.analysis_results:
                df_res = pd.DataFrame(st.session_state.analysis_results).drop_duplicates(subset=['링크']).sort_values("VPH", ascending=False)
                st.data_editor(df_res, column_config={"썸네일": st.column_config.ImageColumn(), "링크": st.column_config.LinkColumn()}, use_container_width=True, hide_index=True)

# --- 6. 실행 제어 ---
if st.session_state.user is None:
    login_page()
else:
    main_app()
