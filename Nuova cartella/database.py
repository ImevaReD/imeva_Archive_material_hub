import streamlit as st
import pandas as pd
import time
import json
from sqlalchemy import text
from supabase import create_client

SUPABASE_URL = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_certificate_to_cloud(uploaded_file, lotto_identifier):
    """Uploads a certificate file to the Supabase 'certificate_storage' bucket and returns its public URL."""
    try:
        file_ext = uploaded_file.name.split('.')[-1]
        file_path = f"certs/{lotto_identifier}_{int(time.time())}.{file_ext}"
        
        # Push bytes to Supabase Storage
        supabase_client.storage.from_("certificate_storage").upload(
            path=file_path,
            file=uploaded_file.getvalue(),
            file_options={"content-type": uploaded_file.type, "upsert": "true"}
        )
        
        public_url = supabase_client.storage.from_("certificate_storage").get_public_url(file_path)
        return public_url
    except Exception as e:
        st.error(f"Cloud Storage Upload Error: {e}")
        return None
    
@st.dialog("Supplier Mill Certificate Viewer", width="large")
def show_cert_modal(mat_name, cert_url, info):
    st.markdown(f"### Material: `{mat_name}`")
    st.write(f"**Grade:** {info.get('grade')} | **Lotto:** {info.get('lotto_number')} | **Provider:** {info.get('provider')}")
    st.divider()
    
    if cert_url:
        st.markdown(f"🔗 [Open certificate in a new browser tab]({cert_url})", unsafe_allow_html=True)
        if cert_url.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            st.image(cert_url, caption="Certificate Document", use_container_width=True)
        else:
            st.info("ℹ️ Inline PDF previews are often restricted by security policies. Use the link above if the viewer below appears empty.")
            st.markdown(f'<iframe src="{cert_url}" width="100%" height="600px"></iframe>', unsafe_allow_html=True)
    else:
        st.warning("No certificate URL provided.")

def init_spectrometer_reports(conn):
    """Auto-restore spectrometer reports from Supabase into session state on app load."""
    if "chemical_reports" not in st.session_state:
        st.session_state.chemical_reports = {}

    if "reports_loaded_from_cloud" not in st.session_state:
        try:
            df_cloud_reports = conn.query("SELECT id, spectrometer_data_json FROM materials WHERE spectrometer_data_json IS NOT NULL;", ttl=0)
            
            for _, row in df_cloud_reports.iterrows():
                m_id = row.get("id")
                spec_json = row.get("spectrometer_data_json")
                
                if isinstance(spec_json, str):
                    try:
                        spec_json = json.loads(spec_json)
                    except Exception:
                        spec_json = {}
                        
                if spec_json and isinstance(spec_json, dict):
                    df_records = spec_json.get("df_records", [])
                    comp = spec_json.get("comp", {})
                    if df_records:
                        reconstructed_df = pd.DataFrame(df_records)
                        st.session_state.chemical_reports[int(m_id)] = {
                            "df": reconstructed_df,
                            "comp": comp
                        }
            st.session_state.reports_loaded_from_cloud = True
        except Exception as e:
            pass

def fetch_cloud_materials(conn, hub_section="PROD", family='METALS'):
    """Fetch entries filtered by hub section, including Crush, RD, and Quality Status fields."""
    
    if hub_section == "RD":
        query = """
            SELECT 
                id, parent_id, family, hub_section, grade, thickness, 
                yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, coil_length_mm, 
                rd_remaining_weight_kg, rd_remaining_length_mm, rd_notes, 
                lotto_number, lotto_figlio, provider, 
                certificate_url AS datasheet, 
                is_promoted, codice, description, quantity, quality_status
            FROM materials 
            WHERE (hub_section = 'RD' OR is_promoted = 1) AND family = :fam 
            ORDER BY id DESC;
        """
        return conn.query(query, params={"fam": family}, ttl=0)
        
    elif hub_section == "CRUSH":
        query = """
            SELECT 
                id, parent_id, family, hub_section, grade, 
                lotto_number, lotto_figlio, codice, description, quantity, rd_notes, quality_status
            FROM materials 
            WHERE hub_section = 'CRUSH' AND family = :fam 
            ORDER BY id DESC;
        """
        return conn.query(query, params={"fam": family}, ttl=0)
        
    else:
        # Default Production Hub view
        query = """
            SELECT 
                id, parent_id, family, hub_section, grade, thickness, 
                yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, coil_length_mm, 
                rd_remaining_weight_kg, rd_remaining_length_mm, rd_notes, 
                lotto_number, lotto_figlio, provider, 
                certificate_url AS datasheet, 
                is_promoted, codice, description, quantity, quality_status
            FROM materials 
            WHERE (hub_section = 'PROD' OR hub_section IS NULL) AND family = :fam 
            ORDER BY id DESC;
        """
        return conn.query(query, params={"fam": family}, ttl=0)

