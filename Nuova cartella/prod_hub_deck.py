import streamlit as st
import pandas as pd
from sqlalchemy import text
from database import fetch_cloud_materials
from database import update_cloud_material
from database import upload_certificate_to_cloud 
from database import show_cert_modal 
from database import insert_cloud_material
from database import split_cloud_material
from database import log_material_consumption 
from database import promote_cloud_material  
from database import delete_cloud_material 
from database import demote_cloud_material 
from database import open_coil_management_dialog


def prod_hub_deck(conn):
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()
        
    family_name = st.session_state.active_family.title()
    st.title(f"{family_name} — Production Hub")
    st.write("---")
    
    ######################## FETCH DATA FIRST ########################################################################
    prod_df = fetch_cloud_materials(conn, family=st.session_state.active_family)
    
    if not prod_df.empty:
        prod_df['calc_weight_kg'] = prod_df['rd_remaining_weight_kg'].fillna(prod_df['coil_weight_kg'])
        prod_df['calc_length_mm'] = prod_df['rd_remaining_length_mm'].fillna(prod_df['coil_length_mm'])
        prod_df = prod_df[prod_df['calc_weight_kg'] > 0.001]

    if not prod_df.empty:
        temp_storage_df = prod_df.sort_values(by="id").reset_index(drop=True).copy()
        temp_storage_df['seq_id'] = temp_storage_df.index + 1
        
        # Filter out parent lots that have been split into children
        split_fathers = temp_storage_df[
            temp_storage_df['lotto_figlio'].notna() & 
            (temp_storage_df['lotto_figlio'].astype(str).str.strip() != '') &
            (temp_storage_df['lotto_figlio'].astype(str).str.strip() != 'None')
        ]['lotto_number'].dropna().astype(str).unique()
        
        temp_storage_df = temp_storage_df[~temp_storage_df['lotto_number'].astype(str).isin(split_fathers)]
        
        temp_storage_df['active_lot'] = temp_storage_df['lotto_number'].apply(
            lambda x: str(x).strip() if pd.notna(x) and str(x).strip() not in ['', 'None', 'nan'] else "Unknown Lot"
        )
        
        # Explicit, clean formatting for Warehouse cards with larger visual weight
        temp_storage_df['lot_detail'] = temp_storage_df.apply(
            lambda row: f"<b>ID #{row['seq_id']}</b> : {row['active_lot']} <i>({row['calc_weight_kg']/1000.0:.1f} t, {row.get('calc_length_mm', 0):.0f} mm)</i>",
            axis=1
        )
        
        grouped_storage = temp_storage_df.groupby(['grade', 'thickness', 'calc_length_mm']).agg(
            coil_count=('active_lot', 'count'),
            total_weight=('calc_weight_kg', 'sum'),
            lots=('lot_detail', lambda x: "<br/>".join(x.dropna().astype(str).tolist()))
        ).reset_index()
    else:
        grouped_storage = pd.DataFrame(columns=['grade', 'thickness', 'calc_length_mm', 'coil_count', 'total_weight', 'lots'])

