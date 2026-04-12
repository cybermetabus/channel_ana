import streamlit as st
from supabase import create_client, Client
from googleapiclient.discovery import build
import pandas as pd
from datetime import datetime, timezone, timedelta
import isodate

# --- 1. 초기 설정 및 Supabase 연결 ---
st.set_page_config(page_title="YouTube Content Strategy Tool", layout="wide")

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

def get_channel_id_robust(youtube, handle):
    """핸들을 통해 정확한 채널 ID를 찾습니다 (Search API 차단 우회 버전)."""
    handle = handle.strip()
    # 핸들이 @로 시작하지 않으면 붙여줍니다.
    if not handle.startswith('@'):
        clean_handle = '@' + handle
    else:
        clean_handle = handle
        
    try:
        # search().list() 대신 channels().list()의 forHandle 매개변수를 사용합니다.
        # 이 방법은 할당량이 1점밖에 안 들고(search는 100점), 차단될 확률이 거의 없습니다.
        res = youtube.channels().list(
            forHandle=clean_handle, 
            part='id,snippet'
        ).execute()
        
        if res.get('items'):
            return res['items'][0]['id']
        else:
            # 혹시 핸들로 못 찾을 경우를 대비한 일반 검색 (차단된 경우 에러 발생 가능)
            st.warning(f"'{clean_handle}' 핸들로 직접 찾지 못했습니다. 일반 검색을 시도합니다.")
            res_search = youtube.search().list(q=clean_handle, type='channel', part='id', maxResults=1).execute()
            if res_search.get('items'):
                return res_search['items'][0]['id']['channelId']
            return None
    except Exception as e:
        if "blocked" in str(e):
            st.error("💡 Google에서 Search API 사용을 제한했습니다. 하지만 '채널 핸들' 기능으로 시도 중입니다.")
        else:
            st.error(f"채널 검색 중 오류 발생: {e}")
        return None

# --- 3. 세션 관리 ---
if 'user' not in st.session_state: st.session_state.user = None
if 'user_api_keys' not in st.session_state: st.session_state.user_api_keys = []

# --- 4. 로그인 화면 ---
def login_page():
    st.title("🔐 YouTube Strategy Tool")
    t1, t2 = st.tabs(["로그인", "회원가입"])
    with t1:
        e = st.text_input("이메일", key="l_email")
        p = st.text_input("비밀번호", type="password", key="l_pw")
        if st.button("로그인", key="l_btn"):
            try:
                res = supabase.auth.sign_in_with_password({"email": e, "password": p})
                st.session_state.user = res.user
                st.rerun()
            except: st.error("로그인 실패")
    with t2:
        ne = st.text_input("이메일", key="s_email")
        np = st.text_input("비밀번호", type="password", key="s_pw")
        if st.button("가입하기", key="s_btn"):
            supabase.auth.sign_up({"email": ne, "password": np})
            st.success("가입 완료!")