def insert_cloud_material(
    conn, grade, thickness, yield_mpa, uts_mpa, elongation, lotto_padre, lotto_figlio, provider, 
    family, cert_path=None, coil_weight=0.0, coil_length=0.0, coil_length_mm=None
):
    """Insert new production batch into Supabase supporting mutually exclusive Padre/Figlio lottos."""
    actual_length = coil_length_mm if coil_length_mm is not None else coil_length
    
    with conn.session as s:
        s.execute(
            text("""
                INSERT INTO materials (
                    grade, thickness, yield_mpa, uts_mpa, elongation_pct, 
                    lotto_number, lotto_figlio, provider, family, certificate_url, 
                    coil_weight_kg, coil_length_mm,
                    rd_remaining_weight_kg, rd_remaining_length_mm, quality_status
                )
                VALUES (
                    :grade, :thick, :ys, :uts, :elong, 
                    :l_padre, :l_figlio, :prov, :fam, :cert, 
                    :c_wt, :c_len, :c_wt, :c_len, :qstat
                )
            """),
            {
                "grade": grade, "thick": thickness, "ys": yield_mpa, 
                "uts": uts_mpa, "elong": elongation, 
                "l_padre": lotto_padre if lotto_padre else None, 
                "l_figlio": lotto_figlio if lotto_figlio else None, 
                "prov": provider, "fam": family, "cert": cert_path, 
                "c_wt": coil_weight, "c_len": actual_length,
                "qstat": "🟢 Standby"
            }
        )
        s.commit()

def split_cloud_material(conn, parent_id, children_list, auto_promote_rd=True):
    """Splits a parent coil into a dynamic number of child coils and zeros out the parent stock."""
    with conn.session as s:
        parent_res = s.execute(
            text("SELECT * FROM materials WHERE id = :id"), 
            {"id": int(parent_id)}
        ).mappings().first()
        
        if not parent_res:
            raise Exception("Parent material not found.")

        parent_lotto = parent_res['lotto_number'] if parent_res['lotto_number'] else parent_res['lotto_figlio']
        promoted_val = 1 if auto_promote_rd else 0

        for child in children_list:
            s.execute(
                text("""
                    INSERT INTO materials (
                        parent_id, family, hub_section, grade, thickness, 
                        yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, 
                        coil_length_mm, rd_remaining_weight_kg, rd_remaining_length_mm, 
                        lotto_number, lotto_figlio, provider, certificate_url, is_promoted
                    ) VALUES (
                        :parent_id, :family, :hub_section, :grade, :thickness, 
                        :yield_mpa, :uts_mpa, :elongation_pct, :coil_weight_kg, 
                        :coil_length_mm, :rd_remaining_weight_kg, :rd_remaining_length_mm, 
                        :lotto_number, :lotto_figlio, :provider, :certificate_url, :is_promoted
                    )
                """),
                {
                    "parent_id": parent_id,
                    "family": parent_res.get("family", "METALS"),
                    "hub_section": parent_res.get("hub_section", "PROD"),
                    "grade": parent_res["grade"],
                    "thickness": parent_res["thickness"],
                    "yield_mpa": parent_res["yield_mpa"],
                    "uts_mpa": parent_res["uts_mpa"],
                    "elongation_pct": parent_res["elongation_pct"],
                    "coil_weight_kg": child["weight"],
                    "coil_length_mm": child["length"],
                    "rd_remaining_weight_kg": child["weight"],
                    "rd_remaining_length_mm": child["length"],
                    "lotto_number": parent_lotto,
                    "lotto_figlio": child["lotto"],
                    "provider": parent_res["provider"],
                    "certificate_url": parent_res.get("certificate_url"),
                    "is_promoted": promoted_val
                }
            )

        s.execute(
            text("""
                UPDATE materials 
                SET rd_remaining_weight_kg = 0.0, 
                    rd_remaining_length_mm = 0.0 
                WHERE id = :id
            """),
            {"id": int(parent_id)}
        )
        s.commit()

def log_material_consumption(conn, material_id, weight_used_kg, length_used_mm, notes=""):
    """Deducts used weight/length from the selected material batch safely and clears cache."""
    with conn.session as s:
        s.execute(
            text("""
                UPDATE materials 
                SET rd_remaining_weight_kg = GREATEST(0.0, COALESCE(rd_remaining_weight_kg, coil_weight_kg) - :w_used),
                    rd_remaining_length_mm = GREATEST(0.0, COALESCE(rd_remaining_length_mm, coil_length_mm) - :l_used),
                    rd_notes = CASE 
                        WHEN rd_notes IS NULL OR rd_notes = '' THEN :notes
                        ELSE rd_notes || ' | ' || :notes
                    END
                WHERE id = :mat_id
            """),
            {"w_used": weight_used_kg, "l_used": length_used_mm, "notes": notes, "mat_id": int(material_id)}
        )
        s.commit()
    st.cache_data.clear()

