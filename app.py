import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate
import time

# --- 1. 초기 설정 및 Supabase 연결 ---
st.set_page_config(page_title="YouTube Target Analyzer", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. 다중 API 키 관리 및 자동 스위칭 로직 ---
if 'api_key_index' not in st.session_state:
    st.session_state.api_key_index = 0

def get_youtube_client():
    keys = st.session_state.get("user_api_keys", [])
    if not keys: return None
    
    # 할당량 초과 시 다음 키로 넘어가기 위한 시도
    current_key = keys[st.session_state.api_key_index % len(keys)]
    return build('youtube', 'v3', developerKey=current_key, cache_discovery=False)

def handle_api_error(e):
    if "quotaExceeded" in str(e):
        st.session_state.api_key_index += 1
        st.toast("🔄 API 할당량 초과! 다음 키로 자동 전환합니다.")
        return True
    return False

# --- 3. 핵심 분석 로직 함수들 ---
def get_channel_id_from_handle(youtube, handle):
    handle = handle.strip()
    if not handle.startswith('@'): handle = '@' + handle
    res = youtube.search().list(q=handle, type='channel', part='id', maxResults=1).execute()
    return res['items'][0]['id']['channelId'] if res.get('items') else None

def get_subscriptions(youtube, channel_id):
    """채널이 구독 중인 목록을 가져옵니다. (공개된 경우만 가능)"""
    subs = []
    try:
        request = youtube.subscriptions().list(channelId=channel_id, part='snippet', maxResults=50)
        while request:
            res = request.execute()
            for item in res.get('items', []):
                subs.append({
                    "name": item['snippet']['title'],
                    "id": item['snippet']['resourceId']['channelId'],
                    "url": f"https://www.youtube.com/channel/{item['snippet']['resourceId']['channelId']}"
                })
            request = youtube.subscriptions().list_next(request, res)
            if len(subs) >= 200: break # 너무 많으면 일단 끊음
    except Exception as e:
        st.error(f"구독 목록을 가져올 수 없습니다 (비공개 채널일 확률 높음): {e}")
    return subs

# --- 4. 세션 및 유저 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []

def login_page():
    st.title("🔐 YouTube Analyzer")
    t1, t2 = st.tabs(["로그인", "회원가입"])
    with t1:
        e = st.text_input("이메일")
        p = st.text_input("비밀번호", type="password")
        if st.button("로그인"):
            res = supabase.auth.sign_in_with_password({"email": e, "password": p})
            st.session_state.user = res.user
            st.rerun()
    with t2:
        ne = st.text_input("가입 이메일")
        np = st.text_input("가입 비번")
        npc = st.text_input("비번 확인", type="password")
        if st.button("가입"):
            if np == npc: 
                supabase.auth.sign_up({"email": ne, "password": np})
                st.success("가입 완료!")

def main_app():
    # 사이드바: 다중 API 키 설정
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        raw_keys = st.text_area("API Keys (한 줄에 하나씩)", 
                               value="\n".join(st.session_state.user_api_keys),
                               placeholder="AIza...1\nAIza...2").split('\n')
        if st.button("키 저장/적용"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("API 키 리스트 적용됨")
        
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    st.title("🎯 유튜브 구독 채널 벤치마킹 도구")

    tab_search, tab_db = st.tabs(["🔍 구독 채널 스캔 및 분석", "🗂️ 저장된 채널 DB 관리"])

    with tab_search:
        # 필터링 섹션
        with st.expander("🛠️ 검색 필터링 설정 (검색 전 설정)", expanded=True):
            c1, c2, c3 = st.columns(3)
            min_sub = c1.number_input("최소 구독자수", value=0)
            max_sub = c1.number_input("최대 구독자수 (0은 무제한)", value=0)
            min_view = c2.number_input("최소 조회수", value=0)
            
            time_map = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주일": 168, "한달": 720}
            time_label = c3.selectbox("업로드 시간", list(time_map.keys()), index=4)
            limit_hours = time_map[time_label]

        target_handle = st.text_input("분석할 기준 채널 핸들 (예: @shukaworld)", placeholder="@를 포함해 입력")
        
        if st.button("🚀 분석 및 DB 저장 시작", type="primary"):
            youtube = get_youtube_client()
            if not youtube: 
                st.warning("API 키를 먼저 입력해주세요."); return

            # 1. 핸들로 채널 ID 찾기
            main_id = get_channel_id_from_handle(youtube, target_handle)
            if not main_id: st.error("채널을 찾을 수 없습니다."); return

            # 2. 구독 목록 가져오기
            st.info("구독 채널 목록을 수집 중...")
            subs = get_subscriptions(youtube, main_id)
            
            if subs:
                # 3. DB 저장 (중복 제외)
                for sub in subs:
                    supabase.table('channels').upsert({
                        "user_id": st.session_state.user.id,
                        "channel_id": sub['id'],
                        "channel_name": sub['name'],
                        "channel_url": sub['url'],
                        "category": "미지정"
                    }, on_conflict="channel_id").execute()
                
                st.success(f"{len(subs)}개 채널을 DB에 저장/업데이트 했습니다.")
                
                # 4. 각 채널의 영상 분석 시작
                final_results = []
                progress_bar = st.progress(0)
                
                for i, sub in enumerate(subs):
                    try:
                        # 채널 정보(구독자수) 가져오기
                        ch_info = youtube.channels().list(id=sub['id'], part='statistics').execute()
                        sub_count = int(ch_info['items'][0]['statistics'].get('subscriberCount', 0))
                        
                        # 구독자수 필터
                        if min_sub > 0 and sub_count < min_sub: continue
                        if max_sub > 0 and sub_count > max_sub: continue

                        # 최신 영상 50개 가져오기
                        v_res = youtube.search().list(channelId=sub['id'], part='snippet', maxResults=50, order='date', type='video').execute()
                        v_ids = [item['id']['videoId'] for item in v_res.get('items', [])]
                        
                        if v_ids:
                            d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet').execute()
                            for item in d_res.get('items', []):
                                pub_at = item['snippet']['publishedAt']
                                pub_date = datetime.fromisoformat(pub_at.replace('Z', '+00:00'))
                                diff_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
                                
                                if diff_hours > limit_hours: continue
                                views = int(item['statistics'].get('viewCount', 0))
                                if views < min_view: continue
                                
                                vph = round(views / max(diff_hours, 0.1), 1)
                                
                                final_results.append({
                                    "썸네일": item['snippet']['thumbnails']['default']['url'],
                                    "채널명": item['snippet']['channelTitle'],
                                    "구독자수": sub_count,
                                    "제목": item['snippet']['title'],
                                    "조회수": views,
                                    "VPH": vph,
                                    "링크": f"https://youtu.be/{item['id']}"
                                })
                    except Exception as e:
                        if not handle_api_error(e): st.write(f"Error at {sub['name']}: {e}")
                    
                    progress_bar.progress((i + 1) / len(subs))

                if final_results:
                    df = pd.DataFrame(final_results)
                    st.subheader("📊 분석 결과 (표 제목을 클릭해 정렬하세요)")
                    st.data_editor(
                        df,
                        column_config={
                            "썸네일": st.column_config.ImageColumn("썸네일"),
                            "링크": st.column_config.LinkColumn("링크")
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.warning("필터 조건에 맞는 영상이 없습니다.")

    with tab_db:
        st.subheader("📝 내 구독 채널 리스트 관리")
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            # 편집 기능 추가
            edited_df = st.data_editor(
                df_db[['id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True,
                num_rows="dynamic",
                key="db_editor"
            )
            if st.button("💾 DB 변경사항 저장"):
                for _, row in edited_df.iterrows():
                    # 수정 로직 (ID 기준 업데이트)
                    supabase.table('channels').update({"category": row['category']}).eq("id", row['id']).execute()
                st.success("DB가 업데이트되었습니다.")
        else:
            st.info("저장된 채널이 없습니다.")

# --- 실행 ---
if st.session_state.user is None: login_page()
else: main_app()
