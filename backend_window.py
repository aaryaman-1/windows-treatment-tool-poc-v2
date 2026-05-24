import pandas as pd
import re
import logging
from itertools import product
from datetime import datetime
from zoneinfo import ZoneInfo

# =========================================================
# CONFIGURATION: Production Logging
# =========================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Industry4.0_Backend")

# =========================================================
# INFINITE SERIES & TIME LOGIC
# =========================================================
def get_index_from_quarter_str(quarter_str: str) -> int:
    quarter_str = quarter_str.strip().upper()
    q_letter = quarter_str[0]
    input_year = int(quarter_str[1:])
    quarter_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    return (input_year - 2020) * 4 + quarter_map[q_letter]

def get_current_quarter_index() -> int:
    french_date_obj = datetime.now(ZoneInfo("Europe/Paris"))
    m, y = french_date_obj.month, french_date_obj.year
    if m <= 3: q_index = 0
    elif m <= 6: q_index = 1
    elif m <= 9: q_index = 2
    else: q_index = 3
    return (y - 2020) * 4 + q_index

def get_index_from_window_element(element: str) -> int:
    clean_el = element.replace("!", "")
    prefix = clean_el[:2]
    val = int(clean_el[2:])
    prefixes = ["W4", "R7", "R0", "R8", "V7", "V8", "V0", "V9"]
    if prefix not in prefixes:
        return -999 
    pos = prefixes.index(prefix)
    block = val - 10 if pos < 4 else val - 9
    return block * 8 + pos