# --- 5. 메인 앱 ---
def main_app():
    # ---------------------------------------------------------
    # 사이드바: API 관리 + 구독 리스트 수집 (여기가 '데이터 확보' 섹션)
    # ---------------------------------------------------------
    with st.sidebar:
        st.subheader("👤 " + st.session_state.user.email)
        
        # 1. API 키 리스트
        raw_keys = st.text_area("🔑 API Keys (줄바꿈으로 구분)", 
                               value="\n".join(st.session_state.user_api_keys),
                               height=100, key="api_keys_input").split('\n')
        if st.button("API 키 적용", key="save_keys"):
            st.session_state.user_api_keys = [k.strip() for k in raw_keys if k.strip()]
            st.success("키 리스트가 업데이트되었습니다.")
        
        st.divider()
        
        # 2. 구독 채널 수집기 (카테고리 자유 입력)
        st.subheader("📥 채널 수집 및 그룹화")
        import_handle = st.text_input("기준 채널 핸들 (예: @비디오26)", key="h_input")
        # 이제 선택 박스가 아니라 직접 입력하는 텍스트 인풋입니다.
        initial_cat = st.text_input("그룹명(카테고리) 지정", value="테크분석", key="cat_input")
        
        if st.button("구독 목록 가져와 DB 저장", key="fetch_subs"):
            youtube = get_youtube_client()
            if not youtube: st.warning("API 키를 입력하세요.")
            else:
                with st.spinner("채널 찾는 중..."):
                    ch_id = get_channel_id_robust(youtube, import_handle)
                    if ch_id:
                        try:
                            res = youtube.subscriptions().list(channelId=ch_id, part='snippet', maxResults=50).execute()
                            count = 0
                            for item in res.get('items', []):
                                sub_id = item['snippet']['resourceId']['channelId']
                                supabase.table('channels').upsert({
                                    "user_id": st.session_state.user.id,
                                    "channel_id": sub_id,
                                    "channel_name": item['snippet']['title'],
                                    "channel_url": f"https://youtube.com/channel/{sub_id}",
                                    "category": initial_cat # 사용자가 입력한 자유 카테고리
                                }, on_conflict="channel_id").execute()
                                count += 1
                            st.success(f"'{initial_cat}' 그룹에 {count}개 채널 저장!")
                        except Exception as e: st.error(f"구독 정보를 가져올 수 없습니다: {e}")
                    else: st.error("채널 핸들을 다시 확인해주세요.")

        st.divider()
        if st.button("로그아웃"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

    # ---------------------------------------------------------
    # 메인 화면: 분석(콘텐츠 검색) / DB 관리(커스터마이징)
    # ---------------------------------------------------------
    st.title("🎯 유튜브 알고리즘 타겟 콘텐츠 분석기")
    
    tab_search, tab_db = st.tabs(["🔍 콘텐츠 분석 검색", "⚙️ 채널 리스트 및 그룹 관리"])

    # [채널 DB 관리 탭] - 여기서 카테고리를 자유롭게 수정
    with tab_db:
        st.subheader("📝 내 채널 리스트 커스터마이징")
        res = supabase.table('channels').select("*").execute()
        if res.data:
            df_db = pd.DataFrame(res.data)
            st.write("아래 표에서 채널명이나 카테고리를 자유롭게 수정하세요. (수정 후 반드시 아래 저장 버튼 클릭)")
            
            # 카테고리 컬럼을 자유 텍스트로 수정할 수 있게 설정
            edited_df = st.data_editor(
                df_db[['id', 'channel_name', 'category', 'channel_url']],
                use_container_width=True,
                num_rows="dynamic",
                key="db_editor"
            )
            
            if st.button("💾 모든 변경사항 DB에 일괄 저장"):
                for _, row in edited_df.iterrows():
                    supabase.table('channels').update({
                        "channel_name": row['channel_name'],
                        "category": row['category']
                    }).eq("id", row['id']).execute()
                st.success("성공적으로 저장되었습니다!")
                st.rerun()
        else:
            st.info("사이드바에서 먼저 채널을 수집해주세요.")

    # [콘텐츠 분석 검색 탭] - "채널"이 아닌 "콘텐츠"를 보여주는 곳
    with tab_search:
        res = supabase.table('channels').select("*").execute()
        if not res.data:
            st.warning("먼저 채널을 수집하여 DB를 구축해주세요.")
        else:
            df_all = pd.DataFrame(res.data)
            user_categories = sorted(df_all['category'].unique())

            with st.form("analysis_form"):
                st.subheader("📊 조건별 콘텐츠 스캔")
                c1, c2, c3 = st.columns([2, 1, 1])
                # 내가 만든 카테고리 중 분석하고 싶은 그룹만 선택
                target_cats = c1.multiselect("분석할 그룹(카테고리) 선택", options=user_categories, default=user_categories)
                
                time_map = {"12시간": 12, "24시간": 24, "48시간": 48, "3일": 72, "1주일": 168, "한달": 720}
                limit_label = c2.selectbox("업로드 기간", list(time_map.keys()), index=4)
                min_view = c3.number_input("최소 조회수", value=5000)

                c4, c5 = st.columns(2)
                min_sub = c4.number_input("최소 구독자수 (0은 제한없음)", value=0)
                max_sub = c5.number_input("최대 구독자수 (0은 제한없음)", value=0)

                submitted = st.form_submit_button("🚀 영상 분석 시작", type="primary")

            if submitted:
                youtube = get_youtube_client()
                if not youtube: st.warning("API 키가 없습니다."); return

                # 선택한 '그룹(카테고리)'에 속한 채널들만 추출
                target_channels = df_all[df_all['category'].isin(target_cats)]
                st.info(f"{target_cats} 그룹 내 {len(target_channels)}개 채널의 콘텐츠를 스캔합니다.")
                
                all_found_videos = []
                progress = st.progress(0)
                limit_h = time_map[limit_label]

                for i, ch in enumerate(target_channels.to_dict('records')):
                    try:
                        # 1. 채널 정보(구독자) 필터링
                        ch_stat = youtube.channels().list(id=ch['channel_id'], part='statistics').execute()
                        subs = int(ch_stat['items'][0]['statistics'].get('subscriberCount', 0))
                        
                        if (min_sub > 0 and subs < min_sub) or (max_sub > 0 and subs > max_sub): continue

                        # 2. 채널 내 최신 영상 50개 리스트업
                        v_res = youtube.search().list(channelId=ch['channel_id'], part='snippet', maxResults=50, order='date', type='video').execute()
                        v_ids = [v['id']['videoId'] for v in v_res.get('items', []) if 'videoId' in v['id']]
                        
                        if v_ids:
                            # 3. 영상별 상세 데이터(조회수) 가져와서 필터링
                            d_res = youtube.videos().list(id=','.join(v_ids), part='statistics,snippet').execute()
                            for item in d_res.get('items', []):
                                pub_at = item['snippet']['publishedAt']
                                pub_date = datetime.fromisoformat(pub_at.replace('Z', '+00:00'))
                                age_h = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
                                
                                if age_h > limit_h: continue
                                views = int(item['statistics'].get('viewCount', 0))
                                if views < min_view: continue
                                
                                # 모든 조건을 통과한 '콘텐츠' 추가
                                all_found_videos.append({
                                    "썸네일": item['snippet']['thumbnails']['default']['url'],
                                    "채널": item['snippet']['channelTitle'],
                                    "구독자": subs,
                                    "영상 제목": item['snippet']['title'],
                                    "조회수": views,
                                    "VPH": round(views / max(age_h, 0.1), 1),
                                    "링크": f"https://youtu.be/{item['id']}"
                                })
                    except Exception as e:
                        if not handle_api_error(e): pass
                    progress.progress((i + 1) / len(target_channels))

                if all_found_videos:
                    st.subheader("🎬 조건에 부합하는 콘텐츠 리스트")
                    res_df = pd.DataFrame(all_found_videos).sort_values("VPH", ascending=False)
                    st.data_editor(
                        res_df,
                        column_config={
                            "썸네일": st.column_config.ImageColumn(),
                            "링크": st.column_config.LinkColumn()
                        },
                        use_container_width=True, hide_index=True
                    )
                else: st.warning("필터 조건에 맞는 영상이 검색되지 않았습니다.")

# --- 실행 ---
if st.session_state.user is None: login_page()
else: main_app()
