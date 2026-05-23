import streamlit as st
import pandas as pd

from backend_window import (
    get_window_elements,
    process_index_simultaneously,
    identify_changed_combinations,
    apply_window_elements,
    filter_old_combinations,
    execute_step_3_merging,
    format_for_data_editor
)

def format_custom_tsv(df: pd.DataFrame) -> str:
    """
    Industry 4.0 Specific Formatter:
    Converts DataFrame to 'ColName+Value' tab-separated string.
    Ignores empty lists [].
    """
    tsv_lines = []
    for _, row in df.iterrows():
        row_elements = []
        for col in df.columns:
            val = str(row[col]).strip()
            # Filter out empty indicators used in your logic
            if val not in ["[]", "['']", "", "nan", "None"]:
                # Logic: Concatenate Column Header + Value (e.g., XF + 01 = XF01)
                row_elements.append(f"{col}{val}")
        if row_elements:
            tsv_lines.append("\t".join(row_elements))
    return "\n".join(tsv_lines)

st.set_page_config(layout="wide")

# ==========================================
# STATE MANAGEMENT
# ==========================================
if 'stage' not in st.session_state:
    st.session_state.stage = 0
if 'delta_results' not in st.session_state:
    st.session_state.delta_results = []

def text_to_list(text):
    """Safely converts multiline text to a list, keeping empty lines as empty strings."""
    if not text: return []
    return [line.strip() for line in text.split('\n')]

# ==========================================
# UI LAYOUT
# ==========================================
st.title("Windows application Tool")

# --- INITIAL INPUTS ---
col1, col2, col3, col4 = st.columns([1, 3, 1, 3])
with col1: old_prod_input = st.text_area("Old Product")
with col2: old_ecdv_input = st.text_area("Old ECDV")
with col3: new_prod_input = st.text_area("New Product")
with col4: new_ecdv_input = st.text_area("New ECDV")

col_date, col_qtr = st.columns([1, 1])
with col_date: dan_date_input = st.text_input("DAN Date (YYYY-MM-DD)")
with col_qtr: quarter_input = st.text_input("Quarter (e.g., A2026)")

# ==========================================
# STAGE 1: DELTA IDENTIFICATION
# ==========================================
if st.button("combinations undergoing change"):
    try:
        new_p_list = text_to_list(new_prod_input)
        new_e_list = text_to_list(new_ecdv_input)
        old_p_list = text_to_list(old_prod_input)
        old_e_list = text_to_list(old_ecdv_input)

        # Pad lists to same length to prevent zipper errors
        max_len = max(len(new_p_list), len(new_e_list), len(old_p_list), len(old_e_list))
        new_p_list += [""] * (max_len - len(new_p_list))
        new_e_list += [""] * (max_len - len(new_e_list))
        old_p_list += [""] * (max_len - len(old_p_list))
        old_e_list += [""] * (max_len - len(old_e_list))

        opening_win, closing_win = get_window_elements(quarter_input)
        
        results_cache = []
        for idx in range(max_len):
            old_df, new_df, CM, Family = process_index_simultaneously(
                idx, old_p_list, old_e_list, new_p_list, new_e_list
            )
            
            # --- MODIFIED: Added quarter_input to parameters ---
            final_old, final_new, final_unchanged = identify_changed_combinations(
                old_p_list[idx], old_df, new_p_list[idx], new_df, quarter_input
            )
            
            results_cache.append({
                "idx": idx, "CM": CM, "Family": Family,
                "old_p": old_p_list[idx], "new_p": new_p_list[idx],
                "final_old": final_old, "final_new": final_new,
                "final_unchanged": final_unchanged
            })
            
        st.session_state.delta_results = results_cache
        st.session_state.opening_win = opening_win
        st.session_state.closing_win = closing_win
        st.session_state.stage = 1
        st.success("Delta processed successfully!")
    except Exception as e:
        # Note: If Case 3 ValueError triggers, it will be caught gracefully here!
        st.error(f"Error processing deltas: {str(e)}")

