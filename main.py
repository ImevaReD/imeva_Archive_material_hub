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

#########################################################################################################################################################
##############################################  PASSWORD ################################################################################################

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

#########################################################################################################################################################

@st.dialog(" Manage & Split Coil Batch", width="large")
def open_coil_management_dialog(grade, thick, length, group_rows):
    st.write(f"### 📦 Selected Group: **{grade} | {thick} mm | W {length:.0f} mm**")   # [CLAUDE FIX W1] W = larghezza
    st.markdown("Select a specific coil/lotto from this group to manage or split:")
    
    sorted_full_df = prod_df.sort_values(by="id").reset_index(drop=True)
    
    coil_options = {}
    for _, row in group_rows.iterrows():
        db_id = int(row['id'])
        
        match_idx = sorted_full_df[sorted_full_df['id'] == db_id].index
        seq_id = int(match_idx[0] + 1) if not match_idx.empty else db_id
        
        father = row.get('lotto_number', '')
        son = row.get('lotto_figlio', '')
        lotto_str = f"{father} → {son}" if (father and son) else (son or father or "No Lotto")
        
        label = f"Queue ID #{seq_id} | Lotto: {lotto_str}"
        coil_options[label] = db_id

    selected_coil_label = st.selectbox("Choose Specific Coil", options=list(coil_options.keys()))
    selected_db_id = coil_options[selected_coil_label]
    
    # Get the specific row data
    selected_row = group_rows[group_rows['id'].astype(int) == int(selected_db_id)].iloc[0]
    
    st.write("---")
    tabs = st.tabs([" Split Coil", "✏️ Edit Values", "🗑 Delete / Move"])
    
    with tabs[0]:
        st.markdown(" Split Coil (Father -> Sons)")
        rem_w_val = selected_row.get("rd_remaining_weight_kg")
        current_w_kg = float(rem_w_val) if pd.notna(rem_w_val) else float(selected_row.get("coil_weight_kg", 0.0))
        current_w_tons = current_w_kg / 1000.0

        rem_l_val = selected_row.get("rd_remaining_length_mm")
        current_l = float(rem_l_val) if pd.notna(rem_l_val) else float(selected_row.get("coil_length_mm", 0.0))
        
        base_lotto = selected_row.get('lotto_figlio') if pd.notna(selected_row.get('lotto_figlio')) else selected_row.get('lotto_number', '')
        
        st.info(f"Available to split: **{current_w_tons:.3f} TON** / Width **{current_l:.2f} mm**")   # [CLAUDE FIX W1]
        
        num_children = st.number_input("Number of Components", min_value=2, max_value=10, value=2, step=1, key=f"modal_num_child_{selected_db_id}")
        
        with st.form(key=f"modal_split_form_{selected_db_id}"):
            children_inputs = []
            letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            weight_per_mm_kg = current_w_kg / current_l if current_l > 0 else 0.0   # [CLAUDE FIX W1] peso per mm di LARGHEZZA
            
            for i in range(int(num_children)):
                st.markdown(f"**Component {i+1} ({letters[i]})**")
                cc1, cc2, cc3 = st.columns(3)
                with cc1:
                    c_lotto = st.text_input(f"Component Code {i+1}", value=f"{base_lotto}-{letters[i]}", key=f"modal_split_lotto_{selected_db_id}_{i}")
                with cc2:
                    c_length = st.number_input(f"Width {i+1} [mm]", min_value=0.0, max_value=current_l, value=0.0, key=f"modal_split_l_{selected_db_id}_{i}")
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
                    st.error("Sum of child widths exceeds parent coil width!")   # [CLAUDE FIX W1]
                else:
                    split_cloud_material(selected_db_id, children_inputs, auto_promote_rd=True)
                    st.success("Coil successfully split and pushed to R&D!")
                    st.cache_data.clear()
                    st.rerun()

    with tabs[1]:
        st.markdown(" ✏️ Edit Selected Batch Values")
        with st.form(key=f"modal_edit_form_{selected_db_id}"):
            new_grade = st.text_input("Grade", value=str(selected_row.get("grade", "")))
            # [CLAUDE FIX B3] valori iniziali letti con _safe_num(): un campo vuoto (NULL) non viene più riscritto come NaN
            new_thickness = st.number_input("Thickness [mm]", value=_safe_num(selected_row.get("thickness")), step=0.1)
            new_yield = st.number_input("Yield Stress [MPa]", value=_safe_num(selected_row.get("yield_mpa")), step=5.0)
            new_uts = st.number_input("Failure Stress [MPa]", value=_safe_num(selected_row.get("uts_mpa")), step=5.0)
            new_elong = st.number_input("Elongation [%]", value=_safe_num(selected_row.get("elongation_pct")), step=0.5)

            db_weight_kg = _safe_num(selected_row.get("coil_weight_kg"))   # [CLAUDE FIX B3] valore sicuro (NULL -> 0)
            initial_weight_tons = db_weight_kg / 1000.0 if db_weight_kg else 0.0
            new_weight_tons = st.number_input("Coil Weight [TON]", value=initial_weight_tons, step=1.0, format="%.3f")
            # [CLAUDE FIX W1] coil_length_mm = LARGHEZZA coil: prima "Coil Length [mm]"
            new_length = st.number_input("Coil Width [mm]", value=_safe_num(selected_row.get("coil_length_mm")), step=10.0)
            
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
            if st.button(" Move to R&D", key=f"modal_rnd_{selected_db_id}", use_container_width=True):
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

#########################################################################################################################################################


#########################################################################################################################################################
##############################################  PAGE CONFIGURATION & DATABASE SETUP #####################################################################

st.set_page_config(page_title="IMEVA Materials Hub", layout="wide")

@st.cache_resource
def init_supabase_client():
    url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
    key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase_client = init_supabase_client()

# [CLAUDE FIX C1] -------------------------------------------------------------------------------------------------
# PROBLEMA: upload certificati -> errore 403 "Invalid Compact JWS".
# Causa: SUPABASE_KEY è una chiave del nuovo formato "sb_secret_..." (non è un JWT). La libreria supabase-py la invia
# anche nell'header "Authorization: Bearer ...", e lo Storage di Supabase prova a verificarla come JWT -> rifiuto.
# La documentazione Supabase indica di inviare le chiavi publishable/secret SOLO nell'header "apikey".
# SOLUZIONE: client Storage dedicato che invia la chiave solo nell'header "apikey" quando la chiave è "sb_...".
# Con le chiavi legacy (JWT "eyJ...") si continua a usare il client standard, come prima.
@st.cache_resource
def init_storage_client():
    url = st.secrets["connections"]["supabase"]["SUPABASE_URL"].rstrip("/")
    key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    if str(key).startswith("sb_"):
        from storage3 import SyncStorageClient
        return SyncStorageClient(f"{url}/storage/v1/", headers={"apikey": key})
    return supabase_client.storage