def get_window_elements(quarter_str: str) -> tuple[str, str]:
    idx = get_index_from_quarter_str(quarter_str)
    prefixes = ["W4", "R7", "R0", "R8", "V7", "V8", "V0", "V9"]
    prefix = prefixes[idx % 8]
    base_value = 10 + (idx // 8) if (idx % 8) < 4 else 9 + (idx // 8)
    return f"{prefix}{base_value:02d}", f"{prefix}{(base_value - 1):02d}"

# =========================================================
# STEP 1: PARSING AND EXTRACTION
# =========================================================
def inverse_generate_ecdv(ecdv_string: str) -> tuple[pd.DataFrame, str, str]:
    if not isinstance(ecdv_string, str): return pd.DataFrame(), "", ""
    ecdv_string = ecdv_string.strip()
    if not ecdv_string or ecdv_string == "No combinations for this product line": return pd.DataFrame(), "", ""
    if not ecdv_string.endswith("*"): raise ValueError(f"Invalid ECDV format (missing '*'): {ecdv_string}")
    
    ecdv_string = ecdv_string[:-1]
    match = re.match(r'^([^.]+)\.([A-Za-z0-9]+)(.*)$', ecdv_string)
    if not match: raise ValueError("Invalid ECDV structure.")

    CM, Family, remainder = match.group(1), match.group(2), match.group(3)
    if remainder.startswith("."): remainder = remainder[1:]
    if not remainder: return pd.DataFrame([{}]), CM, Family

    if "<" in remainder:
        common_str, body = remainder.split("<", 1)
        common_parts = re.findall(r"\([A-Z0-9]+[A-Z0-9]{2}\)|[A-Z0-9]+[A-Z0-9]{2}", common_str)
    else:
        common_parts, body = [], remainder

    combinations = body.split("/") if body else []
    parsed_rows = []

    for combo in combinations:
        combo = combo.strip()
        if not combo: continue
        row_dict = {}
        tokens = re.findall(r"\([A-Z0-9]+[A-Z0-9]{2}\)|[A-Z0-9]+[A-Z0-9]{2}", combo)
        for token in tokens:
            is_exception = False
            if token.startswith("("):
                is_exception = True
                token = token[1:-1]
            col, val = token[:-2], token[-2:]
            if is_exception: val = f"!{val}"

            if col in row_dict:
                existing = row_dict[col]
                if not isinstance(existing, list): existing = [existing]
                existing.append(val)
                row_dict[col] = existing
            else:
                row_dict[col] = val
        parsed_rows.append(row_dict)

    if not parsed_rows: return pd.DataFrame([{}]), CM, Family

    for row in parsed_rows:
        for part in common_parts:
            is_exception = False
            if part.startswith("("):
                is_exception = True
                part = part[1:-1]
            col, val = part[:-2], part[-2:]
            if is_exception: val = f"!{val}"
            
            if col in row:
                existing = row[col]
                if not isinstance(existing, list): existing = [existing]
                existing.append(val)
                row[col] = existing
            else:
                row[col] = val

    all_columns = sorted({col for row in parsed_rows for col in row.keys()})
    final_rows = [{col: row.get(col, []) for col in all_columns} for row in parsed_rows]
    return pd.DataFrame(final_rows), CM, Family

def process_index_simultaneously(idx, old_p_list, old_e_list, new_p_list, new_e_list):
    if not (len(old_p_list) == len(old_e_list) == len(new_p_list) == len(new_e_list)): raise ValueError("Critical Error: Input lists length mismatch.")
    old_p, old_e = old_p_list[idx], old_e_list[idx]
    new_p, new_e = new_p_list[idx], new_e_list[idx]

    if bool(old_p) != bool(old_e): raise ValueError(f"Integrity Error at index {idx}: Mismatch in Old Product/ECDV pair.")
    if bool(new_p) != bool(new_e): raise ValueError(f"Integrity Error at index {idx}: Mismatch in New Product/ECDV pair.")

    old_df, old_CM, old_Family = inverse_generate_ecdv(old_e)
    new_df, new_CM, new_Family = inverse_generate_ecdv(new_e)

    CM = old_CM if old_CM else new_CM
    Family = old_Family if old_Family else new_Family
    return old_df, new_df, CM, Family

# =========================================================
# STEP 2: DELTA IDENTIFICATION & WINDOW PRE-EVALUATION
# =========================================================
def align_dataframes(df1: pd.DataFrame, df2: pd.DataFrame):
    if df1.empty and df2.empty: return df1.copy(), df2.copy()
    all_columns = sorted(set(df1.columns).union(set(df2.columns)))
    df1_aligned, df2_aligned = df1.copy(), df2.copy()
    for col in all_columns:
        if col not in df1_aligned.columns: df1_aligned[col] = [[] for _ in range(len(df1_aligned))]
        if col not in df2_aligned.columns: df2_aligned[col] = [[] for _ in range(len(df2_aligned))]
    return df1_aligned[all_columns], df2_aligned[all_columns]

def identify_changed_combinations(old_p: str, old_df: pd.DataFrame, new_p: str, new_df: pd.DataFrame, quarter_str: str):
    if not old_p and not new_p: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), [], []

    # --- 1. PRE-EXISTING WINDOW EXTRACTION ---
    window_cols = {"W4", "R7", "R0", "R8", "V7", "V8", "V0", "V9"}
    present_window_cols = [c for c in old_df.columns if c in window_cols]

    clean_old_df = old_df.copy()
    old_windows = []

    for i, row in old_df.iterrows():
        windows = []
        for col in present_window_cols:
            val = row[col]
            vals = val if isinstance(val, list) else ([val] if pd.notna(val) and val != "" else [])
            for v in vals:
                windows.append(f"{col}{v}")
        old_windows.append(windows)

    if present_window_cols:
        clean_old_df = clean_old_df.drop(columns=present_window_cols)

    # --- 2. DELTA IDENTIFICATION ---
    old_changed_comb, new_changed_comb = align_dataframes(clean_old_df, new_df)
    columns = old_changed_comb.columns
    to_drop_old, to_drop_new = set(), set()

    if old_p and new_p and str(old_p).strip() == str(new_p).strip():
        for i, row1 in old_changed_comb.iterrows():
            for j, row2 in new_changed_comb.iterrows():
                if j in to_drop_new: continue
                is_identical = True
                for col in columns:
                    v1, v2 = row1[col], row2[col]
                    list1 = v1 if isinstance(v1, list) else ([v1] if pd.notna(v1) and v1 != "" else [])
                    list2 = v2 if isinstance(v2, list) else ([v2] if pd.notna(v2) and v2 != "" else [])
                    if set(list1) != set(list2):
                        is_identical = False
                        break
                if is_identical:
                    to_drop_old.add(i)
                    to_drop_new.add(j)
                    break

    # --- 3. EVALUATE PRE-EXISTING WINDOWS ---
    final_old_rows = []
    final_unchanged_rows = []
    case_5_records = []
    case_7_records = []

    given_q_idx = get_index_from_quarter_str(quarter_str)
    curr_open_idx = get_current_quarter_index()

    def reattach_windows(r_series, wins):
        r_dict = r_series.to_dict()
        for w in wins:
            col_name, cell_val = w[:2], w[2:]
            if col_name not in r_dict: r_dict[col_name] = []
            current = r_dict[col_name]
            if not isinstance(current, list):
                current = [current] if pd.notna(current) and current != "" else []
            if cell_val not in current:
                current.append(cell_val)
            r_dict[col_name] = current
        return r_dict

    for i, row in clean_old_df.iterrows():
        windows = old_windows[i]
        case = "Normal"
        c_win_str = o_win_str = ""
        c_win_idx = o_win_idx = -999

        if len(windows) == 1:
            window_str = windows[0]
            is_closing = window_str.startswith("!")
            clean_win = window_str.replace("!", "")
            comb_idx = get_index_from_window_element(clean_win)

            given_open_idx = given_q_idx
            given_close_idx = given_q_idx - 8
            curr_close_idx = curr_open_idx - 8

            if not is_closing:
                if comb_idx > given_open_idx:
                    case = "Case 3"
                elif (given_close_idx < comb_idx < given_open_idx) and (comb_idx >= curr_open_idx - 1) and (given_open_idx - comb_idx <= 4):
                    case = "Case 1"
            else:
                if comb_idx < given_close_idx:
                    if comb_idx >= curr_close_idx:
                        case = "Case 2a"
                    elif comb_idx < curr_close_idx:
                        case = "Case 2b"

        elif len(windows) == 2:
            idx1 = get_index_from_window_element(windows[0])
            idx2 = get_index_from_window_element(windows[1])
            
            # The one that comes first in infinite series is closing
            if idx1 < idx2:
                c_win_str, o_win_str = windows[0], windows[1]
                c_win_idx, o_win_idx = idx1, idx2
            else:
                c_win_str, o_win_str = windows[1], windows[0]
                c_win_idx, o_win_idx = idx2, idx1

            c_win_quarter_idx = c_win_idx + 8
            
            if c_win_quarter_idx < given_q_idx:
                case = "Case 5"
            elif o_win_idx > given_q_idx:
                case = "Case 6"
            elif o_win_idx < given_q_idx < c_win_quarter_idx:
                case = "Case 7"

        is_changed = (i not in to_drop_old)

        if case == "Case 3":
            raise ValueError("SN confirmation needed : Old reference ECDV has an opening window which is after the given quarter in the suivi line.")
        elif case == "Case 6":
            raise ValueError("SN confirmation needed : Old reference ECDV has an opening and closing window which is after the given quarter in the suivi line.")
        elif case == "Case 2b":
            continue 
        elif case == "Case 2a":
            final_unchanged_rows.append(reattach_windows(row, windows))
        elif case == "Case 5":
            if not is_changed:
                final_unchanged_rows.append(reattach_windows(row, windows))
            else:
                case_5_records.append({
                    'row_clean': row.to_dict(),
                    'c_win_str': c_win_str,
                    'o_win_str': o_win_str,
                    'c_win_idx': c_win_idx,
                    'display_dict': reattach_windows(row, windows)
                })
        elif case == "Case 7":
            if not is_changed:
                final_unchanged_rows.append(reattach_windows(row, windows))
            else:
                is_ecdv_mod = (old_p and new_p and str(old_p).strip() == str(new_p).strip())
                is_cancel = (old_p and not new_p)
                is_cancel_replace = (old_p and new_p and str(old_p).strip() != str(new_p).strip())
                
                if is_ecdv_mod or is_cancel:
                    final_old_rows.append(reattach_windows(row, [o_win_str]))
                elif is_cancel_replace:
                    identical_in_new = False
                    matching_j = -1
                    
                    for j, row2 in new_changed_comb.iterrows():
                        is_identical = True
                        for col in columns:
                            v1, v2 = row[col], row2[col]
                            list1 = v1 if isinstance(v1, list) else ([v1] if pd.notna(v1) and v1 != "" else [])
                            list2 = v2 if isinstance(v2, list) else ([v2] if pd.notna(v2) and v2 != "" else [])
                            if set(list1) != set(list2):
                                is_identical = False
                                break
                        if is_identical:
                            identical_in_new = True
                            matching_j = j
                            break
                    
                    if identical_in_new:
                        case_7_records.append({
                            'row_clean': row.to_dict(),
                            'c_win_str': c_win_str,
                            'o_win_str': o_win_str,
                            'matching_j': matching_j,
                            'display_dict': reattach_windows(row, windows)
                        })
                    else:
                        final_old_rows.append(reattach_windows(row, [o_win_str]))

        elif case == "Case 1" or case == "Normal":
            if is_changed:
                final_old_rows.append(reattach_windows(row, windows))
            else:
                final_unchanged_rows.append(reattach_windows(row, windows))

    final_old = pd.DataFrame(final_old_rows).reset_index(drop=True) if final_old_rows else pd.DataFrame(columns=old_df.columns)
    final_unchanged = pd.DataFrame(final_unchanged_rows).reset_index(drop=True) if final_unchanged_rows else pd.DataFrame(columns=old_df.columns)
    
    final_new = new_changed_comb.drop(index=list(to_drop_new)).reset_index(drop=True)
    if final_new.empty: final_new = pd.DataFrame(columns=new_df.columns)

    return final_old, final_new, final_unchanged, case_5_records, case_7_records