def promote_cloud_material(conn, material_id):
    """Marks batch as promoted while keeping it visible in Production Hub."""
    with conn.session as s:
        s.execute(text('UPDATE materials SET is_promoted = 1 WHERE id = :id'), {"id": int(material_id)})
        s.commit()

def delete_cloud_material(conn, material_id):
    """Deletes a material batch, safely handling child foreign key constraints by unlinking."""
    with conn.session as s:
        s.execute(
            text('UPDATE materials SET parent_id = NULL WHERE parent_id = :id'), 
            {"id": int(material_id)}
        )
        s.execute(
            text('DELETE FROM materials WHERE id = :id'), 
            {"id": int(material_id)}
        )
        s.commit()

def demote_cloud_material(conn, material_id):
    """Un-promote batch so it returns to production and leaves R&D deck."""
    with conn.session as s:
        s.execute(text('''
            UPDATE materials
            SET is_promoted = 0,
                experimental_curves_json = NULL,
                calculated_sigy_mpa = NULL,
                calculated_uts_mpa = NULL,
                calculated_e_gpa = NULL,
                calculated_elongation_pct = NULL
            WHERE id = :id
        '''), {"id": int(material_id)})
        s.commit()
    if material_id in st.session_state.get("experimental_curves", {}):
        del st.session_state.experimental_curves[material_id]
    st.cache_data.clear()

def update_cloud_material(
    conn, mat_id, grade, thickness, yield_mpa, uts_mpa, elongation_pct, 
    lotto_number, lotto_figlio, provider, coil_weight_kg, coil_length_mm, uploaded_file=None
):
    cert_url = None
    if uploaded_file is not None:
        file_path = f"certs/{mat_id}_{uploaded_file.name}"
        try:
            supabase_client.storage.from_("certificate_storage").upload(
                file_path, 
                uploaded_file.getvalue(), 
                file_options={"upsert": "true", "content-type": uploaded_file.type}
            )
            res = supabase_client.storage.from_("certificate_storage").get_public_url(file_path)
            cert_url = res.get("publicUrl") or res.get("public_url") if isinstance(res, dict) else res
        except Exception as e:
            st.error(f"Detailed Upload Error: {str(e)}")

    with conn.session as s:
        if cert_url is not None:
            s.execute(
                text("""
                    UPDATE materials SET 
                        grade = :grade,
                        thickness = :thickness,
                        yield_mpa = :yield_mpa,
                        uts_mpa = :uts_mpa,
                        elongation_pct = :elongation_pct,
                        lotto_number = :lotto_number,
                        lotto_figlio = :lotto_figlio,
                        provider = :provider,
                        coil_weight_kg = :coil_weight_kg,
                        coil_length_mm = :coil_length_mm,
                        certificate_url = :certificate_url
                    WHERE id = :id
                """),
                params={
                    "id": int(mat_id), "grade": grade, "thickness": float(thickness),
                    "yield_mpa": float(yield_mpa), "uts_mpa": float(uts_mpa),
                    "elongation_pct": float(elongation_pct), "lotto_number": lotto_number,
                    "lotto_figlio": lotto_figlio, "provider": provider,
                    "coil_weight_kg": float(coil_weight_kg), "coil_length_mm": float(coil_length_mm),
                    "certificate_url": cert_url
                }
            )
        else:
            s.execute(
                text("""
                    UPDATE materials SET 
                        grade = :grade,
                        thickness = :thickness,
                        yield_mpa = :yield_mpa,
                        uts_mpa = :uts_mpa,
                        elongation_pct = :elongation_pct,
                        lotto_number = :lotto_number,
                        lotto_figlio = :lotto_figlio,
                        provider = provider,
                        coil_weight_kg = :coil_weight_kg,
                        coil_length_mm = :coil_length_mm
                    WHERE id = :id
                """),
                params={
                    "id": int(mat_id), "grade": grade, "thickness": float(thickness),
                    "yield_mpa": float(yield_mpa), "uts_mpa": float(uts_mpa),
                    "elongation_pct": float(elongation_pct), "lotto_number": lotto_number,
                    "lotto_figlio": lotto_figlio, "provider": provider,
                    "coil_weight_kg": float(coil_weight_kg), "coil_length_mm": float(coil_length_mm)
                }
            )
        s.commit()