# =========================================================================
######################## WAREHOUSE COIL SITUATION (GROUPED CARDS & POPUP MODAL) ###############################################
# =========================================================================
    st.markdown("###  Warehouse Coil Situation ###")
    
    if not grouped_storage.empty:
        cols_per_row = 3
        card_rows = [grouped_storage.iloc[i:i+cols_per_row] for i in range(0, len(grouped_storage), cols_per_row)]
        
        coil_svg = """<svg width="75" height="62" viewBox="0 0 120 100" style="margin: 0 4px 4px 0; filter: drop-shadow(1px 2px 2px rgba(0,0,0,0.15));">
    <ellipse cx="40" cy="50" rx="30" ry="42" fill="#d0d0d0" stroke="#444" stroke-width="4"/>
    <path d="M 40 8 L 85 8 C 98 8, 98 92, 85 92 L 40 92" fill="#e8e8e8" stroke="#444" stroke-width="4"/>
    <path d="M 25 22 L 70 22" stroke="#222" stroke-width="5" stroke-linecap="round"/>
    <path d="M 25 78 L 70 78" stroke="#222" stroke-width="5" stroke-linecap="round"/>
    <ellipse cx="85" cy="50" rx="14" ry="42" fill="#fcfcfc" stroke="#444" stroke-width="4"/>
    <ellipse cx="85" cy="50" rx="10" ry="30" fill="#e0e0e0" stroke="#666" stroke-width="2"/>
    <ellipse cx="85" cy="50" rx="5" ry="15" fill="#333"/>
</svg>"""

        for row_idx, row_df in enumerate(card_rows):
            grid_cols = st.columns(cols_per_row)
            for col_idx, (_, row) in enumerate(row_df.iterrows()):
                with grid_cols[col_idx]:
                    grade = row['grade']
                    thick = row['thickness']
                    length = row['calc_length_mm']
                    count = row['coil_count']
                    
                    tot_w_kg = row['total_weight']
                    tot_w_tons = tot_w_kg / 1000.0
                    lots_list = row['lots']
                    
                    is_low = (tot_w_tons <= 20.0) or (count < 2)
                    border_color = "#ff4b4b" if is_low else "#2e7d32"
                    bg_color = "#fff5f5" if is_low else "#f1f8e9"
                    
                    coil_icons_html = "".join([coil_svg for _ in range(int(count))])
                    
                    card_html = f"""<div style="border: 2px solid {border_color}; background-color: {bg_color}; padding: 14px; border-radius: 8px; text-align: left; box-shadow: 0 2px 4px rgba(0,0,0,0.06); width: 100%; box-sizing: border-box; margin-bottom: 6px;">
<div style="font-weight: bold; color: #111; font-size: 16px; margin-bottom: 8px;">
{grade} | {thick} mm | {length:.0f} mm
</div>
<div style="margin-bottom: 8px; display: flex; justify-content: flex-start; align-items: center; flex-wrap: wrap;">
{coil_icons_html}
</div>
<div style="font-size: 13px; color: #333; margin-bottom: 8px;">
<b>Coils:</b> <span style="color: {'#ff4b4b' if count < 2 else '#1f77b4'}; font-weight: bold;">{count}</span> | <b>Weight:</b> <span style="color: {border_color}; font-weight: bold;">{tot_w_tons:.3f} t</span>
</div>
<div style="font-size: 12px; color: #111; line-height: 1.6; max-height: 90px; overflow-y: auto; background: rgba(255,255,255,0.85); padding: 8px; border-radius: 6px; border: 1px solid #ddd;">
<b style="color: #000; font-size: 12px;">LOTTO & ID DETAILS:</b><br/>{lots_list}
</div>
</div>"""
                    
                    with st.container():
                        st.markdown(card_html, unsafe_allow_html=True)
                        
                        group_rows = temp_storage_df[
                            (temp_storage_df['grade'] == grade) & 
                            (temp_storage_df['thickness'] == thick)
                        ]
                        
                        btn_key = f"manage_group_btn_{row_idx}_{col_idx}"
                        if st.button(f"⚙️ Manage / Split Group", key=btn_key, use_container_width=True):
                            open_coil_management_dialog(conn, grade, thick, length, group_rows)
    else:
        st.info("No active coils currently in storage.")
    
    st.write("---")