# =========================================================
# PROCESS CASE 5 & CASE 7 USER DECISIONS
# =========================================================
def inject_windows_to_dict(r_dict, wins):
    res = r_dict.copy()
    for w in wins:
        col_name, cell_val = w[:2], w[2:]
        if col_name not in res: res[col_name] = []
        current = res[col_name]
        if not isinstance(current, list):
            current = [current] if pd.notna(current) and current != "" else []
        if cell_val not in current:
            current.append(cell_val)
        res[col_name] = current
    return res

def process_case_5_decisions(case_5_records: list, yn_answers: list):
    new_final_old = []
    new_final_unchanged = []
    curr_close_idx = get_current_quarter_index() - 8

    for i, record in enumerate(case_5_records):
        ans = str(yn_answers[i]).strip().upper() if i < len(yn_answers) and yn_answers[i] else ""
        row_clean = record['row_clean']
        c_win_str, o_win_str = record['c_win_str'], record['o_win_str']
        
        if ans == 'N':
            new_final_old.append(inject_windows_to_dict(row_clean, [o_win_str]))
        elif ans == 'Y':
            if record['c_win_idx'] >= curr_close_idx:
                new_final_unchanged.append(inject_windows_to_dict(row_clean, [c_win_str, o_win_str]))

    df_old_add = pd.DataFrame(new_final_old)
    df_unchanged_add = pd.DataFrame(new_final_unchanged)
    return df_old_add, df_unchanged_add

