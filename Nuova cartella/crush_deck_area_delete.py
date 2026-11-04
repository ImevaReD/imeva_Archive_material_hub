if st.button("💾 Save Changes to Database", type="primary"):
            with conn.session as s:
                edited_ids = []
                for _, row in edited_crush_df.iterrows():
                    row_id = row.get('id')

                    
                    if pd.notna(row_id) and str(row_id).strip() != "":
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
                                "id": int(row_id)
                            }
                        )
                    else:
                        s.execute(
                            text("""
                                INSERT INTO materials (family, hub_section, grade, lotto_number, codice, description, quantity, rd_notes)
                                VALUES (:family, :hub_section, :grade, :lotto_number, :codice, :description, :quantity, :rd_notes)
                            """),
                            {
                                "family": active_family,
                                "hub_section": "CRUSH",
                                "grade": row.get('grade'),
                                "lotto_number": row.get('lotto_number'),
                                "codice": row.get('codice'),
                                "description": row.get('description'),
                                "quantity": row.get('quantity'),
                                "rd_notes": row.get('rd_notes')
                            }
                        )

                original_ids = crush_df['id'].dropna().tolist() if 'id' in crush_df.columns else []
                deleted_ids = [oid for oid in original_ids if oid not in edited_ids]
                
                for del_id in deleted_ids:
                    s.execute(
                        text("DELETE FROM materials WHERE id = :id"),
                        {"id": int(del_id)}
                    )

                s.commit()
                
            st.success("Area Crush data updated successfully!")
            st.rerun()