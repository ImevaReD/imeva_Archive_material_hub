import streamlit as st
import pandas as pd
from sqlalchemy import text
from database import fetch_cloud_materials

def area_crush_deck(conn):

    if st.session_state.get("clear_crush_txt", False):
        st.session_state["crush_txt"] = None
        st.session_state["clear_crush_txt"] = False

    if st.button("← Return to Central Portal"):
        st.session_state.current_page = "HOME"
        st.session_state.selected_material = None
        st.rerun()

################# Page Styling Customizations #################
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

    active_family = st.session_state.get('active_family', 'Default')
    st.markdown(f"### 💥 Area Crush — {active_family} Family")
#########################################################################################################################################################
################# Fetch Data for Section #################
    crush_df = fetch_cloud_materials(conn, family=active_family, hub_section="CRUSH")

    allowed_cols = ['id', 'family', 'hub_section', 'grade', 'lotto_number', 'codice', 'description', 'quantity', 'rd_notes']
    existing_cols = [c for c in allowed_cols if c in crush_df.columns]
    crush_df = crush_df[existing_cols]
#########################################################################################################################################################
################# Search Filter #################
    col1, col2 = st.columns([2, 2])
    with col1:
        search_codice = st.text_input("🔍 Search by Code", placeholder="Enter codice to filter...")
    
    if search_codice and not crush_df.empty:
        crush_df = crush_df[crush_df['codice'].astype(str).str.contains(search_codice, case=False, na=False)]

    st.markdown("#### Crush Batch Queue & Quantities")
    st.info("Update quantities and notes directly in the table below, then click 'Save Changes'.")
#########################################################################################################################################################    
################# Data Editor Grid #################
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
                edited_ids = []
                for _, row in edited_crush_df.iterrows():
                    row_id = row.get('id')
                    if pd.notna(row_id) and str(row_id).strip() !="":
                        edited_ids.append(int(row_id))
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
                                "id": row['id']
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
                                "grade": g_in,
                                "lotto_number": lp_in,
                                "codice": cod_in,
                                "description": desc_in,
                                "quantity": qty_in,
                                "rd_notes": note_in
                            }
                        )
                original_ids=crush_df['id'].dropna().tolist() if 'id' in crush_df.columns else []
                deleted_ids = [oid for oid in original_ids if oid not in edited_ids]

                for del_id in deleted_ids:
                    s.execute(
                        text("DELETE FROM materials where id= :id"),
                        {"id": int(del_id)}
                    )
                s.commit()
            st.success("Area Crush data updated successfully!")
            st.rerun()
    else:
        st.warning("No crush records found for this family.")

    st.write("---")
########################################################################################################################################
################# Manual Entry &  File Import Forms  #################
    st.markdown("#### 📥 Manual Entry or .txt Batch Import")
    entry_mode = st.radio("Entry Method", ["Manual Form", "Upload .txt File"], horizontal=True, key="crush_mode")
    
    if entry_mode == "Manual Form":
            with st.form("crush_manual_form"):
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
                    st.session_state["crush_g"] = ""
                    st.session_state["crush_cod"] = ""
                    st.session_state["crush_lp"] = ""
                    st.session_state["crush_desc"] = ""
                    st.session_state["crush_qty"] = ""
                    st.session_state["crush_note"] = ""
                    st.success("New crush record added")
                    st.rerun()
                
    elif entry_mode == "Upload .txt File":
        txt_file = st.file_uploader("Upload Crush .txt File", type=["txt"], key="crush_txt")
        if txt_file is not None:
            content = txt_file.getvalue().decode("utf-8")
            st.text_area("File Content Preview", content, height=120)
            if st.button("Import .txt to Crush Database"):
                with conn.session as s:
                    for line in content.splitlines():
                        if line.strip():
                            parts = line.split(",")
                            if len(parts) >= 6:
                                s.execute(
                                    text("""
                                        INSERT INTO materials (family, hub_section, grade, lotto_number, codice, description, quantity, rd_notes)
                                        VALUES (:family, :hub_section, :grade, :lotto_number, :codice, :description, :quantity, :rd_notes)
                                    """),
                                    {
                                        "family": active_family,
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
                st.session_state["clear_crush_txt"] = True 
                st.success("TXT file successfully imported into Area Crush!")
                st.rerun()