def inject_windows_to_dict(r_dict, wins):
    """
    Safely injects window elements into a row dictionary as list values.
    """
    res = r_dict.copy()
    for w in wins:
        col_name, cell_val = w[:2], w[2:]
        if col_name not in res: 
            res[col_name] = []
        current = res[col_name]
        if not isinstance(current, list):
            current = [current] if pd.notna(current) and current != "" else []
        if cell_val not in current:
            current.append(cell_val)
        res[col_name] = current
    return res

def process_case_7_decisions(case_7_records: list, yn_answers: list, final_new_df: pd.DataFrame):
    """
    Processes the Y/N answers from the user for Case 7 rows.
    If 'N': Merges only the opening window to final_old.
    If 'Y': Merges opening window to final_old AND closing window to the identical new_df row.
    """
    new_final_old = []
    
    for i, record in enumerate(case_7_records):
        ans = str(yn_answers[i]).strip().upper() if i < len(yn_answers) and yn_answers[i] else ""
        row_clean = record['row_clean']
        c_win_str, o_win_str = record['c_win_str'], record['o_win_str']
        matching_j = record['matching_j']
        
        if ans == 'N':
            # Case 7(c1) User entry 'N': Merge only with opening window element
            new_final_old.append(inject_windows_to_dict(row_clean, [o_win_str]))
        elif ans == 'Y':
            # Case 7(c1) User entry 'Y': Merge opening window element to old row
            new_final_old.append(inject_windows_to_dict(row_clean, [o_win_str]))
            
            # AND merge the closing window element to its identical row in final_new
            if matching_j < len(final_new_df):
                row_dict = final_new_df.iloc[matching_j].to_dict()
                row_dict = inject_windows_to_dict(row_dict, [c_win_str])
                final_new_df.loc[matching_j] = pd.Series(row_dict)
                
    df_old_add = pd.DataFrame(new_final_old)
    return df_old_add, final_new_df


