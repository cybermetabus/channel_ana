import re

def get_channel_id_by_handle(youtube, input_text):
    """
    핸들(@), 채널ID(UC...), URL 주소 등 모든 입력에서 채널 ID를 추출합니다.
    """
    if not input_text: return None
    input_text = input_text.strip()

    # 1. 입력값에서 채널 ID(UC로 시작하는 24자)가 포함되어 있는지 확인
    id_match = re.search(r'(UC[\w-]{22})', input_text)
    if id_match:
        ch_id = id_match.group(1)
        try:
            # ID로 직접 조회 (가장 확실함)
            res = youtube.channels().list(id=ch_id, part='id').execute()
            if res.get('items'): return res['items'][0]['id']
        except: pass

    # 2. 핸들(@...) 추출 및 조회
    handle_match = re.search(r'(@[\w.-]+)', input_text)
    target_handle = None
    if handle_match:
        target_handle = handle_match.group(1)
    elif not input_text.startswith('http'):
        # URL이 아닌 그냥 텍스트인 경우 @를 붙여줌
        target_handle = input_text if input_text.startswith('@') else '@' + input_text

    if target_handle:
        try:
            # 핸들로 조회
            res = youtube.channels().list(forHandle=target_handle, part='id').execute()
            if res.get('items'): return res['items'][0]['id']
        except: pass

    # 3. 최후의 보루: 검색 API 사용 (할당량이 높지만 가장 강력함)
    try:
        search_res = youtube.search().list(
            q=input_text, 
            type='channel', 
            part='id', 
            maxResults=1
        ).execute()
        if search_res.get('items'):
            return search_res['items'][0]['id']['channelId']
    except Exception as e:
        # API 키 할당량 초과 등의 문제 시 에러 노출
        if "quotaExceeded" in str(e):
            switch_api_key()
    
    return None