@st.dialog("⚙️ Manage & Split Coil Batch", width="large")
def open_coil_management_dialog(grade, thick, length, group_rows):
    st.write(f"### 📦 Selected Group: **{grade} | {thick} mm | {length:.0f} mm**")
    st.markdown("Select a specific coil/lotto from this group to manage or split:")
    
    # Build sequential ID lookup matching the main queue table sorting
    sorted_full_df = prod_df.sort_values(by="id").reset_index(drop=True)
    
    coil_options = {}
    for _, row in group_rows.iterrows():
        db_id = int(row['id'])
        
        # Find its exact sequential queue ID (1, 2, 3...)
        match_idx = sorted_full_df[sorted_full_df['id'] == db_id].index
        seq_id = int(match_idx[0] + 1) if not match_idx.empty else db_id
        
        father = row.get('lotto_number', '')
        son = row.get('lotto_figlio', '')
        lotto_str = f"{father} → {son}" if (father and son) else (son or father or "No Lotto")
        
        # Matches sequential numbering from the table
        label = f"Queue ID #{seq_id} | Lotto: {lotto_str}"
        coil_options[label] = db_id

    selected_coil_label = st.selectbox("Choose Specific Coil", options=list(coil_options.keys()))
    selected_db_id = coil_options[selected_coil_label]
    
    # Get the specific row DATAAA
    selected_row = group_rows[group_rows['id'].astype(int) == int(selected_db_id)].iloc[0]
    
    st.write("---")
    tabs = st.tabs(["✂️ Split Coil", "✏️ Edit Values", "🗑 Delete / Move"])
    
    with tabs[0]:
        st.markdown("#### ✂️ Split Coil (Father -> Sons)")
        rem_w_val = selected_row.get("rd_remaining_weight_kg")
        current_w_kg = float(rem_w_val) if pd.notna(rem_w_val) else float(selected_row.get("coil_weight_kg", 0.0))
        current_w_tons = current_w_kg / 1000.0

        rem_l_val = selected_row.get("rd_remaining_length_mm")
        current_l = float(rem_l_val) if pd.notna(rem_l_val) else float(selected_row.get("coil_length_mm", 0.0))
        
        base_lotto = selected_row.get('lotto_figlio') if pd.notna(selected_row.get('lotto_figlio')) else selected_row.get('lotto_number', '')
        
        st.info(f"Available to split: **{current_w_tons:.3f} TON** / **{current_l:.2f} mm**")
        
        num_children = st.number_input("Number of Components", min_value=2, max_value=10, value=2, step=1, key=f"modal_num_child_{selected_db_id}")
        
        with st.form(key=f"modal_split_form_{selected_db_id}"):
            children_inputs = []
            letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            weight_per_mm_kg = current_w_kg / current_l if current_l > 0 else 0.0
            
            for i in range(int(num_children)):
                st.markdown(f"**Component {i+1} ({letters[i]})**")
                cc1, cc2, cc3 = st.columns(3)
                with cc1:
                    c_lotto = st.text_input(f"Component Code {i+1}", value=f"{base_lotto}-{letters[i]}", key=f"modal_split_lotto_{selected_db_id}_{i}")
                with cc2:
                    c_length = st.number_input(f"Length {i+1} [mm]", min_value=0.0, max_value=current_l, value=0.0, key=f"modal_split_l_{selected_db_id}_{i}")
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
                    split_cloud_material(selected_db_id, children_inputs, auto_promote_rd=True)
                    st.success("Coil successfully split and pushed to R&D!")
                    st.cache_data.clear()
                    st.rerun()

    with tabs[1]:
        st.markdown("#### ✏️ Edit Selected Batch Values")
        with st.form(key=f"modal_edit_form_{selected_db_id}"):
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
            
            if st.form_submit_button("💾 Save Changes", use_container_width=True):
                update_cloud_material(
                    selected_db_id, 
                    grade=new_grade, thickness=new_thickness, 
                    yield_mpa=new_yield, uts_mpa=new_uts, 
                    elongation_pct=new_elong, lotto_number=new_lotto_padre, 
                    lotto_figlio=new_component_code, provider=new_provider,
                    coil_weight_kg=new_weight_tons * 1000.0, coil_length_mm=new_length
                )
                st.success("Batch updated successfully!")
                st.cache_data.clear()
                st.rerun()

    with tabs[2]:
        st.markdown("#### 🗑 Delete or Move Batch")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            if st.button("🚀 Move to R&D", key=f"modal_rnd_{selected_db_id}", use_container_width=True):
                promote_cloud_material(selected_db_id)
                st.success("Moved to R&D!")
                st.cache_data.clear()
                st.rerun()
        with col_m2:
            if st.button("🗑 Delete Batch", key=f"modal_del_{selected_db_id}", use_container_width=True):
                delete_cloud_material(selected_db_id)
                st.success("Batch deleted!")
                st.cache_data.clear()
                st.rerun()