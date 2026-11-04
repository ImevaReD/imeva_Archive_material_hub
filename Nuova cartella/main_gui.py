import sys
import os
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st
from supabase import create_client
import json
from sqlalchemy import text
import plotly.subplots as sp
import plotly.graph_objects as go
from openpyxl import load_workbook
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from materialconvert_functions_5 import convert_curve, average_curves
from area_crush_deck import area_crush_deck
import database 
from prod_hub_deck import prod_hub_deck 


###############################################################  PASSWORD ####################################################################
if "password_entered" not in st.session_state:
    st.session_state["password_entered"] = False

def check_password():
    def password_entered():
        if st.session_state["password_input"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password_input"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.image("imeva_logo.jpg", width=200)
        st.text_input("Enter password to access IMEVA Material Hub:", type="password", on_change=password_entered, key="password_input")
        return False
    elif not st.session_state["password_correct"]:
        st.image("imeva_logo.jpg", width=200)
        st.text_input("Enter password to access IMEVA Material Hub:", type="password", on_change=password_entered, key="password_input")
        st.error("Incorrect password")
        return False
    else:
        return True

if not check_password():
    st.stop()

#########################################################################################################################################################################

################################################################### PAGE CONFIGURATION & DATABASE SETUP #################################################################
st.set_page_config(page_title="IMEVA Materials Hub", layout="wide")

@st.cache_resource
def init_supabase_client():
    url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase_client = init_supabase_client()

st.cache_data.clear()
try:
    conn = st.connection("supabase", type="sql")
except Exception as e:
    st.error(f"IMEVA MATERIAL HUB ERROR: Failed to connect to Supabase Cloud Database: {e}")
    st.stop()

#########################################################################################################################################################################
##################################################################### REGULATORY COMPLIANCE HELPER FUNCTIONS ############################################################

def get_en10025_limits(grade, thickness):
    """Evaluates standard EN 10025 structural limits based on grade and thickness."""
    clean_grade = str(grade).upper().replace("JR", "").strip()
    if "S355" in clean_grade:
        y = 355 if thickness <= 16.0 else 345
        uts_min, uts_max = 470, 630
    elif "S275" in clean_grade:
        y = 275 if thickness <= 16.0 else 265
        uts_min, uts_max = 410, 560
    elif "S500" in clean_grade:
        y = 500 if thickness <= 16.0 else 490
        uts_min, uts_max = 590, 720
    else:
        y = 235 if thickness <= 16.0 else 225
        uts_min, uts_max = 360, 510
    return {"min_yield": y, "min_uts": uts_min, "max_uts": uts_max}


def determine_status(row):
    """Determines the display status of a batch, prioritizing formal lab verification."""
    q_status = row.get("quality_status")
    if q_status and pd.notna(q_status) and str(q_status).strip() != "":
        return str(q_status)
    elif row.get("rd_remaining_weight_kg") is not None and float(row.get("rd_remaining_weight_kg", 0)) <= 0.001:
        return "🔴 Not Available"
    elif row.get("is_promoted") == 1:
        return "🔬 R&D"
    else:
        return "🟢 Standby"
    
#### upload chemical composition.. from LAB  

def parse_metal_power_excel(uploaded_file):
    """Parses Metal Power Spectrometer Excel reports, drops Date/Time, and cleans symbol values."""
    try:
        df_raw = pd.read_excel(uploaded_file, header=None, engine="calamine")
        
        header_row_idx = None
        for idx, row in df_raw.iterrows():
            row_str_vals = [str(val).strip() for val in row.values if val is not None]
            if "Elements" in row_str_vals or any("C (%)" in val for val in row_str_vals):
                header_row_idx = idx
                break
        
        if header_row_idx is not None:
            df_raw.columns = [str(col).strip() for col in df_raw.iloc[header_row_idx].values]
            df = df_raw.iloc[header_row_idx + 1:].reset_index(drop=True)
        else:
            df = df_raw
            df.columns = [str(col).strip() for col in df.iloc[0].values]
            df = df.iloc[1:].reset_index(drop=True)
            
        df.columns = [str(col).strip() for col in df.columns]
        
        # Drop Date/Time column if it exists
        if "Date/Time" in df.columns:
            df = df.drop(columns=["Date/Time"])
            
        mean_row = df[df.iloc[:, 0].astype(str).str.contains("Mean", case=False, na=False)]
        if not mean_row.empty:
            target_data = mean_row.iloc[0]
        else:
            target_data = df.iloc[-1]
            
        composition = {}
        for col in df.columns:
            if "(%)" in col:
                val_str = str(target_data[col]).strip()
                # Remove '<', '>', '-', and extra whitespace
                clean_val = val_str.replace("<", "").replace(">", "").replace("-", "").strip()
                try:
                    composition[col] = float(clean_val)
                except ValueError:
                    composition[col] = 0.0  
                    
        return df, composition
    except Exception as e:
        st.error(f"Error parsing spectrometer report: {e}")
        return None, None
###############################################################################################################################################################################################################

################################################################# APPLICATION NAVIGATION  ####################################################################
if "current_page" not in st.session_state:
    st.session_state.current_page = "HOME"
if "selected_material" not in st.session_state:
    st.session_state.selected_material = None
if "active_family" not in st.session_state:
    st.session_state.active_family = "METALS"
if "experimental_curves" not in st.session_state:
    st.session_state.experimental_curves = {}
if "chemical_reports" not in st.session_state:
    st.session_state.chemical_reports = {}
################################################################################################################################################################
if "curves_loaded_from_cloud" not in st.session_state:
    try:
        df_cloud_curves = conn.query("SELECT id, experimental_curves_json FROM materials WHERE experimental_curves_json IS NOT NULL;", ttl=0)
        
        for _, row in df_cloud_curves.iterrows():
            m_id = row.get("id")
            mech_json = row.get("experimental_curves_json")
            
            if isinstance(mech_json, str):
                import json
                try:
                    mech_json = json.loads(mech_json)
                except Exception:
                    mech_json = {}
                    
            if mech_json and isinstance(mech_json, dict):
                st.session_state.experimental_curves[int(m_id)] = {
                    "strain": np.array(mech_json.get("strain", [])),
                    "stress_MPa": np.array(mech_json.get("stress_MPa", [])),
                    "sigy_MPa": mech_json.get("sigy_MPa"),
                    "uts_MPa": mech_json.get("uts_MPa"),
                    "E_GPa": mech_json.get("E_GPa"),
                    "elongation_pct": mech_json.get("elongation_pct"),
                    "avg_strain": np.array(mech_json.get("avg_strain", [])),
                    "avg_stress": np.array(mech_json.get("avg_stress", [])),
                    "eng_strain_clean": np.array(mech_json.get("eng_strain_clean", [])),
                    "eng_stress_clean": np.array(mech_json.get("eng_stress_clean", [])),
                    "deck": mech_json.get("deck")
                }
        st.session_state.curves_loaded_from_cloud = True
    except Exception as e:
        pass

############################################################################ HOME PAGE  ###########################################################################

if st.session_state.current_page == "HOME":
    logo_left, logo_mid, logo_right = st.columns([3, 1, 3])
    with logo_mid:
        if os.path.exists("imeva_logo.jpg"):
            st.image("imeva_logo.jpg", width=200)
        elif os.path.exists("imeva.jpg"):
            st.image("imeva.jpg", width=200)
            
    st.markdown("<h1 style='text-align: center; color: #28A745;'>IMEVA Materials Hub</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; font-weight: 600;'>Select a material family and choose either the R&D Deck or Production Hub.</p>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #666; font-style: italic; margin-top: -10px;'>Azienda leader nella produzione di barriere stradali — Benvenuti</p>", unsafe_allow_html=True)
    st.write("---")

    # --- BANNER IMAGE (Centered and Compact) ---
    if os.path.exists("road_2.png"):
        img_left_pad, img_center, img_right_pad = st.columns([1.5, 2, 1.5])
        with img_center:
            st.image("road_2.png", use_container_width=True, caption="IMEVA Road Safety Barrier Installation")

    st.write("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("### Metals Family")
        st.caption("Structural Steel, EN 10025 compliance, tensile strain curves, and batch tracking.")
        box_rd, box_prod = st.columns(2)
        with box_rd:
            st.info("R&D Deck")
            if st.button("Open R&D", key="metals_rd_btn", use_container_width=True):
                st.session_state.active_family = "METALS"
                st.session_state.current_page = "RD_DECK"
                st.rerun()
        with box_prod:
            st.success("Production Hub")
            if st.button("Open Prod", key="metals_prod_btn", use_container_width=True):
                st.session_state.active_family = "METALS"
                st.session_state.current_page = "PROD_HUB"
                st.rerun()
        
        st.markdown("<div style='margin-top: 6px;'></div>", unsafe_allow_html=True)
        if st.button("💥 Open Area Crush", key="metals_crash_btn", use_container_width=True):
            st.session_state.active_family = "METALS"
            st.session_state.current_page = "area_crush"
            st.rerun()

    with col2:
        st.markdown("### Polymers Family")
        st.caption("Viscoelastic/viscoplastic material models & extrusion quality tracking.")
        box_rd, box_prod = st.columns(2)
        with box_rd:
            st.info("R&D Deck")
            st.button("Locked", key="poly_rd_btn", disabled=True, use_container_width=True)
        with box_prod:
            st.success("Production Hub")
            st.button("Locked", key="poly_prod_btn", disabled=True, use_container_width=True)

    with col3:
        st.markdown("### Composites Family")
        st.caption("Orthotropic failure criteria, layups, & coupon production tracking.")
        box_rd, box_prod = st.columns(2)
        with box_rd:
            st.info("R&D Deck")
            st.button("Locked", key="comp_rd_btn", disabled=True, use_container_width=True)
        with box_prod:
            st.success("Production Hub")
            st.button("Locked", key="comp_prod_btn", disabled=True, use_container_width=True)

##############################################################################################################################################################################
############################################################################### R&D LABORATORY DECK PAGE #####################################################################
elif st.session_state.current_page == "RD_DECK":
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()
        
    family_name = st.session_state.active_family.title()
    st.title(f"{family_name} R&D Laboratory Deck")
    st.write("---")
    
    # 1. FETCH & FILTER DATA IDENTICALLY TO PRODUCTION HUB TO MATCH SEQ_IDs EXACTLY
    sq_rd_df = fetch_cloud_materials(family=st.session_state.active_family)
    
    if not sq_rd_df.empty:
        sq_rd_df['calc_weight_kg'] = sq_rd_df['rd_remaining_weight_kg'].fillna(sq_rd_df['coil_weight_kg'])
        sq_rd_df['calc_length_mm'] = sq_rd_df['rd_remaining_length_mm'].fillna(sq_rd_df['coil_length_mm'])
        sq_rd_df = sq_rd_df[sq_rd_df['calc_weight_kg'] > 0.001]

    combined_materials = {}
    if not sq_rd_df.empty:
        # Match Production Hub's exact sorting and indexing sequence after weight filtering
        rd_storage_df = sq_rd_df.sort_values(by="id").reset_index(drop=True).copy()
        rd_storage_df['seq_id'] = rd_storage_df.index + 1
        
        # Filter out parent lots that have been split into children (Identical to PROD_HUB)
        split_fathers = rd_storage_df[
            rd_storage_df['lotto_figlio'].notna() & 
            (rd_storage_df['lotto_figlio'].astype(str).str.strip() != '') &
            (rd_storage_df['lotto_figlio'].astype(str).str.strip() != 'None')
        ]['lotto_number'].dropna().astype(str).unique()
        
        rd_storage_df = rd_storage_df[
            ~(
                rd_storage_df['lotto_number'].astype(str).isin(split_fathers) & 
                (rd_storage_df['lotto_figlio'].isna() | (rd_storage_df['lotto_figlio'].astype(str).str.strip() == '') | (rd_storage_df['lotto_figlio'].astype(str).str.strip() == 'None'))
            )
        ]
        #rd_storage_df = rd_storage_df[~rd_storage_df['lotto_number'].astype(str).isin(split_fathers)]
        
        for _, row in rd_storage_df.iterrows():
            mat_id = int(row['id'])
            seq_id = int(row['seq_id'])
            
            # --- [BLOCK 2] AUTO-RESTORE MECHANICAL DATA FROM SUPABASE IF NOT IN SESSION ---
            if mat_id not in st.session_state.experimental_curves and pd.notna(row.get('experimental_curves_json')):
                try:
                    raw_mech = row['experimental_curves_json']
                    mech_data = json.loads(raw_mech) if isinstance(raw_mech, str) else raw_mech
                    st.session_state.experimental_curves[mat_id] = {
                        "strain": np.array(mech_data.get("strain", [])),
                        "stress_MPa": np.array(mech_data.get("stress_MPa", [])),
                        "sigy_MPa": mech_data.get("sigy_MPa", 0.0),
                        "uts_MPa": mech_data.get("uts_MPa", 0.0),
                        "E_GPa": mech_data.get("E_GPa", 0.0),
                        "elongation_pct": mech_data.get("elongation_pct", 0.0),
                        "avg_strain": np.array(mech_data.get("avg_strain", [])),
                        "avg_stress": np.array(mech_data.get("avg_stress", [])),
                        "eng_strain_clean": np.array(mech_data.get("eng_strain_clean", [])),
                        "eng_stress_clean": np.array(mech_data.get("eng_stress_clean", [])),
                        "deck": mech_data.get("deck", "")
                    }
                except Exception:
                    pass
            # --------------------------------------------------------------------

            grade_str = str(row['grade'])
            thick_val = float(row['thickness'])
            lotto_num = row.get('lotto_number')
            lotto_fig = row.get('lotto_figlio')
            
            if pd.notna(lotto_num) and pd.notna(lotto_fig) and str(lotto_fig) != "None" and str(lotto_fig) != "":
                lotto_str = f"{lotto_num} → {lotto_fig}"
            elif pd.notna(lotto_fig) and str(lotto_fig) != "None" and str(lotto_fig) != "":
                lotto_str = str(lotto_fig)
            elif pd.notna(lotto_num) and str(lotto_num) != "None" and str(lotto_num) != "":
                lotto_str = str(lotto_num)
            else:
                lotto_str = "N/A"

            cert_sy = float(row['yield_mpa'] or 0.0)
            cert_sf = float(row['uts_mpa'] or 0.0)
            
            c_weight = float(row.get('coil_weight_kg') or 0.0)
            c_length = float(row.get('coil_length_mm') or 0.0)
            rd_rem_weight = float(row.get('rd_remaining_weight_kg') if pd.notna(row.get('rd_remaining_weight_kg')) else c_weight)
            rd_rem_length = float(row.get('rd_remaining_length_mm') if pd.notna(row.get('rd_remaining_length_mm')) else c_length)
            rd_notes_val = str(row.get('rd_notes') or "")
            
            is_promoted = (row.get('is_promoted', 0) == 1)
            db_status = str(row.get('quality_status') or "").strip()
            
            if not db_status or db_status == "None":
                q_status = "🔬 R&D" if is_promoted else "🟢 Standby"
            else:
                q_status = db_status
            
            lab_data = st.session_state.experimental_curves.get(mat_id, {})
            lab_sy = f"{lab_data.get('sigy_MPa', 0.0):.0f}" if lab_data else "-"
            lab_sf = f"{lab_data.get('uts_MPa', 0.0):.0f}" if lab_data else "-"

            # Identical ID presentation matching the Production Hub cards (ID #{seq_id})
            display_label = (
                f"{grade_str}-SP{thick_val:.1f}-{lotto_str} (ID #{seq_id}) "
                f"[Cert: {cert_sy:.0f}, {cert_sf:.0f} | Lab: {lab_sy}, {lab_sf}]"
            )
            
            combined_materials[mat_id] = {
                "id": mat_id,
                "seq_id": seq_id,
                "display_name": display_label,
                "grade": grade_str,
                "thickness": thick_val,
                "cert_yield_MPa": cert_sy,
                "cert_uts_MPa": cert_sf,
                "lotto": lotto_str,
                "coil_weight_kg": c_weight,
                "coil_length_mm": c_length,
                "rd_remaining_weight_kg": rd_rem_weight,
                "rd_remaining_length_mm": rd_rem_length,
                "rd_notes": rd_notes_val,
                "quality_status": q_status,
                "is_promoted": is_promoted,
                "datasheet": row['datasheet'] if pd.notna(row['datasheet']) else None
            }

    # ==========================================
    # PROFESSIONAL STACKED FILTER HUB
    # ==========================================
    st.sidebar.markdown("Property Filters")
    
    # Status filter cleaned to exclude rejected/non-compliant options
    filter_status = st.sidebar.selectbox(
        "Filter by Status", 
        ["All", "Standby", " R&D", "Verified "],
        index=0
    )

    st.sidebar.write("---")

    # Steel Class / Grade
    available_grades = sorted(list(set(info["grade"] for info in combined_materials.values()))) if combined_materials else ["All"]
    filter_grade = st.sidebar.selectbox("Steel Class / Grade", ["All"] + available_grades)

    st.sidebar.write("---")

    # Thickness Filter
    st.sidebar.markdown("Thickness")
    enable_thick_filter = st.sidebar.checkbox("Filter by Exact Thickness", value=False)
    
    available_thicknesses = sorted(list(set(meta.get("thickness", 3.0) for meta in combined_materials.values()))) if combined_materials else [3.0]
    
    selected_thickness = st.sidebar.number_input(
        "Nominal Thickness [mm]", 
        min_value=1.0, 
        max_value=20.0, 
        value=float(available_thicknesses[0]) if available_thicknesses else 3.0, 
        step=0.1, 
        format="%.1f"
    ) if enable_thick_filter else None

    st.sidebar.write("---")

    with st.sidebar.expander("Supplier Cert. or/& Laboratory", expanded=True):
        cert_status_filter = st.radio( 
            "Select Data mode",
            ["All Batches", "Supplier Cert.", "Laboratory"]
        )

    with st.sidebar.expander("Mech. prop", expanded=True):
        min_yield, max_yield = st.slider(
            "Yield Strength Range (σ_yield) [MPa]", 
            min_value=100.0, max_value=1000.0, value=(100.0, 1000.0), step=10.0
        )
        min_uts, max_uts = st.slider(
            "Tensile Strength Range (σ_uts) [MPa]", 
            min_value=100.0, max_value=1200.0, value=(100.0, 1200.0), step=10.0
        )
        min_elong, max_elong = st.slider(
            "Elongation Range [%]",
            min_value=0.0, max_value=50.0, value=(0.0, 50.0), step=0.5
        )

    # ==========================================
    # FILTERING EXECUTION LOGIC
    # ==========================================
    filtered_materials = {}
    for mat_id, metadata in combined_materials.items():
        grade_match = (filter_grade == "All" or metadata.get("grade") == filter_grade)
        
        raw_st = str(metadata.get("quality_status", ""))
        is_prom = metadata.get("is_promoted", False)
        
        if filter_status == "All":
            status_match = True
        elif filter_status == "🟢 Standby":
            status_match = ("Standby" in raw_st) or (not is_prom)
        elif filter_status == "🔬 R&D":
            status_match = ("R&D" in raw_st) or is_prom
        elif filter_status == "Verified ✅":
            status_match = ("Verified" in raw_st) or ("✅" in raw_st)
        else:
            status_match = True

        thick_val = metadata.get("thickness", 0)
        thickness_match = True if not enable_thick_filter else (abs(thick_val - selected_thickness) <= 0.05)
        
        if cert_status_filter == "Supplier Cert.":
            active_yield = metadata.get("cert_yield_MPa", 0)
            active_uts = metadata.get("cert_uts_MPa", 0)
            active_elong = metadata.get("elongation_pct", 0)
            cert_match = True
        elif cert_status_filter == "Laboratory":
            session_data = st.session_state.experimental_curves.get(mat_id, {})
            active_yield = session_data.get("sigy_MPa", metadata.get("sigy_MPa", 0))
            active_uts = session_data.get("uts_MPa", metadata.get("uts_MPa", 0))
            active_elong = session_data.get("elongation_pct", session_data.get("elongation", metadata.get("elongation_pct", 0)))
            
            has_metadata_lab = metadata.get("sigy_MPa") is not None
            has_session_lab = mat_id in st.session_state.experimental_curves
            cert_match = bool(has_metadata_lab or has_session_lab)
        else: 
            active_yield = metadata.get("sigy_MPa", metadata.get("cert_yield_MPa", 0))
            active_uts = metadata.get("uts_MPa", metadata.get("cert_uts_MPa", 0))
            active_elong = metadata.get("elongation_pct", 0)
            cert_match = True

        yield_match = min_yield <= active_yield <= max_yield
        uts_match = min_uts <= active_uts <= max_uts
        elong_match = min_elong <= active_elong <= max_elong

        if grade_match and status_match and thickness_match and yield_match and uts_match and elong_match and cert_match:
            filtered_materials[mat_id] = metadata

    main_col, chart_col = st.columns([1, 1])

    # 3. Apply the loop with clean, unique material IDs
    with main_col:
        st.subheader("Available Inventory & Promoted Batches")
        st.caption("Format: `[Cert: YS, UTS | Lab: YS, UTS]`")
        if not combined_materials:
            st.info("No materials found in the database yet.")
        elif not filtered_materials:
            st.warning("No materials match the current sidebar filter limits.")
        else:
            for mat_id, info in filtered_materials.items():
                limits = get_en10025_limits(info["grade"], info["thickness"])
                is_compliant = info["cert_yield_MPa"] >= limits["min_yield"]
                
                rem_len = info["rd_remaining_length_mm"]
                rem_weight = info["rd_remaining_weight_kg"]
                inv_badge = "🟢 Available" if rem_len > 10.0 else "🔴 Low Stock"
                status_badge = "✅ EN Compliant" if is_compliant else "❌ Non-Compliant"
                st.markdown(f"**Status:** {status_badge} | {inv_badge} | **Stock:** {rem_weight/1000.0:.1f} TON / {rem_len:.1f} mm")
                
                card_col1, card_col2 = st.columns([3, 1])
                with card_col1:
                    title_col, prev_col = st.columns([4, 1])
                    with title_col:
                        st.markdown(f"#### `{info['display_name']}`")
                    with prev_col:
                        cert_url = info.get("datasheet")
                        if cert_url:
                            if st.button("🔍 View Cert", key=f"btn_cert_{mat_id}"):
                                show_cert_modal(info['display_name'], cert_url, info)
                        else:
                            st.markdown("*(No Cert)*")
                            
                with card_col2:
                    # Un-promote button shown strictly for promoted inventory items
                    if info.get("is_promoted"):
                        if st.button("↩️ Un-promote", key=f"demote_rd_{mat_id}", help="Send back to Production Hub"):
                            demote_cloud_material(mat_id)
                            st.toast("Material un-promoted and sent back to Production!")
                            st.rerun()
                    else:
                        st.markdown("*(In Production)*")

                if st.button(f"Analyze & Process Data", key=f"btn_{mat_id}", use_container_width=True):
                    st.session_state.selected_material = mat_id 
                
                st.divider()

    # 4. Maintain the Charts Column 
    with chart_col:
        st.subheader("Inventory Properties Group Overview")
        if filtered_materials:
            batches = [info["display_name"] for info in filtered_materials.values()]
            yields = [info["cert_yield_MPa"] for info in filtered_materials.values()]
            utss = [info["cert_uts_MPa"] for info in filtered_materials.values()]

            x = np.arange(len(batches))
            width = 0.35

            fig_bar, ax_bar = plt.subplots(figsize=(6, 3.8))
            rects1 = ax_bar.bar(x - width/2, yields, width, label='σ_yield (MPa)', color='#1E3A8A')
            rects2 = ax_bar.bar(x + width/2, utss, width, label='σ_uts / failure (MPa)', color='#D97706')

            ax_bar.set_ylabel('Stress [MPa]')
            ax_bar.set_title('Strength Properties Comparison')
            ax_bar.set_xticks(x)
            ax_bar.set_xticklabels(batches, rotation=15, ha='right', fontsize=8)
            ax_bar.legend()
            ax_bar.grid(axis='y', linestyle=':', alpha=0.7)
            
            ax_bar.bar_label(rects1, padding=3, fmt='%.0f')
            ax_bar.bar_label(rects2, padding=3, fmt='%.0f')

            fig_bar.tight_layout()
            st.pyplot(fig_bar)
            
            st.subheader("📈 Multi-Material Comparative Curves")
            fig_comp, ax_comp = plt.subplots(figsize=(6, 3.8))
            curves_plotted = 0

            for mat_id, info in filtered_materials.items():
                if mat_id in st.session_state.experimental_curves:
                    exp = st.session_state.experimental_curves[mat_id]
                    lot_name = info.get('lotto') or 'N/A'
                    ax_comp.plot(exp["strain"], exp["stress_MPa"], label=f"ID #{info['seq_id']} - Lot: {lot_name}", lw=2)
                    curves_plotted += 1

            if curves_plotted > 0:
                ax_comp.set_xlabel("Strain [-]")
                ax_comp.set_ylabel("Stress [MPa]")
                ax_comp.set_title("Experimental Stress-Strain Overlay")
                ax_comp.grid(True, linestyle=":")
                ax_comp.legend()
                fig_comp.tight_layout()
                st.pyplot(fig_comp)
            else:
                st.info("Click 'Analyze & Process Data' below to upload CSV dog bone tests for curve plotting.")
        else:
            st.caption("Awaiting inventory entries to generate strength comparison bars.")

    # ---------------------------------------------------------------------
    # ### SUB-SECTION: DETAILED REVIEW & EXPERIMENTAL MULTI-CSV ANALYSIS ###
    # ---------------------------------------------------------------------
    selected_mat_id = st.session_state.get("selected_material")
    if selected_mat_id and selected_mat_id in combined_materials:
        mat_info = combined_materials[selected_mat_id]
        mat_id = mat_info["id"]
        
        st.write("---")
        st.header(f"📊 Detailed Review Deck: {mat_info['display_name']}")
        
        props_col, action_col = st.columns([1, 1])
        
        with props_col:
            st.subheader("📜 Material Mill Certificate Parameters")
            st.write(f"**Structural Grade:** {mat_info['grade']}")
            st.write(f"**Nominal Thickness:** {mat_info['thickness']} mm")
            st.write(f"**Lotto :** {mat_info.get('lotto') or 'N/A'}")
            st.write(f"**Yield Stress ($R_e$):** {mat_info['cert_yield_MPa']} MPa")
            st.write(f"**Tensile Stress ($R_m$):** {mat_info['cert_uts_MPa']} MPa")

            st.markdown("---")
            st.subheader("Log Material Usage")
            
            init_w = float(mat_info.get("coil_weight_kg") or 0.0)
            init_l = float(mat_info.get("coil_length_mm") or 0.0)
            
            raw_rem_w = mat_info.get("rd_remaining_weight_kg")
            raw_rem_l = mat_info.get("rd_remaining_length_mm")
            
            curr_rem_w = float(raw_rem_w) if raw_rem_w is not None else init_w
            curr_rem_l = float(raw_rem_l) if raw_rem_l is not None else init_l

            if curr_rem_w <= 0.001:
                curr_rem_w = 0.0
            if curr_rem_l <= 0.01:
                curr_rem_l = 0.0

            rd_notes_val = mat_info.get("rd_notes")

            st.markdown(f"""
            **Remaining Stock:** **{curr_rem_w/ 1000.0:.3f} TON** / **{curr_rem_l:.3f} mm**  
            *(Original Baseline: {init_w / 1000.0:.3f} TON / {init_l:.3f} mm)*  
            📝**Notes / Log History:** {rd_notes_val if rd_notes_val else "No cuts logged yet."}
            """)

            with st.form(key=f"rd_tracker_form_{mat_id}"):
                col_w1, col_w2 = st.columns(2)
                with col_w1:
                    weight_consumed_ton = st.number_input("Weight Used [TON]", value=0.0, step=0.1, format="%.3f")
                with col_w2:
                    length_consumed = st.number_input("Length Used [mm]", value=0.0, step=100.0)
                
                usage_notes = st.text_input("Usage Purpose / Notes", value="")
                
                if st.form_submit_button("📉 Deduct & Update Inventory", use_container_width=True):
                    try:
                        log_material_consumption(
                            material_id=mat_id,
                            weight_used_kg=weight_consumed_ton * 1000.0,
                            length_used_mm=length_consumed,
                            notes=usage_notes
                        )
                        st.success("Material usage logged and inventory deducted successfully!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to record consumption: {e}")

            st.markdown("---")
            st.subheader("🔬 Chemical Composition Analysis")
            uploaded_spec = st.file_uploader("Upload Spectrometer Report (Excel/CSV)", type=["xlsx", "csv"], key=f"spec_{mat_id}")
        
        if uploaded_spec is not None:
            df_spec, comp_dict = parse_metal_power_excel(uploaded_spec)
            if comp_dict is not None:
                st.session_state.chemical_reports[mat_id] = {"df": df_spec, "comp": comp_dict}
                
                temp_numeric = {
                    k.replace(" (%)", ""): v for k, v in comp_dict.items() 
                    if isinstance(v, (int, float)) and "Fe" not in k
                }
                
                try:
                    c_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "C"), 0.0)
                    mn_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MN"), 0.0)
                    cr_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CR"), 0.0)
                    mo_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MO"), 0.0)
                    v_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "V"), 0.0)
                    ni_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "NI"), 0.0)
                    cu_v = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CU"), 0.0)
                    calc_cev = float(c_v) + float(mn_v)/6.0 + (float(cr_v)+float(mo_v)+float(v_v))/5.0 + (float(ni_v)+float(cu_v))/15.0
                except Exception:
                    calc_cev = 0.0

                try:
                    df_json_records = df_spec.to_dict(orient="records") if df_spec is not None else []
                    spec_payload = json.dumps({"df_records": df_json_records, "comp": comp_dict})
                    
                    c_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "C"), 0.0)
                    si_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "SI"), 0.0)
                    mn_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MN"), 0.0)
                    p_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "P"), 0.0)
                    s_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "S"), 0.0)
                    cr_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CR"), 0.0)
                    mo_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MO"), 0.0)
                    ni_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "NI"), 0.0)
                    cu_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CU"), 0.0)
                    al_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "AL"), 0.0)
                    as_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "AS"), 0.0)
                    nb_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "NB"), 0.0)
                    sn_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "SN"), 0.0)
                    ti_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "TI"), 0.0)
                    w_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "W"), 0.0)
                    b_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "B"), 0.0)
                    co_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CO"), 0.0)
                    pb_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "PB"), 0.0)
                    v_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "V"), 0.0)
                    zn_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "ZN"), 0.0)
                    mg_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "MG"), 0.0)
                    ce_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "CE"), 0.0)
                    la_val_db = next((v for k, v in temp_numeric.items() if k.strip().upper() == "LA"), 0.0)

                    with conn.session as s:
                        s.execute(
                            text("""
                                UPDATE materials SET 
                                    carbon_equivalent_cev = :cev,
                                    c_pct = :c, si_pct = :si, mn_pct = :mn, p_pct = :p, s_pct = :s,
                                    cr_pct = :cr, mo_pct = :mo, ni_pct = :ni, cu_pct = :cu, al_pct = :al,
                                    as_pct = :as, nb_pct = :nb, sn_pct = :sn, ti_pct = :ti, w_pct = :w,
                                    b_pct = :b, co_pct = :co, pb_pct = :pb, v_pct = :v, zn_pct = :zn,
                                    mg_pct = :mg, ce_pct = :ce, la_pct = :la,
                                    spectrometer_data_json = :spec_json
                                WHERE id = :id
                            """),
                            params={
                                "cev": float(calc_cev),
                                "c": float(c_val_db), "si": float(si_val_db), "mn": float(mn_val_db),
                                "p": float(p_val_db), "s": float(s_val_db), "cr": float(cr_val_db),
                                "mo": float(mo_val_db), "ni": float(ni_val_db), "cu": float(cu_val_db),
                                "al": float(al_val_db), "as": float(as_val_db), "nb": float(nb_val_db),
                                "sn": float(sn_val_db), "ti": float(ti_val_db), "w": float(w_val_db),
                                "b": float(b_val_db), "co": float(co_val_db), "pb": float(pb_val_db),
                                "v": float(v_val_db), "zn": float(zn_val_db), "mg": float(mg_val_db),
                                "ce": float(ce_val_db), "la": float(la_val_db),
                                "spec_json": spec_payload,
                                "id": int(mat_id)
                            }
                        )
                        s.commit()
                    st.toast("Spectrometer analysis saved to cloud via SQL!", icon="☁️")
                except Exception as db_err:
                    st.warning(f"Cloud sync error: {db_err}")
        
        if mat_id in st.session_state.chemical_reports:
            saved_spec = st.session_state.chemical_reports[mat_id]
            st.success("Spectrometer Data Loaded Successfully!")
            st.dataframe(saved_spec["df"], use_container_width=True)
            
            numeric_comp = {
                k.replace(" (%)", ""): v for k, v in saved_spec["comp"].items() 
                if isinstance(v, (int, float)) and "Fe" not in k
            }
            
            if numeric_comp:
                st.markdown("#### 📊 Alloy Elements Distribution (Excluding Base Iron)")
                fig_chem, ax_chem = plt.subplots(figsize=(6, 3))
                ax_chem.bar(list(numeric_comp.keys()), list(numeric_comp.values()), color="#28A745")
                ax_chem.set_ylabel("Concentration [%]")
                ax_chem.grid(axis="y", linestyle=":", alpha=0.7)
                plt.xticks(rotation=45, ha="right")
                fig_chem.tight_layout()
                st.pyplot(fig_chem)

        with action_col:
            st.subheader("Dog Bone Tensile Test CSV Processing")
            st.caption("Upload multiple CSV files corresponding to dog bone specimens for this material.")
            
            uploaded_dogbones = st.file_uploader(
                "Upload Dog Bone CSV Files", 
                type=["csv"], 
                accept_multiple_files=True,
                key=f"uploader_{mat_id}"
            )

            if st.button("Process & Save Dog Bone Data to Cloud", key=f"process_btn_{mat_id}"):
                if not uploaded_dogbones:
                    st.warning("Please upload at least one dog bone CSV file before processing.")
                else:
                    runs_data = []
                    os.makedirs("temp_uploads", exist_ok=True)
                    for uploaded_file in uploaded_dogbones:
                        temp_path = os.path.join("temp_uploads", uploaded_file.name)
                        with open(temp_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        runs_data.append(convert_curve(temp_path))
                    
                    results_computed = average_curves(runs_data)
                    curve_payload_dict = {
                        "strain": results_computed["eng_strain_clean"].tolist() if hasattr(results_computed["eng_strain_clean"], "tolist") else list(results_computed["eng_strain_clean"]),
                        "stress_MPa": (results_computed["eng_stress_clean"] * 1000.0).tolist() if hasattr(results_computed["eng_stress_clean"], "tolist") else list(results_computed["eng_stress_clean"] * 1000.0),
                        "sigy_MPa": float(results_computed["sigy_GPa"] * 1000.0),
                        "uts_MPa": float(results_computed["uts_GPa"] * 1000.0),
                        "E_GPa": float(results_computed["E_GPa"]),
                        "elongation_pct": float(results_computed["elongation_pct"]),
                        "avg_strain": results_computed["avg_strain"].tolist() if hasattr(results_computed["avg_strain"], "tolist") else list(results_computed["avg_strain"]),
                        "avg_stress": results_computed["avg_stress"].tolist() if hasattr(results_computed["avg_stress"], "tolist") else list(results_computed["avg_stress"]),
                        "eng_strain_clean": results_computed["eng_strain_clean"].tolist() if hasattr(results_computed["eng_strain_clean"], "tolist") else list(results_computed["eng_strain_clean"]),
                        "eng_stress_clean": results_computed["eng_stress_clean"].tolist() if hasattr(results_computed["eng_stress_clean"], "tolist") else list(results_computed["eng_stress_clean"]),
                        "deck": results_computed["deck"]
                    }
                    
                    st.session_state.experimental_curves[mat_id] = {
                        "strain": np.array(curve_payload_dict["strain"]),
                        "stress_MPa": np.array(curve_payload_dict["stress_MPa"]),
                        "sigy_MPa": curve_payload_dict["sigy_MPa"],
                        "uts_MPa": curve_payload_dict["uts_MPa"],
                        "E_GPa": curve_payload_dict["E_GPa"],
                        "elongation_pct": curve_payload_dict["elongation_pct"],
                        "avg_strain": np.array(curve_payload_dict["avg_strain"]),
                        "avg_stress": np.array(curve_payload_dict["avg_stress"]),
                        "eng_strain_clean": np.array(curve_payload_dict["eng_strain_clean"]),
                        "eng_stress_clean": np.array(curve_payload_dict["eng_stress_clean"]),
                        "deck": curve_payload_dict["deck"]
                    }
                    
                    try:
                        mech_json_payload = json.dumps(curve_payload_dict)
                        with conn.session as s:
                            s.execute(
                                text("""
                                    UPDATE materials SET 
                                        experimental_curves_json = :mech_json,
                                        calculated_sigy_mpa = :sigy,
                                        calculated_uts_mpa = :uts,
                                        calculated_e_gpa = :egpa,
                                        calculated_elongation_pct = :elong
                                    WHERE id = :id
                                """),
                                params={
                                    "mech_json": mech_json_payload,
                                    "sigy": float(results_computed["sigy_GPa"] * 1000.0),
                                    "uts": float(results_computed["uts_GPa"] * 1000.0),
                                    "egpa": float(results_computed["E_GPa"]),
                                    "elong": float(results_computed["elongation_pct"]),
                                    "id": int(mat_id)
                                }
                            )
                            s.commit()
                            st.cache_data.clear() 
                            st.toast("Dog bone analysis saved to cloud via SQL!", icon="☁️")
                            st.success(f"Successfully processed and saved {len(runs_data)} specimen run(s)!")
                            st.rerun() 
                    except Exception as db_err:
                        st.warning(f"Cloud sync error for mechanical data: {db_err}")

            if mat_id not in st.session_state.experimental_curves:
                saved_json = mat_info.get("experimental_curves_json") if "experimental_curves_json" in mat_info else None
                if saved_json:
                    loaded_dict = json.loads(saved_json) if isinstance(saved_json, str) else saved_json
                    st.session_state.experimental_curves[mat_id] = {
                        "strain": np.array(loaded_dict["strain"]),
                        "stress_MPa": np.array(loaded_dict["stress_MPa"]),
                        "sigy_MPa": loaded_dict["sigy_MPa"],
                        "uts_MPa": loaded_dict["uts_MPa"],
                        "E_GPa": loaded_dict["E_GPa"],
                        "elongation_pct": loaded_dict["elongation_pct"],
                        "avg_strain": np.array(loaded_dict["avg_strain"]),
                        "avg_stress": np.array(loaded_dict["avg_stress"]),
                        "eng_strain_clean": np.array(loaded_dict["eng_strain_clean"]),
                        "eng_stress_clean": np.array(loaded_dict["eng_stress_clean"]),
                        "deck": loaded_dict["deck"]
                    }

            results = st.session_state.experimental_curves.get(mat_id, None)

            if results:
                st.metric(label="Calculated Young's Modulus (E)", value=f"{results['E_GPa']:.2f} GPa")
                st.metric(label="Calculated Lab Yield Avg (sigy)", value=f"{results['sigy_MPa']:.1f} MPa")
                st.metric(label="Calculated Lab Ultimate Stress (UTS)", value=f"{results['uts_MPa']:.1f} MPa")
                st.metric(label="Calculated Elongation (A%)", value=f"{results.get('elongation_pct', 0.0):.2f} %")
            else:
                st.info("Upload tensile test CSV logs above and click process to calculate true strain/stress properties.")

            if results:
                st.write("---")
                plot_col, deck_col = st.columns([1, 1])
                
                with plot_col:
                    st.subheader("📈 Averaged Stress-Strain Trajectory")
                    fig, ax = plt.subplots(figsize=(6, 4.5))
                    
                    ax.plot(results["avg_strain"], results["avg_stress"] * 1000.0, label="Raw Engineering Average", color="navy", linestyle="--")
                    ax.plot(results["eng_strain_clean"], results["eng_stress_clean"] * 1000.0, label="Monotonic Clean Filter", color="orange", lw=2)
                    
                    ax.set_xlabel("Strain [-]")
                    ax.set_ylabel("Stress [MPa]")
                    ax.grid(True, linestyle=":")
                    ax.legend()
                    st.pyplot(fig)

                with deck_col:
                    st.subheader("💾 LS-DYNA Keyword Deck Export")
                    st.caption("MAT_024 card automatically generated from experimental curves.")
                    st.text_area("Keyword Output Preview", results["deck"], height=280)
                    
                    st.download_button(
                        label="📥 Download MAT_024 Keyword Deck (*.k)",
                        data=results["deck"],
                        file_name=f"MAT_024_{mat_info['grade']}_{int(mat_info['thickness']*100)}mm.k",
                        mime="text/plain"
                    )
##############################################################################################################################################################################

###################################################### PRODUCTION QUALITY CONTROL HUB PAGE ###################################################################################
elif st.session_state.current_page == "PROD_HUB":
    prod_hub_deck(conn)
##############################################################################################################################################################################

###################################################### AREA CRUSHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH #############################################################################
elif st.session_state.current_page == "area_crush":
    area_crush_deck(conn)

##############################################################################################################################################################################