storage_client = init_storage_client()


def _safe_storage_name(name):
    """[CLAUDE FIX C1] Nome file sicuro per lo Storage: spazi e caratteri speciali sostituiti con '_'."""
    import re
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))
# [END CLAUDE FIX C1] ---------------------------------------------------------------------------------------------

st.cache_data.clear()
try:
    conn = st.connection("supabase", type="sql")
except Exception as e:
    st.error(f"IMEVA MATERIAL HUB ERROR: Failed to connect to Supabase Cloud Database: {e}")
    st.stop()

def upload_certificate_to_cloud(uploaded_file, lotto_identifier):
    """Uploads a certificate file to the Supabase 'certificate_storage' bucket and returns its public URL."""
    try:
        file_ext = uploaded_file.name.split('.')[-1]
        file_path = f"certs/{_safe_storage_name(lotto_identifier)}_{int(time.time())}.{file_ext}"   # [CLAUDE FIX C1] nome sicuro
        
        # Push bytes to Supabase Storage
        # [CLAUDE FIX C1] storage_client al posto di supabase_client.storage (vedi init_storage_client)
        storage_client.from_("certificate_storage").upload(
            path=file_path,
            file=uploaded_file.getvalue(),
            file_options={"content-type": uploaded_file.type, "upsert": "true"}
        )
        
        # Retrieve public URL
        public_url = storage_client.from_("certificate_storage").get_public_url(file_path)   # [CLAUDE FIX C1]
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
        st.markdown(f" [Open certificate in a new browser tab]({cert_url})", unsafe_allow_html=True)
        if cert_url.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            st.image(cert_url, caption="Certificate Document", use_container_width=True)
        else:
            st.info(" Inline PDF previews are often restricted by security policies. Use the link above if the viewer below appears empty.")
            st.markdown(f'<iframe src="{cert_url}" width="100%" height="600px"></iframe>', unsafe_allow_html=True)
    else:
        st.warning("No certificate URL provided.")


if "chemical_reports" not in st.session_state:
    st.session_state.chemical_reports = {}

if "reports_loaded_from_cloud" not in st.session_state:
    try:
        df_cloud_reports = conn.query("SELECT id, spectrometer_data_json FROM materials WHERE spectrometer_data_json IS NOT NULL;", ttl=0)
        
        for _, row in df_cloud_reports.iterrows():
            m_id = row.get("id")
            spec_json = row.get("spectrometer_data_json")
            
            if isinstance(spec_json, str):
                import json
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


# [CLAUDE FIX W1] -------------------------------------------------------------------------------------------------
# NOTA MODELLO DATI: le colonne DB "coil_length_mm" e "rd_remaining_length_mm" contengono la LARGHEZZA del coil [mm]
# (non la lunghezza). I nomi delle colonne NON sono stati cambiati per non richiedere una migrazione del database
# Supabase e non perdere i dati esistenti: sono cambiate solo le etichette dell'interfaccia e i commenti.
# Lo split (slitting) divide il coil in coil di larghezza minore: peso figlio = larghezza figlio x (peso / larghezza padre).
# [END CLAUDE FIX W1] ---------------------------------------------------------------------------------------------
def fetch_cloud_materials(hub_section="PROD", family='METALS'):
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

    # [CLAUDE FIX A1] -------------------------------------------------------------------------------------------
    # Nuova sorgente dati dedicata alla pagina R&D Deck.
    # Prima l'R&D Deck usava la query PROD (default) e la query "RD" qui sopra non veniva mai chiamata.
    # Requisito: nell'R&D Deck devono essere visibili sia i coil ancora in produzione (Standby / Verified)
    # sia quelli già passati in R&D (hub_section = 'RD' oppure is_promoted = 1).
    # L'esclusione dei NON CONFORMI e la suddivisione per stato vengono fatte dal filtro "Filter by Status".
    # L'area CRUSH resta esclusa.
    elif hub_section == "RD_DECK":
        query = """
            SELECT 
                id, parent_id, family, hub_section, grade, thickness, 
                yield_mpa, uts_mpa, elongation_pct, coil_weight_kg, coil_length_mm, 
                rd_remaining_weight_kg, rd_remaining_length_mm, rd_notes, 
                lotto_number, lotto_figlio, provider, 
                certificate_url AS datasheet, 
                is_promoted, codice, description, quantity, quality_status
            FROM materials 
            WHERE (hub_section IN ('PROD', 'RD') OR hub_section IS NULL OR is_promoted = 1) AND family = :fam 
            ORDER BY id DESC;
        """
        return conn.query(query, params={"fam": family}, ttl=0)
    # [END CLAUDE FIX A1] ---------------------------------------------------------------------------------------
        
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
    grade, thickness, yield_mpa, uts_mpa, elongation, lotto_padre, lotto_figlio, provider, 
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

def split_cloud_material(parent_id, children_list, auto_promote_rd=True):
    """Splits a parent coil into a dynamic number of child coils and zeros out the parent stock."""
    with conn.session as s:
        parent_res = s.execute(
            text("SELECT * FROM materials WHERE id = :id"), 
            {"id": int(parent_id)}
        ).mappings().first()
        
        if not parent_res:
            raise Exception("Parent material not found.")

        parent_lotto = parent_res['lotto_number'] if parent_res['lotto_number'] else parent_res['lotto_figlio']
        
        # Determine promotion status based on the flag
        promoted_val = 1 if auto_promote_rd else 0

        # Insert each child dynamically
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

def log_material_consumption(material_id, weight_used_kg, length_used_mm, notes=""):
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

def promote_cloud_material(material_id):
    """Marks batch as promoted while keeping it visible in Production Hub."""
    with conn.session as s:
        s.execute(text('UPDATE materials SET is_promoted = 1 WHERE id = :id'), {"id": int(material_id)})
        s.commit()

def delete_cloud_material(material_id):
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

    if "chemical_reports" in st.session_state and material_id in st.session_state.chemical_reports:
        del st.session_state.chemical_reports[material_id]
        
    if "experimental_curves" in st.session_state and material_id in st.session_state.experimental_curves:
        del st.session_state.experimental_curves[material_id]
        
    st.cache_data.clear()

