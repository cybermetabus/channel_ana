# (상단 import 및 초기화 로직은 동일)

# --- [관리 탭] 내 '채널 및 그룹 관리' 로직 수정 ---
with tab_manage:
    st.subheader("🛠️ 일괄 관리 및 그룹 수정")
    
    # 1. DB에서 현재 저장된 모든 데이터 불러오기
    res = supabase.table('channels').select("*").execute()
    if res.data:
        df_db = pd.DataFrame(res.data)
        all_cats = sorted(df_db['category'].unique())
        
        # --- 일괄 처리 섹션 ---
        with st.expander("🚀 일괄 수정/삭제 도구 (여기서 전체 선택 및 처리가 가능합니다)", expanded=False):
            c1, c2 = st.columns(2)
            
            # 대상 선택
            target_method = c1.radio("대상 선택 방식", ["카테고리별 선택", "전체 채널 선택"])
            
            if target_method == "카테고리별 선택":
                bulk_target_cats = c1.multiselect("작업할 카테고리를 선택하세요", options=all_cats)
                target_df = df_db[df_db['category'].isin(bulk_target_cats)]
            else:
                bulk_target_cats = []
                target_df = df_db
                st.warning("⚠️ 모든 데이터가 작업 대상입니다.")

            st.write(f"**선택된 대상 수:** {len(target_df)}개 채널")
            
            st.divider()
            
            # 액션 1: 카테고리 이름 일괄 변경
            new_cat_name = c2.text_input("변경할 새 카테고리명 입력")
            if c2.button("🏷️ 카테고리 일괄 변경 실행"):
                if target_method == "카테고리별 선택" and bulk_target_cats:
                    with st.spinner("이동 중..."):
                        for cat in bulk_target_cats:
                            supabase.table('channels').update({"category": new_cat_name}).eq("category", cat).eq("user_id", st.session_state.user.id).execute()
                    st.success(f"선택한 그룹이 '{new_cat_name}'으로 통합되었습니다.")
                    st.rerun()
                elif target_method == "전체 채널 선택":
                    supabase.table('channels').update({"category": new_cat_name}).eq("user_id", st.session_state.user.id).execute()
                    st.success(f"모든 채널의 카테고리가 '{new_cat_name}'으로 변경되었습니다.")
                    st.rerun()

            # 액션 2: 일괄 삭제
            if st.button("🗑️ 선택된 대상 전체 삭제", type="secondary"):
                if target_method == "카테고리별 선택" and bulk_target_cats:
                    confirm = st.warning(f"정말로 {bulk_target_cats} 내의 {len(target_df)}개 채널을 삭제하시겠습니까?")
                    if st.button("예, 삭제를 확정합니다"):
                        for cat in bulk_target_cats:
                            supabase.table('channels').delete().eq("category", cat).eq("user_id", st.session_state.user.id).execute()
                        st.success("일괄 삭제 완료!")
                        st.rerun()
                elif target_method == "전체 채널 선택":
                    if st.button("🔥 DB 전체 초기화 확정"):
                        supabase.table('channels').delete().eq("user_id", st.session_state.user.id).execute()
                        st.success("모든 데이터가 삭제되었습니다.")
                        st.rerun()

        st.divider()
        
        # 2. 개별 수정용 데이터 에디터 (기존 기능 유지)
        st.subheader("📝 개별 상세 수정")
        edited_df = st.data_editor(
            df_db[['id', 'channel_name', 'category', 'channel_url']],
            use_container_width=True,
            num_rows="dynamic",
            key="db_editor_v2"
        )
        
        if st.button("💾 개별 수정사항 저장"):
            for _, row in edited_df.iterrows():
                supabase.table('channels').update({
                    "channel_name": row['channel_name'],
                    "category": row['category']
                }).eq("id", row['id']).execute()
            st.success("저장되었습니다.")
            st.rerun()
    else:
        st.info("저장된 채널이 없습니다.")
