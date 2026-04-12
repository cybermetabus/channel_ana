import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate

# --- 1. 초기 설정 및 Supabase 연결 ---
st.set_page_config(page_title="YouTube Growth Manager", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

# --- 2. API 및 데이터 로직 ---
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
        st.toast("🔄 API 할당량 초과! 다음 키로 전환합니다.")
        return True
    return False

def get_channel_id(youtube, handle):
    handle = handle.strip()
    if not handle.startswith('@'): handle = '@' + handle
    res = youtube.search().list(q=handle, type='channel', part='id', maxResults=1).execute()
    return res['items'][0]['id']['channelId'] if res.get('items') else None

# --- 3. 세션 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []

# --- 4. 로그인 화면 ---
def login_page():
    st.title("🔐 YouTube Analyzer")
    t1, t2 = st.tabs(["로그인", "회원가입"])
    with t1:
        e = st.text_input("이메일")
        p = st.text_input("비밀번호", type="password")
        if st.button("로그인"):
            try:
                res = supabase.auth.sign_in_with_password({"email": e, "password": p})
                st.session_state.user = res.user
                st.rerun()
            except: st.error("로그인 실패")
    with t2:
        ne = st.text_input("가입 이메일")
        np = st.text_input("비밀번호", type="password")
        if st.button("회원가입"):
            try:
                supabase.auth.sign_up({"email": ne, "password": np})
                st.success("인증 메일을 확인하거나 바로 로그인해보세요.")
            except Exception as ex: st.error(f"실패: {ex}")

# --- 5. 메인 앱 ---
def main_app():
    # ---------------------------------------------------------
    # 1단계: 사이드바 (API 키 관리 + 구독 채널 수집 도구)
    # ---------------------------------------------------------
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        
        # API 키 관리
        raw_keys = st.text_area("🔑 API Keys (한 줄에 하나씩)", 
                               value="\n".join(st.session_state.user_api_keys),
                               height=100).split('\n')
        if st.button("키 리스트 적용"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("적용됨")
        
        st.divider()
        
        # 구독 채널 임포터 (사이드바 하단 이동)
        st.subheader("📥 채널 수집 도구")
        import_handle = st.text_input("기준 채널 핸들 (예: @shukaworld)", key="importer")
        default_cat = st.selectbox("기본 카테고리 지정", ["경제", "예능", "테크", "정보", "기타"], index=3)
        
        if st.button("구독 목록 불러와서 DB 저장"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키를 먼저 입력하세요.")
            else:
                with st.spinner("목록 수집 중..."):
                    ch_id = get_channel_id(youtube, import_handle)
                    if ch_id:
                        res = youtube.subscriptions().list(channelId=ch_id, part='snippet', maxResults=50).execute()
                        count = 0
                        for item in res.get('items', []):
                            sub_id = item['snippet']['resourceId']['channelId']
                            supabase.table('channels').upsert({
                                "user_id": st.session_state.user.id,
                                "channel_id": sub_id,
                                "channel_name": item['snippet']['title'],
                                "channel_url": f"https://youtube.com/channel/{sub_id}",
                                "category": default_cat
                            }, on_conflict="channel_id").execute()
                            count += 1
                        st.success(f"{count}개 채널 저장 완료!")
                    else: st.error("채널을 찾을 수 없습니다.")

        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # ---------------------------------------------------------
    # 2단계: 메인 화면 (분석 탭 / DB 관리 탭)
    # ---------------------------------------------------------
    st.title("🎯 유튜브 알고리즘 벤치마킹 시스템")
    
    tab_search, tab_db = st.tabs(["🔍 영상 분석 검색", "🗂️ 채널 DB 관리 및 수정"])

    # 🗂️ 채널 DB 관리 탭
    with tab_db:
        st.subheader("📝 내 채널 리스트 관리")
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.info("표 안에서 카테고리를 직접 수정하거나 행을 선택해 삭제할 수 있습니다.")
            
            # 카테고리 등 수정 가능한 데이터 에디터
            edited_df = st.data_editor(
                df_db[['id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True,
                num_rows="dynamic", # 행 삭제 가능
                column_config={
                    "category": st.column_config.SelectboxColumn(
                        "카테고리", options=["경제", "예능", "테크", "정보", "기타", "제외"], required=True
                    )
                },
                key="db_editor"
            )
            
            if st.button("💾 변경사항을 데이터베이스에 일괄 저장"):
                with st.spinner("저장 중..."):
                    # 실제 수정/삭제 로직 (여기서는 단순 업데이트 예시)
                    for _, row in edited_df.iterrows():
                        supabase.table('channels').update({"category": row['category']}).eq("id", row['id']).execute()
                    st.success("DB가 업데이트되었습니다!")
                    st.rerun()
        else:
            st.info("아직 수집된 채널이 없습니다. 사이드바에서 채널을 불러오세요.")

    # 🔍 영상 분석 검색 탭
    with tab_search:
        res = supabase.table('channels').select("*").execute()
        if not res.data:
            st.warning("먼저 채널 DB 관리 탭에서 채널을 등록해주세요.")
        else:
            df_all = pd.DataFrame(res.data)
            all_categories = sorted(df_all['category'].unique())

            with st.form("search_filter"):
                st.subheader("⚙️ 검색 조건 설정")
                c1, c2, c3 = st.columns([2, 1, 1])
                
                # 카테고리 다중 선택 (하나만, 여러개, 혹은 전체 선택 가능)
                selected_cats = c1.multiselect("분석할 카테고리 선택", options=all_categories, default=all_categories)
                
                time_map = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주일": 168, "한달": 720}
                limit_label = c2.selectbox("업로드 시간 범위", list(time_map.keys()), index=4)
                
                min_view = c3.number_input("최소 조회수", value=10000, step=5000)
                
                c4, c5 = st.columns(2)
                min_sub = c4.number_input("최소 구독자수", value=0)
                max_sub = c5.number_input("최대 구독자수 (0은 무제한)", value=0)
                
                search_clicked = st.form_submit_button("🚀 설정된 리스트 분석 시작", type="primary")

            if search_clicked:
                youtube = get_youtube_client()
                if not youtube: st.warning("API 키를 입력하세요.")
                else:
                    # 선택된 카테고리에 해당하는 채널만 필터링
                    filtered_channels = df_all[df_all['category'].isin(selected_cats)]
                    st.info(f"[{', '.join(selected_cats)}] 카테고리의 {len(filtered_channels)}개 채널을 스캔합니다.")
                    
                    final_results = []
                    progress = st.progress(0)
                    limit_hours = time_map[limit_label]

                    for i, ch in enumerate(filtered_channels.to_dict('records')):
                        try:
                            # 1. 채널 스탯 확인 (구독자수 필터)
                            ch_res = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                            sub_count = int(ch_res['items'][0]['statistics'].get('subscriberCount', 0))
                            
                            if min_sub > 0 and sub_count < min_sub: continue
                            if max_sub > 0 and sub_count > max_sub: continue

                            # 2. 최신 영상 50개 스캔
                            v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                            v_ids = [item['id']['videoId'] for item in v_res.get('items', [])]
                            
                            if v_ids:
                                d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet').execute()
                                for item in d_res.get('items', []):
                                    pub_at = item['snippet']['publishedAt']
                                    pub_date = datetime.fromisoformat(pub_at.replace('Z', '+00:00'))
                                    diff_h = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
                                    
                                    if diff_h > limit_hours: continue
                                    views = int(item['statistics'].get('viewCount', 0))
                                    if views < min_view: continue
                                    
                                    final_results.append({
                                        "썸네일": item['snippet']['thumbnails']['default']['url'],
                                        "채널명": item['snippet']['channelTitle'],
                                        "구독자수": sub_count,
                                        "제목": item['snippet']['title'],
                                        "조회수": views,
                                        "VPH": round(views / max(diff_h, 0.1), 1),
                                        "링크": f"https://youtu.be/{item['id']}"
                                    })
                        except Exception as e:
                            if not handle_api_error(e): st.write(f"오류: {ch['channel_name']} - {e}")
                        
                        progress.progress((i + 1) / len(filtered_channels))

                    if final_results:
                        st.subheader("📊 분석 결과 (표 상단을 눌러 정렬)")
                        st.data_editor(
                            pd.DataFrame(final_results).sort_values("VPH", ascending=False),
                            column_config={
                                "썸네일": st.column_config.ImageColumn("썸네일"),
                                "링크": st.column_config.LinkColumn("링크")
                            },
                            use_container_width=True, hide_index=True
                        )
                    else: st.warning("조건에 맞는 영상이 없습니다.")

# --- 실행 ---
if st.session_state.user is None: login_page()
else: main_app()