# =========================================================
# STEP 3: WINDOW INJECTION & MERGING
# =========================================================
def apply_window_elements(
    final_old, final_new, DAN_date, 
    old_versions_start_date, old_below_above, 
    new_versions_start_date, new_below_above, 
    opening_window, closing_window
):
    if len(final_old) != len(old_versions_start_date) or len(final_old) != len(old_below_above):
        raise ValueError(f"Length mismatch: final_old({len(final_old)}) vs inputs.")
    if len(final_new) != len(new_versions_start_date) or len(final_new) != len(new_below_above):
        raise ValueError(f"Length mismatch: final_new({len(final_new)}) vs inputs.")

    df_old, df_new = final_old.copy(), final_new.copy()
    below_flags = {'B', 'b', 'below', 'Below', 'BELOW'}

    def inject_window(df, row_idx, window_str):
        if not window_str or len(window_str) < 3: return
        col_name, cell_val = window_str[:2], window_str[2:]
        if col_name not in df.columns: df[col_name] = [[] for _ in range(len(df))]
        current_cell = df.at[row_idx, col_name]
        if not isinstance(current_cell, list):
            current_cell = [current_cell] if pd.notna(current_cell) and current_cell != "" else []
        if cell_val not in current_cell:
            new_list = current_cell.copy()
            new_list.append(cell_val)
            df.at[row_idx, col_name] = new_list

    parsed_DAN = None
    if DAN_date:
        if isinstance(DAN_date, str):
            try: parsed_DAN = datetime.strptime(DAN_date, "%Y-%m-%d")
            except: pass
        else:
            parsed_DAN = DAN_date

    def is_below_logic(flag_val, d_val, dan):
        if flag_val is not None and str(flag_val).strip() in below_flags: return True
        if d_val and dan:
            try:
                parsed_d = datetime.strptime(str(d_val).strip(), "%Y-%m-%d") if isinstance(d_val, str) else d_val
                if parsed_d < dan: return True
            except: pass
        return False

    for i in range(len(df_old)):
        if is_below_logic(old_below_above[i], old_versions_start_date[i], parsed_DAN):
            inject_window(df_old, i, closing_window)

    for i in range(len(df_new)):
        if is_below_logic(new_below_above[i], new_versions_start_date[i], parsed_DAN):
            inject_window(df_new, i, opening_window)

    return df_old, df_new

def filter_old_combinations(df_old: pd.DataFrame, closing_window: str) -> pd.DataFrame:
    if df_old.empty or not closing_window or len(closing_window) < 3:
        return pd.DataFrame(columns=df_old.columns)
    col_name, cell_val = closing_window[:2], closing_window[2:]
    if col_name not in df_old.columns:
        return pd.DataFrame(columns=df_old.columns)
    
    mask = df_old[col_name].apply(lambda cell: isinstance(cell, list) and cell_val in cell)
    return df_old[mask].reset_index(drop=True)