# =========================================================================
############################  QUALITY VERIFICATION SECTION  ###########################################################################
# =========================================================================
    with st.container(border=True):
        st.markdown(" Quality Verification (Tensile Testing)")
        if not prod_df.empty:
            qv_col1, qv_col2 = st.columns([1, 1.5], gap="large")
            
            with qv_col1:
                st.markdown("####  Select Batch")
                temp_qv_df = prod_df.sort_values(by="id").reset_index(drop=True)
                temp_qv_df['seq_id'] = temp_qv_df.index + 1
                
                qv_options = {}
                for _, r in temp_qv_df.iterrows():
                    seq_id = int(r["seq_id"])
                    real_db_id = int(r["id"])
                    g_name = str(r.get("grade", ""))
                    l_num = str(r.get("lotto_number", ""))
                    l_label = l_num if l_num != "None" and l_num != "" else f"DB-{real_db_id}"
                    qv_options[f"ID #{seq_id} | {g_name} | Lotto: {l_label}"] = real_db_id
                
                selected_qv_label = st.selectbox("Choose Coil for Lab Verification", options=list(qv_options.keys()), key="qv_batch_select")
                selected_qv_id = qv_options[selected_qv_label]
                
                qv_row = prod_df[prod_df['id'] == selected_qv_id].iloc[0]
                
                st.info(f"""
                **📋 Certificate & EN10025 Reference Targets:**
                - **Min Yield ($\sigma_{{yield}}$):** **{qv_row.get('yield_mpa', 0.0)} MPa**
                - **Min UTS ($\sigma_{{uts}}$):** **{qv_row.get('uts_mpa', 0.0)} MPa**
                - **Min Elongation:** **{qv_row.get('elongation_pct', 0.0)} %**
                """)

            with qv_col2:
                st.markdown(" 📊 Enter 3 Tensile Test Samples")
                
                with st.form(key=f"quality_verification_form_{selected_qv_id}"):
                    st.markdown("**1. Yield Stress ($\sigma_{yield}$ - MPa)**")
                    y1, y2, y3 = st.columns(3)
                    with y1: t1_y = st.number_input("Test 1", value=None, step=1.0, format="%.1f", placeholder="e.g. 525.0", key=f"t1_y_{selected_qv_id}")
                    with y2: t2_y = st.number_input("Test 2", value=None, step=1.0, format="%.1f", placeholder="e.g. 525.0", key=f"t2_y_{selected_qv_id}")
                    with y3: t3_y = st.number_input("Test 3", value=None, step=1.0, format="%.1f", placeholder="e.g. 525.0", key=f"t3_y_{selected_qv_id}")
                    
                    st.markdown("**2. Ultimate Tensile Strength ($\sigma_{uts}$ - MPa)**")
                    u1, u2, u3 = st.columns(3)
                    with u1: t1_u = st.number_input("Test 1", value=None, step=1.0, format="%.1f", placeholder="e.g. 580.0", key=f"t1_u_{selected_qv_id}")
                    with u2: t2_u = st.number_input("Test 2", value=None, step=1.0, format="%.1f", placeholder="e.g. 580.0", key=f"t2_u_{selected_qv_id}")
                    with u3: t3_u = st.number_input("Test 3", value=None, step=1.0, format="%.1f", placeholder="e.g. 580.0", key=f"t3_u_{selected_qv_id}")
                    
                    st.markdown("**3. Elongation Percentage (%)**")
                    e1, e2, e3 = st.columns(3)
                    with e1: t1_e = st.number_input("Test 1", value=None, step=0.1, format="%.1f", placeholder="e.g. 24.0", key=f"t1_e_{selected_qv_id}")
                    with e2: t2_e = st.number_input("Test 2", value=None, step=0.1, format="%.1f", placeholder="e.g. 24.0", key=f"t2_e_{selected_qv_id}")
                    with e3: t3_e = st.number_input("Test 3", value=None, step=0.1, format="%.1f", placeholder="e.g. 24.0", key=f"t3_e_{selected_qv_id}")
                    
                    submit_verification = st.form_submit_button(" Calculate Averages & Evaluate Status", use_container_width=True)

                    
                    if submit_verification:
                        inputs_list = [t1_y, t2_y, t3_y, t1_u, t2_u, t3_u, t1_e, t2_e, t3_e]
                        if any(val is None for val in inputs_list):
                            st.warning("⚠️ Please fill in all 9 tensile test input fields before evaluating.")
                        else:
                            avg_y = (t1_y + t2_y + t3_y) / 3.0
                            avg_u = (t1_u + t2_u + t3_u) / 3.0
                            avg_e = (t1_e + t2_e + t3_e) / 3.0
                            
                            ref_y = float(qv_row.get('yield_mpa', 0.0))
                            ref_u = float(qv_row.get('uts_mpa', 0.0))
                            ref_e = float(qv_row.get('elongation_pct', 0.0))
                        
                        #  EZIO:: Engineering Allowance
                        stress_allowance_mpa = 30.0
                        elong_allowance_pct = 7.0

                        effective_ref_y = ref_y - stress_allowance_mpa
                        effective_ref_u = ref_u - stress_allowance_mpa
                        effective_ref_e = ref_e - elong_allowance_pct

                        if avg_y >= ref_y and avg_u >= ref_u and avg_e >= ref_e:
                            new_status_val = "Verified ✅"
                            st.success(f"✅ **Fully Verified (Passed)** — Averages meet standard requirements!")
                        elif avg_y >= effective_ref_y and avg_u >= effective_ref_u and avg_e >= effective_ref_e:
                            new_status_val = "Verified ✅"
                            st.info(f"⚠️ **Conditionally Verified (Within Allowance)** — Averages are slightly below standard targets but within the acceptable 10 MPa / 5.0% engineering tolerance buffer.")
                        else:
                            new_status_val = "Non-Compliant / Rejected ❌"
                            st.error(f"❌ **Non-Compliant / Rejected (Failed)** — Averages exceed the allowable engineering tolerance limits.")
                        
                        # --- SAVE STATUS TO DATABASE INSTANTLY ---
                        with conn.session as s:
                            s.execute(
                                text("UPDATE materials SET quality_status = :qstat WHERE id = :id"),
                                {"qstat": new_status_val, "id": int(selected_qv_id)}
                            )
                            s.commit()
                        
                        st.toast(f"Status updated to '{new_status_val}' in database!", icon="💾")
                        st.cache_data.clear()
                        st.rerun()
                        
        else:
            st.info("No batches available for quality verification.")

    st.write("---")