def demote_cloud_material(material_id):
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

def _safe_num(val, default=0.0):
    """[CLAUDE FIX B3] Converte un valore dal DB in float per i campi dei form Edit.
    Campi lasciati vuoti nel Manual Form vengono salvati come NULL -> NaN in pandas: senza questo controllo
    il form Edit riceveva NaN come valore iniziale e lo riscriveva nel database."""
    try:
        f = float(val)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default

def update_cloud_material(
    mat_id, 
    grade, 
    thickness, 
    yield_mpa, 
    uts_mpa, 
    elongation_pct, 
    lotto_number, 
    lotto_figlio, 
    provider, 
    coil_weight_kg, 
    coil_length_mm,
    uploaded_file=None 
):
    cert_url = None
    cert_upload_ok = True   # [CLAUDE FIX C1] esito upload restituito al chiamante
    if uploaded_file is not None:
        file_path = f"certs/{mat_id}_{_safe_storage_name(uploaded_file.name)}"   # [CLAUDE FIX C1] nome sicuro
        try:
            # [CLAUDE FIX C1] storage_client al posto di supabase_client.storage (vedi init_storage_client)
            storage_client.from_("certificate_storage").upload(
                file_path, 
                uploaded_file.getvalue(), 
                file_options={"upsert": "true", "content-type": uploaded_file.type}
            )
            res = storage_client.from_("certificate_storage").get_public_url(file_path)   # [CLAUDE FIX C1]
            cert_url = res.get("publicUrl") or res.get("public_url") if isinstance(res, dict) else res
        except Exception as e:
            cert_upload_ok = False   # [CLAUDE FIX C1]
            st.error(f"Detailed Upload Error: {str(e)}")

    # [CLAUDE FIX B3] -----------------------------------------------------------------------------------------------
    # PROBLEMA: dopo "Save Changes" peso e larghezza nella tabella "Production Batch Queue" non cambiavano.
    # Causa: l'UPDATE modificava solo coil_weight_kg / coil_length_mm (valori originali), mentre la tabella, le card
    # del Warehouse e l'R&D Deck mostrano le giacenze residue rd_remaining_weight_kg / rd_remaining_length_mm,
    # che restavano quelle dell'inserimento iniziale.
    # SOLUZIONE: le giacenze residue vengono aggiornate della stessa differenza (nuovo - vecchio) applicata al valore
    # originale. Così:
    #   - coil appena inserito (residuo = originale)  -> residuo = nuovo valore inserito
    #   - coil già parzialmente consumato in R&D        -> il consumo registrato viene mantenuto
    #   - coil padre già splittato (ha figli)          -> il residuo resta 0 (il materiale è nei figli)
    # In PostgreSQL le espressioni nel SET leggono i valori PRIMA dell'aggiornamento, quindi (:nuovo - coil_weight_kg)
    # è proprio la differenza tra nuovo e vecchio valore.
    # Le due query duplicate (con / senza certificato) sono state unificate: certificate_url si aggiorna solo se
    # è stato caricato un nuovo file (COALESCE con il valore esistente).
    with conn.session as s:
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
                    rd_remaining_weight_kg = CASE
                        WHEN EXISTS (SELECT 1 FROM materials c WHERE c.parent_id = materials.id) THEN 0.0
                        ELSE GREATEST(0.0, COALESCE(rd_remaining_weight_kg, coil_weight_kg, 0.0)
                                           + (:coil_weight_kg - COALESCE(coil_weight_kg, 0.0)))
                    END,
                    rd_remaining_length_mm = CASE
                        WHEN EXISTS (SELECT 1 FROM materials c WHERE c.parent_id = materials.id) THEN 0.0
                        ELSE GREATEST(0.0, COALESCE(rd_remaining_length_mm, coil_length_mm, 0.0)
                                           + (:coil_length_mm - COALESCE(coil_length_mm, 0.0)))
                    END,
                    coil_weight_kg = :coil_weight_kg,
                    coil_length_mm = :coil_length_mm,
                    certificate_url = COALESCE(:certificate_url, certificate_url)
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
        s.commit()
    return cert_upload_ok   # [CLAUDE FIX C1] False = dati salvati ma certificato NON caricato
    # [END CLAUDE FIX B3] -------------------------------------------------------------------------------------------

#########################################################################################################################################################


#########################################################################################################################################################
##############################################  REGULATORY COMPLIANCE HELPER FUNCTIONS ##################################################################

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
###########################  END OF CHEMICAL COMPOSITION ##################

#########################################################################################################################################################

#########################################################################################################################################################
##############################################  APPLICATION NAVIGATION INITIALIZATION ###################################################################

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
################################################################################
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
        if st.button(" Open Area Crush", key="metals_crash_btn", use_container_width=True):
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

#########################################################################################################################################################


#########################################################################################################################################################
##############################################  R&D LABORATORY DECK PAGE ################################################################################