# ==========================================
# STAGE 1 UI: HUMAN-IN-THE-LOOP
# ==========================================
if st.session_state.stage >= 1:
    st.markdown("---")
    edited_dataframes = {}

    for res in st.session_state.delta_results:
        idx = res["idx"]
        st.write(f"### Index {idx} | {res['old_p']} -> {res['new_p']}")
        
        # --- OLD DATAFRAME UI ---
        if not res["final_old"].empty:
            st.markdown("**Final Old Combinations**")
            
            # Prepare the custom dataframe format
            editor_df_old = format_for_data_editor(res["final_old"])
            
            # Lock the configuration columns, keep input columns editable
            disabled_cols_old = [c for c in editor_df_old.columns if c not in ["below or above", "versions start date"]]
            
            # Render interactive table and save the user's edits
            edited_dataframes[f"old_{idx}"] = st.data_editor(
                editor_df_old, 
                key=f"editor_old_{idx}", 
                disabled=disabled_cols_old,
                use_container_width=True,
                hide_index=True
            )

            with st.expander("📋 Copy Custom TSV (For Manufacturing Docs)"):
                st.code(format_custom_tsv(res["final_old"]), language="text")
                
        # --- NEW DATAFRAME UI ---
        if not res["final_new"].empty:
            st.markdown("**Final New Combinations**")
            
            editor_df_new = format_for_data_editor(res["final_new"])
            disabled_cols_new = [c for c in editor_df_new.columns if c not in ["below or above", "versions start date"]]
            
            edited_dataframes[f"new_{idx}"] = st.data_editor(
                editor_df_new, 
                key=f"editor_new_{idx}", 
                disabled=disabled_cols_new,
                use_container_width=True,
                hide_index=True
            )

            with st.expander("📋 Copy Custom TSV (For Manufacturing Docs)"):
                st.code(format_custom_tsv(res["final_new"]), language="text")
        
        st.markdown("---")

    # ==========================================
    # STAGE 2: WINDOWS TREATMENT
    # ==========================================
    if st.button("windows treatment"):
        try:
            final_summaries = []
            
            for res in st.session_state.delta_results:
                idx = res["idx"]
                
                # --- Extract from edited_dataframes ---
                e_old = edited_dataframes.get(f"old_{idx}")
                e_new = edited_dataframes.get(f"new_{idx}")
                
                o_ba = e_old["below or above"].tolist() if e_old is not None else [None] * len(res["final_old"])
                o_vsd = e_old["versions start date"].tolist() if e_old is not None else [None] * len(res["final_old"])
                
                n_ba = e_new["below or above"].tolist() if e_new is not None else [None] * len(res["final_new"])
                n_vsd = e_new["versions start date"].tolist() if e_new is not None else [None] * len(res["final_new"])

                # 1. Apply Windows 
                df_old, df_new = apply_window_elements(
                    res["final_old"], res["final_new"], dan_date_input,
                    o_vsd, o_ba, n_vsd, n_ba,
                    st.session_state.opening_win, st.session_state.closing_win
                )
                
                # 2. Filter Old
                filtered_df = filter_old_combinations(df_old, st.session_state.closing_win)
                
                # 3. Merge and Generate
                merge_results = execute_step_3_merging(
                    res["old_p"], filtered_df, res["new_p"], df_new,
                    res["final_unchanged"],
                    res["CM"], res["Family"]
                )
                
                # Compile Output Format
                summary_row = {
                    "Case": merge_results["case_executed"],
                    "Old Product": res["old_p"],
                    "Old String Output": merge_results["old_ecdv_output"],
                    "New Product": res["new_p"],
                    "New String Output": merge_results["new_ecdv_output"]
                }
                final_summaries.append(summary_row)

            # Display Final Tabular Data
            st.success("Windows Treatment Applied Successfully!")
            st.title("Final Operations Output")
            final_df = pd.DataFrame(final_summaries)
            
            # Using st.data_editor makes it very easy to view/copy the final result
            st.dataframe(final_df, use_container_width=True)
            
        except Exception as e:
            st.error(f"Error during windows treatment: {str(e)}")