# =========================================================
# GENERATE ECDV
# =========================================================
def generate_ecdv(df: pd.DataFrame, CM: str, Family: str) -> str:
    if df.empty: return ""
    df = df.copy()

    VT_CM_MAP = {'CJ': '09', '88': '02', '89': '01', '82': '04', 'FV': '07', 'FL': '11', 'EL': '49', 'EN': '47', 'GL': '48', 'RL': '46', 'VB': '36', 'VN': '44', '76': '21'}

    if 'VT' in df.columns:
        expected_vt = VT_CM_MAP.get(str(CM))
        if expected_vt is None: raise ValueError(f"CM '{CM}' not defined in VT mapping.")
        def valid_VT(val):
            if isinstance(val, list): return len(val) == 0
            if pd.isna(val): return True
            return str(val).zfill(2) == expected_vt
        df = df[df['VT'].apply(valid_VT)]
        df = df.drop(columns=['VT'])

    if 'A' in df.columns:
        expected_A = str(Family[0]).zfill(2)
        def valid_A(val):
            if isinstance(val, list): return len(val) == 0
            if pd.isna(val): return True
            return str(val).zfill(2) == expected_A
        df = df[df['A'].apply(valid_A)]
        df = df.drop(columns=['A'])

    if 'C' in df.columns:
        expected_C = Family[2:4]
        def valid_C(val):
            if isinstance(val, list): return len(val) == 0
            if pd.isna(val): return True
            return str(val) == expected_C
        df = df[df['C'].apply(valid_C)]
        df = df.drop(columns=['C'])

    family_second_char = Family[1] if len(Family) > 1 else ""
    valid_values = {"01", "0V"} if family_second_char == "G" else {f"0{family_second_char}"}

    for col in ['B', 'ZZ']:
        if col in df.columns:
            def valid_B(val):
                if isinstance(val, list): return len(val) == 0
                if pd.isna(val): return True
                return str(val).zfill(2) in valid_values
            df = df[df[col].apply(valid_B)]
            if col == 'B': df = df.drop(columns=['B'])

    def normalize_value(v):
        s = str(v)
        return s.zfill(2) if s.isdigit() and len(s) == 1 else s

    common_parts, non_common_columns = [], []

    for col in df.columns:
        column_values = df[col].tolist()
        normalized_rows = []
        for val in column_values:
            if isinstance(val, list): normalized_rows.append([normalize_value(v) for v in val])
            elif pd.isna(val): normalized_rows.append([])
            else: normalized_rows.append([normalize_value(val)])

        if not normalized_rows: continue
        common_elements = set(normalized_rows[0]).intersection(*[set(r) for r in normalized_rows[1:]])

        if common_elements:
            for el in sorted(list(common_elements)):
                if el.startswith("!"): common_parts.append(f"({col}{el[1:]})")
                else: common_parts.append(f"{col}{el}")
            new_col_values = []
            has_leftovers = False
            for r in normalized_rows:
                leftovers = [v for v in r if v not in common_elements]
                new_col_values.append(leftovers)
                if leftovers: has_leftovers = True
            df[col] = new_col_values
            if has_leftovers: non_common_columns.append(col)
        else:
            df[col] = normalized_rows
            if any(normalized_rows): non_common_columns.append(col)

    result = []
    for row_index, row in df.iterrows():
        values = []
        for col in non_common_columns:
            val = row[col]
            if isinstance(val, list):
                if len(val) == 0: continue
            else:
                if pd.isna(val): continue
                val = [val]
            val = [normalize_value(v) for v in val]
            normal_vals = [v for v in val if not v.startswith("!")]
            exception_vals = [v for v in val if v.startswith("!")]
            if normal_vals and exception_vals: raise ValueError(f"Mixed include/exclude in col '{col}'")
            if exception_vals:
                grouped = "".join(f"({col}{v[1:]})" for v in exception_vals)
                values.append([grouped])
            elif normal_vals:
                values.append([f"{col}{v}" for v in normal_vals])

        if not values: continue
        for combo in product(*values):
            formatted = ""
            for part in combo:
                if part.startswith("("): formatted += part
                else:
                    if formatted and not formatted.endswith(")"): formatted += "."
                    formatted += part
            result.append(formatted)

    body = "/".join(result)

    def build_common_string(parts):
        formatted = ""
        for part in parts:
            if part.startswith("("): formatted += part
            else:
                if formatted and not formatted.endswith(")"): formatted += "."
                formatted += part
        return formatted

    common_str = build_common_string(common_parts)
    if not common_parts and not body: return "No combinations for this product line"

    first_char = common_str[0] if common_parts else (body[0] if body else "")
    prefix = f"{CM}.{Family}" if first_char == "(" else f"{CM}.{Family}."

    if common_parts and body:
        return f"{prefix}{common_str}<{body}*" if len(result) > 1 else f"{prefix}{common_str}{body}*"
    elif common_parts and not body:
        return f"{prefix}{common_str}*"
    else:
        return f"{prefix}{body}*"