elif st.session_state.current_page == "RD_DECK":
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()
        
    family_name = st.session_state.active_family.title()
    st.title(f"{family_name} R&D Laboratory Deck")
    st.write("---")
    
    # [CLAUDE FIX A1] Prima: fetch_cloud_materials(family=...) -> caricava la query PROD (default hub_section="PROD").
    # Ora: sorgente dedicata "RD_DECK" = coil in produzione + coil in R&D (vedi fetch_cloud_materials).
    sq_rd_df = fetch_cloud_materials(hub_section="RD_DECK", family=st.session_state.active_family)
    
    if not sq_rd_df.empty:
        sq_rd_df['calc_weight_kg'] = sq_rd_df['rd_remaining_weight_kg'].fillna(sq_rd_df['coil_weight_kg'])
        sq_rd_df['calc_length_mm'] = sq_rd_df['rd_remaining_length_mm'].fillna(sq_rd_df['coil_length_mm'])
        sq_rd_df = sq_rd_df[sq_rd_df['calc_weight_kg'] > 0.001]

    combined_materials = {}
    if not sq_rd_df.empty:
        # Match Production Hub's exact sorting and indexing sequence after weight filtering
        rd_storage_df = sq_rd_df.sort_values(by="id").reset_index(drop=True).copy()
        rd_storage_df['seq_id'] = rd_storage_df.index + 1
        
        # [CLAUDE FIX A1b] -------------------------------------------------------------------------------------
        # Prima: venivano nascosti TUTTI i coil senza Component Code il cui lotto_number coincideva con il lotto
        # di un qualsiasi coil con Component Code. Coil diversi dello stesso lotto (stessa colata) sparivano
        # dall'R&D Deck -> causa del numero ridotto di coil "Standby".
        # Ora: si nascondono solo i padri realmente splittati, riconosciuti tramite parent_id dei figli.
        # (I padri splittati hanno comunque peso residuo 0 e sono già esclusi dal filtro peso qui sopra.)
        split_parent_ids = set(
            pd.to_numeric(rd_storage_df['parent_id'], errors='coerce').dropna().astype(int).tolist()
        )
        rd_storage_df = rd_storage_df[~rd_storage_df['id'].astype(int).isin(split_parent_ids)]
        # [END CLAUDE FIX A1b] ---------------------------------------------------------------------------------
        
        for _, row in rd_storage_df.iterrows():
            mat_id = int(row['id'])
            seq_id = int(row['seq_id'])
            
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
            # [CLAUDE FIX A1b] Allungamento da certificato: prima non veniva letto e il filtro lo vedeva sempre = 0.
            cert_el = float(row['elongation_pct']) if pd.notna(row.get('elongation_pct')) else 0.0
            
            c_weight = float(row.get('coil_weight_kg') or 0.0)
            c_length = float(row.get('coil_length_mm') or 0.0)
            rd_rem_weight = float(row.get('rd_remaining_weight_kg') if pd.notna(row.get('rd_remaining_weight_kg')) else c_weight)
            rd_rem_length = float(row.get('rd_remaining_length_mm') if pd.notna(row.get('rd_remaining_length_mm')) else c_length)
            rd_notes_val = str(row.get('rd_notes') or "")
            
            is_promoted = (row.get('is_promoted', 0) == 1)
            # [CLAUDE FIX A1] Posizione del coil: è "in R&D" se promosso oppure se hub_section = 'RD'.
            # Tutti gli altri sono "in produzione". Serve al filtro "Filter by Status".
            hub_sec_val = str(row.get('hub_section') or "PROD").strip().upper()
            in_rd = is_promoted or hub_sec_val == "RD"
            db_status = str(row.get('quality_status') or "").strip()
            
            if not db_status or db_status == "None":
                q_status = "🔬 R&D" if is_promoted else "🟢 Standby"
            else:
                q_status = db_status
            
            lab_data = st.session_state.experimental_curves.get(mat_id, {})
            lab_sy = f"{lab_data.get('sigy_MPa', 0.0):.0f}" if lab_data else "-"
            lab_sf = f"{lab_data.get('uts_MPa', 0.0):.0f}" if lab_data else "-"

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
                "cert_elong_pct": cert_el,          # [CLAUDE FIX A1b] allungamento da certificato
                "lotto": lotto_str,
                "coil_weight_kg": c_weight,
                "coil_length_mm": c_length,
                "rd_remaining_weight_kg": rd_rem_weight,
                "rd_remaining_length_mm": rd_rem_length,
                "rd_notes": rd_notes_val,
                "quality_status": q_status,
                "is_promoted": is_promoted,
                "in_rd": in_rd,                     # [CLAUDE FIX A1] True = coil in R&D, False = coil in produzione
                "datasheet": row['datasheet'] if pd.notna(row['datasheet']) else None
            }


    st.sidebar.markdown("Property Filters")
    
    filter_status = st.sidebar.selectbox(
        "Filter by Status", 
        ["All", "🟢 Standby", "🔬 R&D", "Verified ✅"],
        index=0
    )

    st.sidebar.write("---")

    available_grades = sorted(list(set(info["grade"] for info in combined_materials.values()))) if combined_materials else ["All"]
    filter_grade = st.sidebar.selectbox("Steel Class / Grade", ["All"] + available_grades)

    st.sidebar.write("---")

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

    
    # [CLAUDE FIX A1b] Gli slider meccanici filtrano SOLO se l'utente li sposta dai valori di default.
    # Prima i coil con valori di certificato mancanti (0) venivano esclusi in silenzio (min slider = 100 MPa).
    yield_filter_on = (min_yield, max_yield) != (100.0, 1000.0)
    uts_filter_on = (min_uts, max_uts) != (100.0, 1200.0)
    elong_filter_on = (min_elong, max_elong) != (0.0, 50.0)
    excluded_log = []   # [CLAUDE FIX A1b] (nome coil, motivo esclusione) -> mostrato nell'expander diagnostico

    filtered_materials = {}
    for mat_id, metadata in combined_materials.items():
        grade_match = (filter_grade == "All" or metadata.get("grade") == filter_grade)
        
        raw_st = str(metadata.get("quality_status", ""))
        # [CLAUDE FIX A1] ---------------------------------------------------------------------------------------
        # Nuova logica "Filter by Status":
        #   All         -> tutti i coil (produzione + R&D) TRANNE i non conformi
        #   🟢 Standby  -> coil IN PRODUZIONE con stato Standby (o stato non ancora assegnato)
        #   Verified ✅ -> coil IN PRODUZIONE verificati in laboratorio
        #   🔬 R&D      -> coil IN R&D (promossi / splittati)
        # I non conformi (stato contenente "Non-Compliant", "Rejected" o "❌") non sono mai mostrati in R&D Deck.
        in_rd = metadata.get("in_rd", False)
        is_non_compliant = ("Non-Compliant" in raw_st) or ("Rejected" in raw_st) or ("❌" in raw_st)
        is_verified = ("Verified" in raw_st) and not is_non_compliant
        is_standby = ("Standby" in raw_st) or (raw_st.strip() in ["", "None"])

        if is_non_compliant:
            status_match = False
        elif filter_status == "All":
            status_match = True
        elif filter_status == "🟢 Standby":
            status_match = (not in_rd) and is_standby
        elif filter_status == "Verified ✅":
            status_match = (not in_rd) and is_verified
        elif filter_status == "🔬 R&D":
            status_match = in_rd
        else:
            status_match = True
        # [END CLAUDE FIX A1] -----------------------------------------------------------------------------------

        thick_val = metadata.get("thickness", 0)
        thickness_match = True if not enable_thick_filter else (abs(thick_val - selected_thickness) <= 0.05)
        
        if cert_status_filter == "Supplier Cert.":
            active_yield = metadata.get("cert_yield_MPa", 0)
            active_uts = metadata.get("cert_uts_MPa", 0)
            active_elong = metadata.get("cert_elong_pct", 0)      # [CLAUDE FIX A1b] prima: "elongation_pct" (chiave inesistente -> 0)
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
            active_elong = metadata.get("cert_elong_pct", 0)      # [CLAUDE FIX A1b] prima: "elongation_pct" (chiave inesistente -> 0)
            cert_match = True

        # [CLAUDE FIX A1b] range applicati solo se attivati dall'utente (vedi sopra)
        yield_match = (not yield_filter_on) or (min_yield <= active_yield <= max_yield)
        uts_match = (not uts_filter_on) or (min_uts <= active_uts <= max_uts)
        elong_match = (not elong_filter_on) or (min_elong <= active_elong <= max_elong)

        if grade_match and status_match and thickness_match and yield_match and uts_match and elong_match and cert_match:
            filtered_materials[mat_id] = metadata
        else:
            # [CLAUDE FIX A1b] registra il primo motivo di esclusione (diagnostica per l'utente)
            if is_non_compliant:
                reason = "Non conforme (prova di laboratorio)"
            elif not status_match:
                reason = "Filtro stato (" + ("in R&D" if in_rd else "in produzione") + f", stato: {raw_st or 'vuoto'})"
            elif not grade_match:
                reason = "Filtro grade"
            elif not thickness_match:
                reason = "Filtro spessore"
            elif not cert_match:
                reason = "Nessun dato di laboratorio"
            else:
                reason = "Filtro proprietà meccaniche (yield / UTS / allungamento)"
            excluded_log.append((metadata.get("display_name", str(mat_id)), reason))

    main_col, chart_col = st.columns([1, 1])

    with main_col:
        st.subheader("Available Inventory & Promoted Batches")
        st.caption("Format: `[Cert: YS, UTS | Lab: YS, UTS]`")
        # [CLAUDE FIX A1b] Riepilogo: quanti coil sono mostrati e perché gli altri sono esclusi
        st.caption(f"Coil mostrati: **{len(filtered_materials)}** su **{len(combined_materials)}** disponibili (peso residuo > 0).")
        if excluded_log:
            with st.expander(f"🔎 Coil esclusi dai filtri ({len(excluded_log)})", expanded=False):
                st.dataframe(
                    pd.DataFrame(excluded_log, columns=["Coil", "Motivo esclusione"]),
                    use_container_width=True, hide_index=True
                )
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
                # [CLAUDE FIX A1b] Prima il badge "❌ Non-Compliant" indicava SOLO che lo yield del certificato è sotto
                # il minimo EN 10025 (get_en10025_limits) e veniva confuso con lo stato "Non conforme" di laboratorio.
                # Ora i due concetti sono separati: "Quality" = esito prove lab, "Cert vs EN" = controllo certificato.
                status_badge = "✅ Cert ≥ EN min" if is_compliant else f"⚠️ Cert yield < EN min ({limits['min_yield']} MPa)"
                location_badge = "🔬 In R&D" if info.get("in_rd") else "🏭 In Produzione"
                st.markdown(
                    f"**Quality:** {info.get('quality_status')} | {location_badge} | **Cert vs EN:** {status_badge} | "
                    f"{inv_badge} | **Stock:** {rem_weight/1000.0:.1f} TON / W {rem_len:.1f} mm"   # [CLAUDE FIX W1] W = larghezza
                )
                
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
            **Remaining Stock:** **{curr_rem_w/ 1000.0:.3f} TON** / Width **{curr_rem_l:.1f} mm**  
            *(Original Baseline: {init_w / 1000.0:.3f} TON / Width {init_l:.1f} mm)*  
            📝**Notes / Log History:** {rd_notes_val if rd_notes_val else "No cuts logged yet."}
            """)

            with st.form(key=f"rd_tracker_form_{mat_id}"):
                # [CLAUDE FIX W1] Rimosso il campo "Length Used [mm]": il campo dimensionale del coil è la LARGHEZZA,
                # che non diminuisce quando si consuma materiale (si consuma lunghezza di nastro = peso).
                # Si scala quindi solo il peso; la larghezza resta invariata (length_consumed = 0).
                weight_consumed_ton = st.number_input("Weight Used [TON]", value=0.0, step=0.1, format="%.3f")
                length_consumed = 0.0
                
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

            # --- FIX: Synchronize session state report with the currently selected material ---
            if "chemical_reports" not in st.session_state:
                st.session_state.chemical_reports = {}

            if mat_id not in st.session_state.chemical_reports:
                saved_spec_json = mat_info.get("spectrometer_data_json")
                if saved_spec_json:
                    try:
                        loaded_spec = json.loads(saved_spec_json) if isinstance(saved_spec_json, str) else saved_spec_json
                        df_loaded = pd.DataFrame(loaded_spec.get("df_records", []))
                        comp_loaded = loaded_spec.get("comp", {})
                        st.session_state.chemical_reports[mat_id] = {"df": df_loaded, "comp": comp_loaded}
                    except Exception:
                        pass
            # ------------------------------------------------------------------------------

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
        else:
            st.info("No spectrometer report uploaded yet for this material.")

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
#########################################################################################################################################################

#########################################################################################################################################################
##############################################  PRODUCTION HUB PAGE #####################################################################################
elif st.session_state.current_page == "PROD_HUB":
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()
        
    family_name = st.session_state.active_family.title()
    st.title(f"{family_name} — Production Hub")
    st.write("---")
    
    prod_df = fetch_cloud_materials(family=st.session_state.active_family)
    
    if not prod_df.empty:
        prod_df['calc_weight_kg'] = prod_df['rd_remaining_weight_kg'].fillna(prod_df['coil_weight_kg'])
        prod_df['calc_length_mm'] = prod_df['rd_remaining_length_mm'].fillna(prod_df['coil_length_mm'])
        prod_df = prod_df[prod_df['calc_weight_kg'] > 0.001]
    if not prod_df.empty:
        temp_storage_df = prod_df.sort_values(by="id").reset_index(drop=True).copy()
        temp_storage_df['seq_id'] = temp_storage_df.index + 1
        
        # [CLAUDE FIX B4] -------------------------------------------------------------------------------------
        # Prima: venivano esclusi dal Warehouse TUTTI i coil il cui lotto_number coincideva con il lotto di un
        # qualsiasi coil con Component Code (figli compresi e coil diversi della stessa colata).
        # Ora le esclusioni sono esplicite e basate su id / posizione, non sul numero di lotto:
        #   1) padri realmente splittati -> riconosciuti tramite parent_id dei figli
        #      (hanno già peso residuo 0 e sono esclusi dal filtro peso: questo è un controllo di sicurezza)
        #   2) coil già passati in R&D (is_promoted = 1 oppure hub_section = 'RD') -> il Warehouse mostra
        #      solo i coil fisicamente in produzione, coerente con il filtro "Filter by Status" dell'R&D Deck.
        #      Restano comunque visibili nella tabella "Production Batch Queue" sottostante.
        split_parent_ids = set(
            pd.to_numeric(temp_storage_df['parent_id'], errors='coerce').dropna().astype(int).tolist()
        )
        is_split_parent = temp_storage_df['id'].astype(int).isin(split_parent_ids)

        is_in_rd = (
            (pd.to_numeric(temp_storage_df['is_promoted'], errors='coerce').fillna(0) == 1) |
            (temp_storage_df['hub_section'].fillna('PROD').astype(str).str.strip().str.upper() == 'RD')
        )

        temp_storage_df = temp_storage_df[~is_split_parent & ~is_in_rd]
        # [END CLAUDE FIX B4] ---------------------------------------------------------------------------------
        
        temp_storage_df['active_lot'] = temp_storage_df['lotto_number'].apply(
            lambda x: str(x).strip() if pd.notna(x) and str(x).strip() not in ['', 'None', 'nan'] else "Unknown Lot"
        )
        
        # [CLAUDE FIX B4] Costruzione con list comprehension invece di DataFrame.apply(axis=1):
        # con il nuovo filtro il Warehouse può restare vuoto (es. tutti i coil in R&D) e apply() su un
        # DataFrame vuoto restituisce un DataFrame, che non può essere assegnato a una colonna (errore).
        temp_storage_df['lot_detail'] = [
            f"<b>ID #{row['seq_id']}</b> : {row['active_lot']} <i>({row['calc_weight_kg']/1000.0:.1f} t, W {row.get('calc_length_mm', 0):.0f} mm)</i>"   # [CLAUDE FIX W1] W = larghezza
            for _, row in temp_storage_df.iterrows()
        ]
        
        # [CLAUDE FIX W1] raggruppamento card: grade + spessore + LARGHEZZA (calc_length_mm = larghezza)
        grouped_storage = temp_storage_df.groupby(['grade', 'thickness', 'calc_length_mm']).agg(
            coil_count=('active_lot', 'count'),
            total_weight=('calc_weight_kg', 'sum'),
            lots=('lot_detail', lambda x: "<br/>".join(x.dropna().astype(str).tolist()))
        ).reset_index()
    else:
        grouped_storage = pd.DataFrame(columns=['grade', 'thickness', 'calc_length_mm', 'coil_count', 'total_weight', 'lots'])


    st.markdown(" Warehouse Coil Situation")
    st.caption("💡 *Click **'Manage Group'** on any card below to open the split and management window for its coils.*")
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
{grade} | {thick} mm | W {length:.0f} mm
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
                            open_coil_management_dialog(grade, thick, length, group_rows)
    else:
        st.info("No active coils currently in storage.")
    
    st.write("---")
    with st.container(border=True):
        st.markdown(" Quality Verification Hub (Tensile Testing)")
        st.caption(" *Input 3 tensile test measurements. Fields start blank. Includes a professional 10 MPa / 5% industrial engineering allowance buffer.*")

        if not prod_df.empty:
            qv_col1, qv_col2 = st.columns([1, 1.5], gap="large")
            
            with qv_col1:
                st.markdown(" Select Batch")
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
                st.markdown("#### 📊 Enter 3 Tensile Test Samples")
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
                            st.warning(" Please fill in all 9 tensile test input fields before evaluating.")
                        else:
                            avg_y = (t1_y + t2_y + t3_y) / 3.0
                            avg_u = (t1_u + t2_u + t3_u) / 3.0
                            avg_e = (t1_e + t2_e + t3_e) / 3.0
                            
                            ref_y = float(qv_row.get('yield_mpa', 0.0))
                            ref_u = float(qv_row.get('uts_mpa', 0.0))
                            ref_e = float(qv_row.get('elongation_pct', 0.0))
                        
                        #EZIO  tensile testing values 
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


    st.markdown(f"###  Production Batch Queue ({family_name})")
    st.caption("💡 *Rows highlighted in light red indicate any Lotto at or below the 20 Ton low-stock threshold.*")
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
            "current_length_mm": "Width [mm]",          # [CLAUDE FIX W1] prima "Remaining Length [mm]"
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
            "Remaining Weight [TON]", "Width [mm]", "Lotto Father", "Component Code", "provider", "Status", "real_db_id"
        ]
        table_display_view = display_df[[c for c in cols_order if c in display_df.columns and c != "real_db_id"]]
        #EZIO LOW STOCK 
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
    st.markdown(" Actions & Edits")
    
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
                            promote_cloud_material(selected_prod_id)
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
                                delete_cloud_material(selected_prod_id)
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
                        # [CLAUDE FIX B3] valori iniziali letti con _safe_num(): un campo vuoto (NULL) non viene più riscritto come NaN
                        new_thickness = st.number_input("Thickness [mm]", value=_safe_num(selected_row.get("thickness")), step=0.1)
                        new_yield = st.number_input("Yield Stress [MPa]", value=_safe_num(selected_row.get("yield_mpa")), step=5.0)
                        new_uts = st.number_input("Failure Stress [MPa]", value=_safe_num(selected_row.get("uts_mpa")), step=5.0)
                        new_elong = st.number_input("Elongation [%]", value=_safe_num(selected_row.get("elongation_pct")), step=0.5)
                        db_weight_kg = _safe_num(selected_row.get("coil_weight_kg"))   # [CLAUDE FIX B3] valore sicuro (NULL -> 0)
                        initial_weight_tons = db_weight_kg / 1000.0 if db_weight_kg else 0.0
                        new_weight_tons = st.number_input("Coil Weight [TON]", value=initial_weight_tons, step=1.0, format="%.3f")
                        # [CLAUDE FIX W1] coil_length_mm = LARGHEZZA coil: prima "Coil Length [mm]"
                        new_length = st.number_input("Coil Width [mm]", value=_safe_num(selected_row.get("coil_length_mm")), step=10.0)
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
                            # [CLAUDE FIX C1] Prima compariva "Batch updated successfully!" anche se l'upload falliva,
                            # e lo st.rerun() cancellava subito il messaggio di errore. Ora si controlla l'esito.
                            cert_ok = update_cloud_material(
                                selected_prod_id, 
                                grade=new_grade, thickness=new_thickness, 
                                yield_mpa=new_yield, uts_mpa=new_uts, 
                                elongation_pct=new_elong, lotto_number=new_lotto_padre, 
                                lotto_figlio=new_component_code, provider=new_provider,
                                coil_weight_kg=new_weight_tons * 1000.0, coil_length_mm=new_length,
                                uploaded_file=cert_file_obj  
                            )
                            st.cache_data.clear()
                            if cert_ok:
                                st.success("Batch updated successfully!")
                                st.rerun()
                            else:
                                st.warning("⚠️ Valori del coil salvati, ma il certificato NON è stato caricato (vedi errore sopra).")

                with st.expander(" Split Coil (Father -> Sons)", expanded=False):
                    rem_w_val = selected_row.get("rd_remaining_weight_kg")
                    current_w_kg = float(rem_w_val) if pd.notna(rem_w_val) else float(selected_row.get("coil_weight_kg", 0.0))
                    current_w_tons = current_w_kg / 1000.0

                    rem_l_val = selected_row.get("rd_remaining_length_mm")
                    current_l = float(rem_l_val) if pd.notna(rem_l_val) else float(selected_row.get("coil_length_mm", 0.0))
                    
                    base_lotto = selected_row.get('lotto_figlio') if pd.notna(selected_row.get('lotto_figlio')) else selected_row.get('lotto_number', '')
                    
                    st.write(f"Available to split: **{current_w_tons:.3f} TON** / Width **{current_l:.2f} mm**")   # [CLAUDE FIX W1]
                    
                    num_children = st.number_input("Number of Components", min_value=2, max_value=10, value=2, step=1)
                    
                    with st.form(key=f"split_form_{selected_prod_id}"):
                        children_inputs = []
                        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        weight_per_mm_kg = current_w_kg / current_l if current_l > 0 else 0.0   # [CLAUDE FIX W1] peso per mm di LARGHEZZA
                        
                        for i in range(int(num_children)):
                            st.markdown(f"**Component {i+1} ({letters[i]})**")
                            cc1, cc2, cc3 = st.columns(3)
                            with cc1:
                                c_lotto = st.text_input(f"Component Code {i+1}", value=f"{base_lotto}-{letters[i]}", key=f"split_lotto_{selected_prod_id}_{i}")
                            with cc2:
                                c_length = st.number_input(f"Width {i+1} [mm]", min_value=0.0, max_value=current_l, value=0.0, key=f"split_l_{selected_prod_id}_{i}")
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
                                st.error("Sum of child widths exceeds parent coil width!")   # [CLAUDE FIX W1]
                            else:
                                split_cloud_material(selected_prod_id, children_inputs, auto_promote_rd=True)
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
    
 
    with st.expander(" Manual Form Entry & File Upload (New Batch)", expanded=False):
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
                    # [CLAUDE FIX W1] coil_length_mm = LARGHEZZA coil: prima "10. Coil Length [mm]"
                    coil_length = st.number_input("10. Coil Width [mm]", value=None, step=10.0)
                
                uploaded_cert = st.file_uploader("Upload Test Certificate (.pdf, .png, .jpg)", type=["pdf", "png", "jpg", "jpeg"])
                
                if st.form_submit_button("💾 Save Batch", use_container_width=True):
                    cert_url = None
                    active_lotto_label = lotto_padre if lotto_padre else (lotto_figlio if lotto_figlio else "batch")
                    if uploaded_cert is not None:
                        cert_url = upload_certificate_to_cloud(uploaded_cert, active_lotto_label)

                    coil_weight_kg_val = (coil_weight_tons * 1000.0) if coil_weight_tons else 0.0

                    insert_cloud_material(
                        grade, thickness, sig_yield, sig_fail, elongation, 
                        lotto_padre=lotto_padre, lotto_figlio=lotto_figlio, provider=provider, 
                        family=st.session_state.active_family,
                        cert_path=cert_url,
                        coil_weight=coil_weight_kg_val,
                        coil_length_mm=coil_length
                    )
                    st.cache_data.clear()
                    # [CLAUDE FIX C1] se il certificato era stato allegato ma l'upload è fallito, il coil è comunque
                    # registrato: si mostra un avviso e NON si fa rerun (altrimenti l'errore sparirebbe subito).
                    if uploaded_cert is not None and not cert_url:
                        st.warning("⚠️ Coil registrato con stato 'Standby', ma il certificato NON è stato caricato (vedi errore sopra). "
                                   "Puoi caricarlo dopo da 'Edit Selected Batch Values'.")
                    else:
                        st.toast("✅ Batch registered into Production database with 'Standby' status!")
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
                        # [CLAUDE FIX W1] il file Excel può usare la colonna "coil_width_mm" (nuova) o "coil_length_mm" (vecchia)
                        if "coil_width_mm" in df_upload.columns and "coil_length_mm" not in df_upload.columns:
                            df_upload = df_upload.rename(columns={"coil_width_mm": "coil_length_mm"})

                    for col in ["grade", "lotto_number", "lotto_figlio", "provider"]:
                        if col in df_upload.columns:
                            df_upload[col] = df_upload[col].astype(str).str.strip()
                            df_upload[col] = df_upload[col].apply(lambda x: None if x in ["nan", "None", ""] else x)

                    # [CLAUDE FIX W1] in anteprima la colonna larghezza è mostrata come "coil_width_mm"
                    st.dataframe(df_upload.rename(columns={"coil_length_mm": "coil_width_mm"}), use_container_width=True, hide_index=True)

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
                                l_mm = safe_float(r.get("coil_length_mm", 0.0))   # [CLAUDE FIX W1] = larghezza coil [mm]
                                
                                insert_cloud_material(
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


#########################################################################################################################################################

#########################################################################################################################################################
##############################################  AREA CRUSH PAGE #########################################################################################
elif st.session_state.current_page == "area_crush":
    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()

    st.markdown("""
        <style>
            /* 1. Scale up all standard form inputs, text areas, labels, and text */
            .stTextInput input, .stTextArea textarea, label, .stMarkdown p, .stRadio label {
                font-size: 18px !important;
            }
            
            /* 2. Target Streamlit Data Editor canvas/grid text */
            canvas {
                font-size: 16px !important;
            }

            /* 3. Force headers to be larger */
            div[data-testid="stDataEditor"] th, div[data-testid="stDataEditor"] [role="columnheader"] {
                font-size: 17px !important;
                font-weight: bold !important;
            }
            
            /* 4. Force the first data column cells to align left */
            div[data-testid="stDataEditor"] td:nth-child(2), 
            div[data-testid="stDataEditor"] th:nth-child(2) {
                text-align: left !important;
            }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(f" Area Crush — {st.session_state.active_family} Family")

    crush_df = fetch_cloud_materials(family=st.session_state.active_family, hub_section="CRUSH")
    allowed_cols = ['id', 'family', 'hub_section', 'grade', 'lotto_number', 'codice', 'description', 'quantity', 'rd_notes']
    existing_cols = [c for c in allowed_cols if c in crush_df.columns]
    crush_df = crush_df[existing_cols]

    col1, col2 = st.columns([2, 2])
    with col1:
        search_codice = st.text_input("🔍 Search by Code", placeholder="Enter codice to filter...")   
    if search_codice and not crush_df.empty:
        crush_df = crush_df[crush_df['codice'].astype(str).str.contains(search_codice, case=False, na=False)]
    st.markdown("#### 📋 Crush Batch Queue & Quantities")
    st.info(" Update quantities and notes directly in the table below, then click 'Save Changes'.")
    if not crush_df.empty:
        crush_df = crush_df.reset_index(drop=True)
        crush_df.insert(0, "No.", (crush_df.index + 1).astype(str))
        edited_crush_df = st.data_editor(
            crush_df,
            column_order=["No.", "grade", "lotto_number", "codice", "description", "quantity", "rd_notes"],
            column_config={
                "id": None,
                "family": None,
                "hub_section": None,
                "No.": st.column_config.NumberColumn("ID.", disabled=True, width="small"),
                "grade": "Grade",
                "lotto_number": "Lotto Padre",
                "codice": "Codice",
                "description": "Description",
                "quantity": "Quantity",
                "rd_notes": "Note"
            },
            hide_index=True,
            num_rows="dynamic",
            key="crush_data_editor"
        )
        if st.button("💾 Save Changes to Database", type="primary"):
            with conn.session as s:
                current_ui_ids = []
                for _, row in edited_crush_df.iterrows():
                    row_id = row.get('id')
                    if pd.notna(row_id):
                        current_ui_ids.append(int(row_id))
                        s.execute(
                            text("""
                                UPDATE materials 
                                SET grade = :grade, 
                                    lotto_number = :lotto_number, 
                                    codice = :codice, 
                                    description = :description, 
                                    quantity = :quantity, 
                                    rd_notes = :rd_notes
                                WHERE id = :id
                            """),
                            {
                                "grade": row.get('grade'),
                                "lotto_number": row.get('lotto_number'),
                                "codice": row.get('codice'),
                                "description": row.get('description'),
                                "quantity": row.get('quantity'),
                                "rd_notes": row.get('rd_notes'),
                                "id": row_id
                            }
                        )
                    else:
                        s.execute(
                            text("""
                                INSERT INTO materials (family, hub_section, grade, lotto_number, codice, description, quantity, rd_notes)
                                VALUES (:family, :hub_section, :grade, :lotto_number, :codice, :description, :quantity, :rd_notes)
                            """),
                            {
                                "family": st.session_state.active_family,
                                "hub_section": "CRUSH",
                                "grade": row.get('grade'),
                                "lotto_number": row.get('lotto_number'),
                                "codice": row.get('codice'),
                                "description": row.get('description'),
                                "quantity": row.get('quantity'),
                                "rd_notes": row.get('rd_notes')
                            }
                        )
                original_ids = crush_df['id'].dropna().astype(int).tolist()
                deleted_ids = [rid for rid in original_ids if rid not in current_ui_ids]
                for del_id in deleted_ids:
                    s.execute(
                        text("DELETE FROM materials WHERE id = :id"),
                        {"id": del_id}
                    )
                s.commit()
            st.success("Area Crush data updated and deletions synchronized successfully!")
            st.rerun()
    else:
        st.warning("No crush records found for this family.")
    st.write("---")

    st.markdown(" 📥 Manual Entry or .txt Batch Import")
    entry_mode = st.radio("Entry Method", ["Manual Form", "Upload .txt File"], horizontal=True, key="crush_mode")
    if entry_mode == "Manual Form":
        with st.form("crush_manual_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                g_in = st.text_input("Grade", key="crush_g")
                cod_in = st.text_input("Codice", key="crush_cod")
                qty_in = st.text_input("Quantity", key="crush_qty")
            with c2: 
                lp_in = st.text_input("Lotto Padre", key="crush_lp")
                desc_in = st.text_input("Description", key="crush_desc")
                
            note_in = st.text_area("Note", key="crush_note")                        
            if st.form_submit_button("Add Crush Record"):
                with conn.session as s:
                    s.execute(
                        text("""
                            INSERT INTO materials (family, hub_section, grade, lotto_number, codice, description, quantity, rd_notes)
                            VALUES (:family, :hub_section, :grade, :lotto_number, :codice, :description, :quantity, :rd_notes)
                        """),
                        {
                            "family": st.session_state.active_family,
                            "hub_section": "CRUSH",
                            "grade": g_in,
                            "lotto_number": lp_in,
                            "codice": cod_in,
                            "description": desc_in,
                            "quantity": qty_in,
                            "rd_notes": note_in
                        }
                    )
                    s.commit()
                st.success("New crush part record added")
                st.rerun()
                
    elif entry_mode == "Upload .txt File":
        if "uploader_reset_counter" not in st.session_state:
            st.session_state["uploader_reset_counter"] = 0
        txt_file = st.file_uploader(
            "Upload Crush .txt File", 
            type=["txt"], 
            key=f"crush_txt_{st.session_state['uploader_reset_counter']}"
        )
        
        if txt_file is not None:
            content = txt_file.getvalue().decode("utf-8")
            st.text_area("File Content Preview", content, height=120)
            if st.button("Import .txt to Crush Database"):
                with conn.session as s:
                    for line in content.splitlines():
                        if line.strip():
                            parts = line.split(",")
                            if len(parts) >= 5:
                                s.execute(
                                    text("""
                                        INSERT INTO materials (family, hub_section, grade, lotto_number,  codice, description, quantity, rd_notes)
                                        VALUES (:family, :hub_section, :grade, :lotto_number, :codice, :description, :quantity, :rd_notes)
                                    """),
                                    {
                                        "family": st.session_state.active_family,
                                        "hub_section": "CRUSH",
                                        "grade": parts[0].strip(),
                                        "lotto_number": parts[1].strip(),
                                        "codice": parts[2].strip(),
                                        "description": parts[3].strip(),
                                        "quantity": parts[4].strip(),
                                        "rd_notes": parts[5].strip() if len(parts) > 5 else ""
                                    }
                                )
                    s.commit()      
                st.session_state["uploader_reset_counter"] += 1
                st.success("TXT file successfully imported into Area Crush!")
                st.rerun()