######################### =========================================================================
################################################ PRODUCTION BATCH QUEUE TABLE  ###################################################
#########################    =========================================================================  ########################
    st.markdown(f"###  Production Batch Queue ({family_name})")
    search_query = st.text_input("🔍 Search by ID, Lotto Number, or Component Code", value="")
    
    dropdown_options_df = pd.DataFrame()
    
    if not prod_df.empty:
        prod_df['current_weight_kg'] = prod_df['rd_remaining_weight_kg'].fillna(prod_df['coil_weight_kg'])
        prod_df['current_length_mm'] = prod_df['rd_remaining_length_mm'].fillna(prod_df['coil_length_mm'])
        prod_df['current_weight_kg'] = prod_df['current_weight_kg'].apply(lambda x: 0.0 if x <= 0.001 else x)
        prod_df['current_length_mm'] = prod_df['current_length_mm'].apply(lambda x: 0.0 if x <= 0.01 else x)

        prod_df['current_weight_tons'] = prod_df['current_weight_kg'] / 1000.0

        display_df = prod_df[[
            "id", "grade", "thickness", "yield_mpa", "uts_mpa", "elongation_pct", 
            "current_weight_tons", "current_length_mm", "lotto_number", "lotto_figlio", "provider", 
            "rd_remaining_weight_kg", "is_promoted", "quality_status"
        ]].copy()
        
        display_df = display_df.sort_values(by="id").reset_index(drop=True)
        
        display_df["ID"] = display_df.index + 1
        display_df["real_db_id"] = display_df["id"]

        def determine_status(row):
            q_status = row.get("quality_status")
            if q_status and pd.notna(q_status) and str(q_status).strip() != "":
                return str(q_status)
            elif row["rd_remaining_weight_kg"] is not None and float(row["rd_remaining_weight_kg"]) <= 0.001:
                return "🔴 Not Available"
            elif row["is_promoted"] == 1:
                return "🔬 R&D"
            else:
                return "🟢 Standby"

        display_df["Status"] = display_df.apply(determine_status, axis=1)
        display_df = display_df.drop(columns=["id", "rd_remaining_weight_kg", "is_promoted", "quality_status"])
        
        display_df = display_df.rename(columns={
            "current_weight_tons": "Remaining Weight [TON]",
            "current_length_mm": "Remaining Length [mm]",
            "lotto_number": "Lotto Father",
            "lotto_figlio": "Component Code"
        })
        
        if search_query.strip():
            q = search_query.strip().lower()
            mask = (
                display_df["ID"].astype(str).str.lower().str.contains(q) |
                display_df["Lotto Father"].astype(str).str.lower().str.contains(q) | 
                display_df["Component Code"].astype(str).str.lower().str.contains(q)
            )
            display_df = display_df[mask]

        dropdown_options_df = display_df.copy()

        cols_order = [
            "ID", "grade", "thickness", "yield_mpa", "uts_mpa", "elongation_pct", 
            "Remaining Weight [TON]", "Remaining Length [mm]", "Lotto Father", "Component Code", "provider", "Status", "real_db_id"
        ]
        table_display_view = display_df[[c for c in cols_order if c in display_df.columns and c != "real_db_id"]]
        #  EZIO:: LOW STOCK     
        def highlight_low_stock(row):
            w_tons = row.get('Remaining Weight [TON]', 0)
            if 0.0001 < w_tons <= 20.0:
                return ['background-color: #ffebee'] * len(row)
            return [''] * len(row)

        styled_df = table_display_view.style.apply(highlight_low_stock, axis=1)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
    else:
        st.info("No production batches in database queue.")

    st.write("---")
    
    # =========================================================================
    ################################################# CENTERED ACTIONS & EDITS CONTROLS ###########################
    # =========================================================================
    st.markdown("###  Actions & Edits")
    
    with st.container(border=True):
        table_df = dropdown_options_df if not dropdown_options_df.empty else pd.DataFrame()
        
        if not table_df.empty:
            batch_options = {}
            default_index = 0
            preset_id = st.session_state.get("preset_selected_id", None)
            
            for seq_idx, (_, row) in enumerate(table_df.iterrows(), 1):
                db_id = int(row["real_db_id"])
                grade = str(row.get("grade", ""))
                father = str(row.get("Lotto Father", ""))
                son = str(row.get("Component Code", ""))
                
                father_str = father if father != "None" and father != "" else ""
                son_str = son if son != "None" and son != "" else ""
                
                if father_str and son_str:
                    lotto_str = f"{father_str} → {son_str}"
                elif son_str:
                    lotto_str = son_str
                else:
                    lotto_str = father_str if father_str else "No Lotto"
                    
                label = f"ID #{seq_idx} | {grade} | Lotto: {lotto_str}"
                batch_options[label] = db_id
                
                # Instantly match the preset ID clicked from the card above
                if preset_id is not None and db_id == int(preset_id):
                    default_index = seq_idx - 1

            selected_label = st.selectbox(
                "Select Production Batch", 
                options=list(batch_options.keys()), 
                index=default_index,
                key="actions_batch_select_box"
            )
            selected_prod_id = batch_options[selected_label]
            
            if preset_id is not None:
                st.session_state.preset_selected_id = None

            matching_rows = prod_df[prod_df['id'] == selected_prod_id]
            if not matching_rows.empty:
                selected_row = matching_rows.iloc[0]
                is_already_promoted = (selected_row.get("is_promoted", 0) == 1)

                act_col1, act_col2 = st.columns(2)
                with act_col1:
                    if is_already_promoted:
                        st.info("Already in R&D")
                    else:
                        if st.button(" Move to R&D", use_container_width=True):
                            promote_cloud_material(conn, selected_prod_id)
                            st.success("Batch promoted to R&D!")
                            st.cache_data.clear()
                            st.rerun()
                            
                with act_col2:
                    if "confirm_delete_id" not in st.session_state:
                        st.session_state.confirm_delete_id = None

                    if st.session_state.confirm_delete_id == selected_prod_id:
                        st.warning("Are you sure?")
                        col_yes, col_no = st.columns(2)
                        with col_yes:
                            if st.button("Yes, Delete", key=f"yes_del_{selected_prod_id}", use_container_width=True):
                                delete_cloud_material(conn, selected_prod_id)
                                st.session_state.confirm_delete_id = None
                                st.success("Batch deleted!")
                                st.cache_data.clear()
                                st.rerun()
                        with col_no:
                            if st.button("Cancel", key=f"no_del_{selected_prod_id}", use_container_width=True):
                                st.session_state.confirm_delete_id = None
                                st.rerun()
                    else:
                        if st.button("🗑 Delete Batch", use_container_width=True):
                            st.session_state.confirm_delete_id = selected_prod_id
                            st.rerun()

                with st.expander("✏️ Edit Selected Batch Values"):
                    with st.form(key=f"edit_form_{selected_prod_id}"):
                        new_grade = st.text_input("Grade", value=str(selected_row.get("grade", "")))
                        new_thickness = st.number_input("Thickness [mm]", value=float(selected_row.get("thickness", 0.0)), step=0.1)
                        new_yield = st.number_input("Yield Stress [MPa]", value=float(selected_row.get("yield_mpa", 0.0)), step=5.0)
                        new_uts = st.number_input("Failure Stress [MPa]", value=float(selected_row.get("uts_mpa", 0.0)), step=5.0)
                        new_elong = st.number_input("Elongation [%]", value=float(selected_row.get("elongation_pct", 0.0)), step=0.5)

                        db_weight_kg = float(selected_row.get("coil_weight_kg", 0.0) or 0.0)
                        initial_weight_tons = db_weight_kg / 1000.0 if db_weight_kg else 0.0
                        new_weight_tons = st.number_input("Coil Weight [TON]", value=initial_weight_tons, step=1.0, format="%.3f")
                        new_length = st.number_input("Coil Length [mm]", value=float(selected_row.get("coil_length_mm", 0.0)), step=100.0)
                        
                        val_padre = selected_row.get("lotto_number", "") if pd.notna(selected_row.get("lotto_number")) else ""
                        val_figlio = selected_row.get("lotto_figlio", "") if pd.notna(selected_row.get("lotto_figlio")) else ""
                        
                        new_lotto_padre = st.text_input("Lotto Father", value=str(val_padre))
                        new_component_code = st.text_input("Component Code", value=str(val_figlio))
                        new_provider = st.text_input("Provider", value=str(selected_row.get("provider", "")))
                        
                        new_certificate = st.file_uploader(
                            "Upload Material Certificate (PDF / Image)", 
                            type=["pdf", "png", "jpg", "jpeg"],
                            key=f"cert_file_{selected_prod_id}"
                        )
                        
                        if st.form_submit_button("💾 Save Changes", use_container_width=True):
                            cert_file_obj = new_certificate if 'new_certificate' in locals() else None            

                            update_cloud_material(
                                conn,
                                selected_prod_id, 
                                grade=new_grade, thickness=new_thickness, 
                                yield_mpa=new_yield, uts_mpa=new_uts, 
                                elongation_pct=new_elong, lotto_number=new_lotto_padre, 
                                lotto_figlio=new_component_code, provider=new_provider,
                                coil_weight_kg=new_weight_tons * 1000.0, coil_length_mm=new_length,
                                uploaded_file=cert_file_obj  
                            )
                            st.success("Batch updated successfully!")
                            st.cache_data.clear()
                            st.rerun()

                with st.expander("✂️ Split Coil (Father -> Sons)", expanded=False):
                    rem_w_val = selected_row.get("rd_remaining_weight_kg")
                    current_w_kg = float(rem_w_val) if pd.notna(rem_w_val) else float(selected_row.get("coil_weight_kg", 0.0))
                    current_w_tons = current_w_kg / 1000.0

                    rem_l_val = selected_row.get("rd_remaining_length_mm")
                    current_l = float(rem_l_val) if pd.notna(rem_l_val) else float(selected_row.get("coil_length_mm", 0.0))
                    
                    base_lotto = selected_row.get('lotto_figlio') if pd.notna(selected_row.get('lotto_figlio')) else selected_row.get('lotto_number', '')
                    
                    st.write(f"Available to split: **{current_w_tons:.3f} TON** / **{current_l:.2f} mm**")
                    
                    num_children = st.number_input("Number of Components", min_value=2, max_value=10, value=2, step=1)
                    
                    with st.form(key=f"split_form_{selected_prod_id}"):
                        children_inputs = []
                        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        weight_per_mm_kg = current_w_kg / current_l if current_l > 0 else 0.0
                        
                        for i in range(int(num_children)):
                            st.markdown(f"**Component {i+1} ({letters[i]})**")
                            cc1, cc2, cc3 = st.columns(3)
                            with cc1:
                                c_lotto = st.text_input(f"Component Code {i+1}", value=f"{base_lotto}-{letters[i]}", key=f"split_lotto_{selected_prod_id}_{i}")
                            with cc2:
                                c_length = st.number_input(f"Length {i+1} [mm]", min_value=0.0, max_value=current_l, value=0.0, key=f"split_l_{selected_prod_id}_{i}")
                            with cc3:
                                c_weight_kg = c_length * weight_per_mm_kg
                                c_weight_tons = c_weight_kg / 1000.0
                                st.write(f"Weight {i+1} [TON]")
                                st.metric(label="", value=f"{c_weight_tons:.3f} TON")
                            
                            children_inputs.append({"lotto": c_lotto, "weight": c_weight_kg, "length": c_length})
                            st.write("---")
                            
                        if st.form_submit_button("Confirm Split & Close Parent", use_container_width=True):
                            total_l = sum(c["length"] for c in children_inputs)
                            if total_l > current_l:
                                st.error("Sum of child lengths exceeds parent remaining length!")
                            else:
                                split_cloud_material(conn, selected_prod_id, children_inputs, auto_promote_rd=True)
                                for idx in range(int(num_children)):
                                    key_l = f"split_l_{selected_prod_id}_{idx}"
                                    key_lot = f"split_lotto_{selected_prod_id}_{idx}"
                                    if key_l in st.session_state: del st.session_state[key_l]
                                    if key_lot in st.session_state: del st.session_state[key_lot]
                                st.success(f"Coil successfully split, automatically pushed to R&D status!")
                                st.cache_data.clear()
                                st.rerun()
            else:
                st.warning("Selected batch details not found in production data.")
        else:
            st.info("No production batches found in the current view.")

    st.write("---")
    
    # =========================================================================
    ###################  MANUAL FORM ENTRY & FILE UPLOAD SECTION ####################################
    # =========================================================================
    with st.expander("✍ Manual Form Entry & File Upload (New Batch)", expanded=False):
        entry_tab1, entry_tab2 = st.tabs(["Manual Form Entry", "📁 File Upload (.txt / .csv / .xlsx)"])
        
        with entry_tab1:
            with st.form("prod_form_manual", clear_on_submit=True):
                ca, cb = st.columns(2)
                with ca:
                    grade = st.text_input("1. Material Grade / Name", value="")
                    thickness = st.number_input("2. Thickness [mm]", value=None, step=0.1)
                    sig_yield = st.number_input("3. Yield Stress σ_yield [MPa]", value=None, step=5.0)
                    sig_fail = st.number_input("4. Ultimate Stress σ_uts [MPa]", value=None, step=5.0)
                    elongation = st.number_input("5. Elongation [%]", value=None, step=0.5)
                with cb:
                    lotto_padre = st.text_input("6. Lotto Father", value="")
                    lotto_figlio = st.text_input("7. Component Code", value="")
                    provider = st.text_input("8. Material Supplier", value="")
                    coil_weight_tons = st.number_input("9. Coil Weight [TON]", value=None, step=10.0, format="%.3f")
                    coil_length = st.number_input("10. Coil Length [mm]", value=None, step=1000.0)
                
                uploaded_cert = st.file_uploader("Upload Test Certificate (.pdf, .png, .jpg)", type=["pdf", "png", "jpg", "jpeg"])
                
                if st.form_submit_button("💾 Save Batch", use_container_width=True):
                    cert_url = None
                    active_lotto_label = lotto_padre if lotto_padre else (lotto_figlio if lotto_figlio else "batch")
                    if uploaded_cert is not None:
                        cert_url = upload_certificate_to_cloud(uploaded_cert, active_lotto_label)

                    coil_weight_kg_val = (coil_weight_tons * 1000.0) if coil_weight_tons else 0.0

                    insert_cloud_material(
                        conn,
                        grade, thickness, sig_yield, sig_fail, elongation, 
                        lotto_padre=lotto_padre, lotto_figlio=lotto_figlio, provider=provider, 
                        family=st.session_state.active_family,
                        cert_path=cert_url,
                        coil_weight=coil_weight_kg_val,
                        coil_length_mm=coil_length
                    )
                    st.toast("✅ Batch registered into Production database with 'Standby' status!")
                    st.cache_data.clear()
                    st.rerun()

        with entry_tab2:
            if "file_uploader_key" not in st.session_state:
                st.session_state.file_uploader_key = 0

            uploaded_file = st.file_uploader("Upload Batch Import File", type=["txt", "csv", "xlsx"], key=f"uploader_{st.session_state.file_uploader_key}")  
            if uploaded_file is not None:
                try:
                    filename = uploaded_file.name.lower()
                    if filename.endswith(".txt") or filename.endswith(".csv"):
                        df_upload = pd.read_csv(
                            uploaded_file, 
                            header=None,
                            names=["grade", "thickness", "yield_ns", "uts_ns", "elong_ns", "lotto_number", "lotto_figlio", "provider", "coil_weight_TON", "coil_length_mm"]
                        )
                    elif filename.endswith(".xlsx"):
                        df_upload = pd.read_excel(uploaded_file)
                        if "lotto_figlio" not in df_upload.columns:
                            df_upload["lotto_figlio"] = None
                        if "coil_weight_kg" in df_upload.columns and "coil_weight_TON" not in df_upload.columns:
                            df_upload = df_upload.rename(columns={"coil_weight_kg": "coil_weight_TON"})

                    for col in ["grade", "lotto_number", "lotto_figlio", "provider"]:
                        if col in df_upload.columns:
                            df_upload[col] = df_upload[col].astype(str).str.strip()
                            df_upload[col] = df_upload[col].apply(lambda x: None if x in ["nan", "None", ""] else x)

                    st.dataframe(df_upload, use_container_width=True, hide_index=True)

                    if st.button("Import Batches to Database", use_container_width=True):
                        success_count = 0
                        for _, r in df_upload.iterrows():
                            try:
                                def safe_float(val):
                                    if pd.isna(val) or str(val).strip().lower() in ["nan", "none", ""]:
                                        return 0.0
                                    cleaned = ''.join(c for c in str(val) if c.isdigit() or c in '.-')
                                    return float(cleaned) if cleaned else 0.0

                                grade = str(r.get("grade", "Unknown"))
                                thickness = safe_float(r.get("thickness", 0.0))
                                yield_val = safe_float(r.get("yield_mpa") if "yield_mpa" in r else r.get("yield_ns", 0.0))
                                uts_val = safe_float(r.get("uts_mpa") if "uts_mpa" in r else r.get("uts_ns", 0.0))
                                elong_val = safe_float(r.get("elongation_pct") if "elongation_pct" in r else r.get("elong_ns", 0.0))
                                
                                lotto_num = r.get("lotto_number")
                                lotto_son = r.get("lotto_figlio")
                                provider = str(r.get("provider", ""))
                                w_tons = safe_float(r.get("coil_weight_TON", 0.0))
                                l_mm = safe_float(r.get("coil_length_mm", 0.0))
                                
                                insert_cloud_material(
                                    conn,
                                    grade=grade, thickness=thickness, yield_mpa=yield_val, uts_mpa=uts_val, elongation=elong_val,
                                    lotto_padre=lotto_num, lotto_figlio=lotto_son, provider=provider,
                                    family=st.session_state.active_family, cert_path=None,
                                    coil_weight=w_tons * 1000.0, coil_length_mm=l_mm
                                )
                                success_count += 1
                            except Exception as ex:
                                st.error(f"Error importing row: {ex}")
                        
                        if success_count > 0:
                            st.success(f"Successfully imported {success_count} batches!")
                            st.cache_data.clear()
                            st.rerun()
                except Exception as e:
                    st.error(f"Error reading upload file: {e}")