def execute_step_3_merging(old_p: str, df_old: pd.DataFrame, new_p: str, df_new: pd.DataFrame, final_unchanged: pd.DataFrame, CM: str, Family: str) -> dict:
    old_exists = bool(old_p and str(old_p).strip())
    new_exists = bool(new_p and str(new_p).strip())
    
    results = {"old_ecdv_output": None, "new_ecdv_output": None, "case_executed": None}

    if old_exists and not new_exists:
        results["old_ecdv_output"] = generate_ecdv(df_old, CM, Family)
        results["case_executed"] = "Case 4 (Cancellation)"
    elif new_exists and not old_exists:
        results["new_ecdv_output"] = generate_ecdv(df_new, CM, Family)
        results["case_executed"] = "Case 3 (Creation)"
    elif old_exists and new_exists:
        if str(old_p).strip() != str(new_p).strip():
            results["old_ecdv_output"] = generate_ecdv(df_old, CM, Family)
            results["new_ecdv_output"] = generate_ecdv(df_new, CM, Family)
            results["case_executed"] = "Case 1 (Cancel and Replace)"
        else:
            df_merged = pd.concat([final_unchanged, df_old, df_new], ignore_index=True)
            results["new_ecdv_output"] = generate_ecdv(df_merged, CM, Family)
            results["case_executed"] = "Case 2 (ECDV Modification)"
    
    return results

# ==================================================
# UI FORMATTING HELPER FUNCTIONS
# ==================================================
def format_cell_for_display(value):
    if isinstance(value, list):
        if len(value) == 0:
            return ""
        lines = []
        for i, v in enumerate(value):
            if isinstance(v, str) and v.startswith("!"):
                v = v[1:]
            prefix = "" if i == 0 else "+"
            lines.append(f"{prefix}({v})")
        return "\n".join(lines)
    if pd.isna(value): return ""
    s = str(value)
    if s.startswith("!"): return f"({s[1:]})"
    return s

def format_dataframe_for_display(df):
    display_df = df.copy()
    for col in display_df.columns:
        display_df[col] = display_df[col].apply(format_cell_for_display)
    return display_df

def format_for_data_editor(df: pd.DataFrame, is_case_5=False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    rows = []
    for _, row in df.iterrows():
        row_elements = []
        for col in df.columns:
            raw_val = row[col]
            if isinstance(raw_val, list):
                vals = [str(v).replace("!", "") for v in raw_val if str(v).strip() not in ["", "[]"]]
                val = ",".join(vals) if vals else ""
            else:
                val = str(raw_val).strip()
                if val.startswith("!"):
                    val = val[1:]
            
            if val not in ["[]", "['']", "", "nan", "None", "NaN"]:
                row_elements.append(f"{col}{val}")
        rows.append(row_elements)
        
    display_df = pd.DataFrame(rows)
    display_df.columns = display_df.columns.astype(str)
    
    if is_case_5:
        display_df["Y/N"] = None
    else:
        display_df["below or above"] = None
        display_df["versions start date"] = None
        
    return display_df
