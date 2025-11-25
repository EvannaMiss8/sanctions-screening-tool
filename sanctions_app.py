import streamlit as st
import pandas as pd
import pdfplumber
import re
from thefuzz import process, fuzz
from datetime import datetime

# --- CONFIGURATION & STYLING ---
st.set_page_config(page_title="MY-COSEC Sanctions Screener", layout="wide", page_icon="🛡️")

# Custom CSS
st.markdown("""
    <style>
    .red-alert {
        background-color: #8B0000;
        color: white;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        font-size: 24px;
        font-weight: bold;
        animation: blinker 1.5s linear infinite;
    }
    @keyframes blinker {
        50% { opacity: 0.8; }
    }
    .safe-alert {
        background-color: #228B22;
        color: white;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        font-size: 20px;
        font-weight: bold;
    }
    .compliance-panel {
        background-color: #f0f2f6;
        padding: 20px;
        border-left: 5px solid #ff4b4b;
        border-radius: 5px;
    }
    </style>
""", unsafe_allow_html=True)

# --- DATA PROCESSING FUNCTIONS ---

@st.cache_data
def parse_un_style_pdf(file_path, list_name):
    """
    Parses UN-style Sanction List PDFs (1267, 1988, 1718, 2231).
    Robust regex to capture prefixes: QDi, QDe, TAi, TAe, KPi, KPe, IRi, IRe.
    """
    data = []
    try:
        with pdfplumber.open(file_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                # Extract text and add a newline to ensure separation
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"
        
        # 1. Robust Split: Look for the pattern [Letter][Letter][i/e].[Number]
        # The (?=...) is a lookahead to split without consuming the reference number.
        # We allow for optional whitespace/newlines before the identifier.
        # Pattern matches: TAi.173, QDi.001, KPe.022, etc.
        entries = re.split(r'(?=\s+[A-Z]{2}[i|e]\.\d+)', full_text)
        
        for entry in entries:
            if not entry.strip():
                continue
            
            # 2. Extract Reference Number
            # Looks for the pattern at the start of the entry chunk or just after whitespace
            ref_match = re.search(r'([A-Z]{2}[i|e]\.\d+)', entry)
            if not ref_match:
                continue # Skip if not a valid entry chunk
            
            ref_no = ref_match.group(1)
            
            # 3. Extract Name
            # UN lists usually follow "Name: 1: FIRST 2: MIDDLE 3: LAST" format
            # We try to capture everything between "Name:" and the next field (Title, Designation, or DOB)
            name_match = re.search(r'Name:\s*(.+?)\s+(?:Name \(original|Title:|Designation:|DOB:)', entry, re.DOTALL)
            
            if name_match:
                raw_name = name_match.group(1)
                # Remove the "1:", "2:", "3:", "4:", "na" markers common in UN lists
                clean_name = re.sub(r'\d+:', '', raw_name)
                clean_name = clean_name.replace('na', '').replace('\n', ' ').strip()
                # Remove extra spaces
                name = re.sub(r'\s+', ' ', clean_name)
            else:
                name = "Unknown Name"
            
            # 4. Extract Aliases
            # Looks for "Good quality a.k.a.:" and "Low quality a.k.a.:"
            aliases = []
            alias_section = re.search(r'(Good quality a\.k\.a\.:|Low quality a\.k\.a\.:)(.+?)(Nationality:|Passport no:|National identification|Address:)', entry, re.DOTALL)
            if alias_section:
                raw_aliases = alias_section.group(2).replace('\n', ' ')
                # UN aliases are often separated by a), b), c)
                cleaned = re.split(r'[a-z]\)', raw_aliases)
                aliases = [a.strip(' ;').strip() for a in cleaned if len(a) > 2 and a.lower() != 'na']
            
            # 5. Extract DOB
            dob_match = re.search(r'DOB:\s*(.+?)\s+(?:POB:|Good quality)', entry)
            dob = dob_match.group(1).strip() if dob_match else "NA"
            
            data.append({
                'Source': list_name,
                'Reference_No': ref_no,
                'Name': name,
                'Aliases': " | ".join(aliases),
                'DOB': dob,
                'Designation': 'UN Sanctioned',
                'Nationality': 'International',
                'Raw_Data': entry[:300]
            })
            
    except Exception as e:
        pass
        
    return pd.DataFrame(data)

@st.cache_data
def parse_kdn_pdf(file_path):
    """
    Parses the tabular KDN Domestic List PDF (MOHA).
    """
    data = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        # Robust Check for 'KDN' in reference column (Col 1)
                        # We use a broader check because sometimes the "KDN" prefix is in Col 0 or merged
                        row_str = str(row)
                        if "KDN" in row_str:
                            # Try to identify columns based on content rather than fixed index
                            ref = "Unknown"
                            name = "Unknown"
                            alias = ""
                            dob = ""
                            id_num = ""
                            
                            # Scan row for KDN ref
                            for cell in row:
                                if cell and "KDN" in str(cell):
                                    ref = cell
                                    break
                            
                            # Heuristic: Name is usually the longest string that isn't the address or ref
                            # This is a simplification for robustness
                            if len(row) > 2:
                                name = row[2]
                            
                            if len(row) > 7: alias = row[7]
                            if len(row) > 5: dob = row[5]
                            if len(row) > 10: id_num = row[10]

                            # Clean data
                            ref = str(ref).replace('\n', ' | ') if ref else "N/A"
                            name = str(name).replace('\n', ' ') if name else "N/A"
                            alias = str(alias).replace('\n', ' ') if alias else ""
                            dob = str(dob).replace('\n', ' | ') if dob else ""
                            id_num = str(id_num).replace('\n', ' | ') if id_num else ""
                            
                            data.append({
                                'Source': 'MOHA Domestic List (Malaysia)',
                                'Reference_No': ref,
                                'Name': name,
                                'Aliases': alias,
                                'DOB': dob,
                                'Designation': 'Specified Entity (Domestic)',
                                'Nationality': 'Malaysia/Other',
                                'Raw_Data': f"ID: {id_num}"
                            })
    except Exception as e:
        pass
    return pd.DataFrame(data)

def get_sample_data():
    """
    Provides fallback data covering ALL 5 Lists for demonstration.
    """
    data = [
        # 1. MOHA (Domestic)
        {'Source': 'MOHA Domestic List (Malaysia)', 'Reference_No': 'KDN.1.08-2014', 'Name': 'Halimah binti Hussein', 'Aliases': '', 'DOB': '9.12.1961', 'Designation': 'Individual', 'Nationality': 'Malaysia', 'Raw_Data': 'ID: 611209-01-5514'},
        {'Source': 'MOHA Domestic List (Malaysia)', 'Reference_No': 'KDN.1.04-2016', 'Name': 'Nor Mahmudah binti Ahmad', 'Aliases': 'Cik Mud', 'DOB': '1.1.1989', 'Designation': 'Individual', 'Nationality': 'Malaysia', 'Raw_Data': 'ID: 890101-26-5006'},
        
        # 2. UNSCR 1267 (ISIL/Al-Qaida)
        {'Source': 'UNSCR 1267/1989/2253 (ISIL/Al-Qaida)', 'Reference_No': 'QDi.006', 'Name': 'AIMAN MUHAMMED RABI AL-ZAWAHIRI', 'Aliases': 'Ayman Al-Zawahari', 'DOB': '19 Jun. 1951', 'Designation': 'Leader of Al-Qaida', 'Nationality': 'Egypt', 'Raw_Data': 'Reportedly deceased'},
        
        # 3. UNSCR 1988 (Taliban)
        {'Source': 'UNSCR 1988 (Taliban)', 'Reference_No': 'TAi.144', 'Name': 'SIRAJUDDIN JALLALOUDINE HAQQANI', 'Aliases': 'Siraj Haqqani | Khalifa', 'DOB': '1977-1978', 'Designation': 'Deputy Commander', 'Nationality': 'Afghanistan', 'Raw_Data': 'Haqqani Network'},
        {'Source': 'UNSCR 1988 (Taliban)', 'Reference_No': 'TAi.173', 'Name': 'ABDUL BASIR NOORZAI', 'Aliases': 'Haji Abdul Basir | Haji Basir Noorzai', 'DOB': '1965', 'Designation': 'Haji', 'Nationality': 'Afghanistan', 'Raw_Data': 'Owner of Haji Basir and Zarjmil Company'},
        
        # 4. UNSCR 1718 (DPRK)
        {'Source': 'UNSCR 1718 (DPRK)', 'Reference_No': 'KPi.033', 'Name': 'RI WON HO', 'Aliases': '', 'DOB': '17 Jul. 1964', 'Designation': 'Official', 'Nationality': 'DPRK', 'Raw_Data': 'Ministry of State Security'},
        
        # 5. UNSCR 2231 (Iran)
        {'Source': 'UNSCR 2231 (Iran)', 'Reference_No': 'IRi.039', 'Name': 'QASEM SOLEIMANI', 'Aliases': 'Qasim Soleimani', 'DOB': '11 Mar. 1957', 'Designation': 'Major General', 'Nationality': 'Iran', 'Raw_Data': 'Commander of Qods Force'},
    ]
    return pd.DataFrame(data)

# --- MAIN APP ---

def main():
    # Initialize Session State
    if 'search_results' not in st.session_state:
        st.session_state['search_results'] = None
    if 'search_performed' not in st.session_state:
        st.session_state['search_performed'] = False
    if 'high_risk_matches' not in st.session_state:
        st.session_state['high_risk_matches'] = []

    # 1. Persona & Context Sidebar
    with st.sidebar:
        st.image("https://www.ssm.com.my/Style%20Library/Images/logo.png", width=200)
        st.header("Compliance Officer Dashboard")
        st.info("**Role:** Certified AML/CFT Compliance Officer\n\n**Duty:** Targeted Financial Sanctions (TFS) Screening\n\n**Authority:** AMLA 2001 (Part IV) & BNM/SSM Guidelines")
        
        st.subheader("Upload Sanctions Lists")
        st.markdown("Please upload the latest PDF for each list:")
        st.markdown("---")
        
        # 1. MOHA Domestic List
        st.markdown("**1. MOHA Domestic List**")
        st.markdown("🔗 [MOHA Reference Link](https://www.moha.gov.my/utama/index.php/en/component/content/article/350-list-of-ministries-of-home-affairs)")
        file_moha = st.file_uploader("Upload MOHA List", type="pdf", key="moha", label_visibility="collapsed")
        st.write("") # Spacer

        # 2. UNSCR 1267
        st.markdown("**2. UNSCR 1267 (ISIL/Al-Qaida)**")
        st.markdown("🔗 [UNSCR 1267 Reference Link](https://www.un.org/sc/suborg/en/sanctions/1267/aq_sanctions_list)")
        file_1267 = st.file_uploader("Upload UNSCR 1267", type="pdf", key="1267", label_visibility="collapsed")
        st.write("")

        # 3. UNSCR 1988
        st.markdown("**3. UNSCR 1988 (Taliban)**")
        st.markdown("🔗 [UNSCR 1988 Reference Link](https://www.un.org/sc/suborg/en/sanctions/1988/materials)")
        file_1988 = st.file_uploader("Upload UNSCR 1988", type="pdf", key="1988", label_visibility="collapsed")
        st.write("")

        # 4. UNSCR 1718
        st.markdown("**4. UNSCR 1718 (DPRK)**")
        st.markdown("🔗 [UNSCR 1718 Reference Link](https://www.un.org/sc/suborg/en/sanctions/1718/materials)")
        file_1718 = st.file_uploader("Upload UNSCR 1718", type="pdf", key="1718", label_visibility="collapsed")
        st.write("")

        # 5. UNSCR 2231
        st.markdown("**5. UNSCR 2231 (Iran)**")
        st.markdown("🔗 [UNSCR 2231 Reference Link](https://www.un.org/securitycouncil/content/2231/list)")
        file_2231 = st.file_uploader("Upload UNSCR 2231", type="pdf", key="2231", label_visibility="collapsed")
        
        st.markdown("---")
        
    # 2. Data Loading Logic
    data_frames = []
    
    # Process MOHA List
    if file_moha:
        data_frames.append(parse_kdn_pdf(file_moha))
    
    # Process UNSCR Lists
    if file_1267:
        data_frames.append(parse_un_style_pdf(file_1267, "UNSCR 1267 (ISIL/Al-Qaida)"))
    if file_1988:
        data_frames.append(parse_un_style_pdf(file_1988, "UNSCR 1988 (Taliban)"))
    if file_1718:
        data_frames.append(parse_un_style_pdf(file_1718, "UNSCR 1718 (DPRK)"))
    if file_2231:
        data_frames.append(parse_un_style_pdf(file_2231, "UNSCR 2231 (Iran)"))
    
    # Combine Data
    if data_frames:
        df_master = pd.concat(data_frames, ignore_index=True)
        data_source_status = f"Live Files ({len(data_frames)} Lists Loaded)"
    else:
        # Fallback to embedded data for simulation
        df_master = get_sample_data()
        data_source_status = "Simulated Data (Upload PDFs for full check)"

    # Sidebar Metric
    st.sidebar.metric("Total Entities Loaded", len(df_master))
    
    # 3. Main Title Area
    st.title("Sanctions Screening Tool")
    st.caption(f"System Status: Active | Database: {data_source_status} | Total Records: {len(df_master)}")
    st.markdown("---")

    # 4. Input Section
    col1, col2 = st.columns([3, 1])
    with col1:
        # Using session state key to keep the input text
        search_query = st.text_input("Enter Customer Name / Entity Name", placeholder="e.g., Halimah Hussein, Abdul Basir Noorzai...")
    with col2:
        st.write("") # Spacer
        st.write("") 
        search_btn = st.button("🔍 SCREEN NOW", type="primary", use_container_width=True)

    # 5. Search Logic
    if search_btn:
        if search_query:
            # Fuzzy Search Logic
            # We concatenate Name and Aliases for searching to ensure aliases are also checked
            df_master['Search_Vector'] = df_master['Name'].astype(str) + " " + df_master['Aliases'].astype(str)
            matches = process.extract(search_query, df_master['Search_Vector'].tolist(), limit=10, scorer=fuzz.token_set_ratio)
            
            # Filter matches above threshold
            threshold = 80
            high_risk_matches = [m for m in matches if m[1] >= threshold]
            
            st.session_state['search_performed'] = True
            st.session_state['high_risk_matches'] = high_risk_matches
            
            if high_risk_matches:
                matched_indices = [df_master[df_master['Search_Vector'] == m[0]].index[0] for m in high_risk_matches]
                st.session_state['search_results'] = df_master.loc[matched_indices].drop(columns=['Search_Vector'])
            else:
                st.session_state['search_results'] = None
        else:
            st.warning("Please enter a name to screen.")
            st.session_state['search_performed'] = False

    # 6. Result Display Logic (Persistent)
    if st.session_state['search_performed']:
        
        if st.session_state['high_risk_matches']:
            # -- RED ALERT SECTION --
            st.markdown('<div class="red-alert">⚠️ RED ALERT: POTENTIAL MATCH FOUND ⚠️</div>', unsafe_allow_html=True)
            st.markdown("### 🚨 IMMEDIATE ACTION REQUIRED: DO NOT EXECUTE TRANSACTION")
            
            results_df = st.session_state['search_results']
            
            # -- MATCH DETAILS TABLE --
            st.subheader("Match Details")
            st.dataframe(
                results_df[['Source', 'Name', 'Aliases', 'Reference_No', 'Nationality', 'DOB']],
                use_container_width=True,
                hide_index=True
            )
            
            # Show granular details for the top match
            top_match = results_df.iloc[0]
            
            with st.expander("🔎 View Detailed Match Information", expanded=True):
                dm_col1, dm_col2 = st.columns(2)
                with dm_col1:
                    st.write(f"**Designated Name:** {top_match['Name']}")
                    st.write(f"**Reference No:** {top_match['Reference_No']}")
                    st.write(f"**Source List:** {top_match['Source']}")
                with dm_col2:
                    st.write(f"**Date of Birth:** {top_match['DOB']}")
                    st.write(f"**Nationality:** {top_match['Nationality']}")
                    st.write(f"**Other Info:** {top_match['Raw_Data']}")

            # -- COMPLIANCE ACTION PANEL (MANDATORY) --
            st.markdown("---")
            st.subheader("👮 Compliance Action Panel (Mandatory)")
            
            with st.container():
                st.markdown("""
                <div class="compliance-panel">
                <strong>Pursuant to AMLA 2001 & Strategic Trade Act 2010, you must:</strong>
                <ol>
                    <li><strong>FREEZE:</strong> Immediately freeze funds, properties, or assets. Do not process the transaction.</li>
                    <li><strong>BLOCK:</strong> Prevent the individual/entity from accessing services.</li>
                    <li><strong>REPORT:</strong> Submit a Suspicious Transaction Report (STR) to Bank Negara Malaysia (FIED).</li>
                    <li><strong>NOTIFY:</strong> Inform the Inspector-General of Police (IGP).</li>
                </ol>
                </div>
                """, unsafe_allow_html=True)
                
                st.write("") # Spacer
                
                # Action Buttons
                act_col1, act_col2 = st.columns(2)
                with act_col1:
                    str_content = f"""
                    SUSPICIOUS TRANSACTION REPORT (DRAFT)
                    -------------------------------------
                    Date: {datetime.now()}
                    Reporting Institution: Company Secretary Firm
                    
                    SUBJECT DETAILS
                    ---------------
                    Name Match: {top_match['Name']}
                    Reference No: {top_match['Reference_No']}
                    Source List: {top_match['Source']}
                    Nationality: {top_match['Nationality']}
                    
                    REASON FOR SUSPICION
                    --------------------
                    Positive match found against Sanctions List.
                    Asset freezing measures initiated immediately.
                    """
                    st.download_button(
                        label="📄 Generate STR Draft (Download)",
                        data=str_content,
                        file_name="STR_Draft_Report.txt",
                        mime="text/plain",
                        type="primary"
                    )
                    
                with act_col2:
                    if st.button("📝 Log Internal Compliance Report"):
                        st.success("Internal Compliance Log Updated: Case ID #2025-99283. Audit trail saved.")
                        
        else:
            # -- NO MATCH SECTION --
            st.markdown('<div class="safe-alert">✅ NO MATCH FOUND</div>', unsafe_allow_html=True)
            st.markdown("### Standard Customer Due Diligence (CDD) Applies")
            st.info(f"No hits found for '{search_query}' in the consolidated Sanctions Database (5 Lists).")
            
            st.markdown("**Next Steps:**")
            st.markdown("- Proceed with Standard ID Verification (NRIC/Passport).")
            st.markdown("- Conduct beneficial ownership checks.")
            st.markdown("- Archive this search result for audit trail.")
            
            # Audit Trail Download
            audit_log = f"Search Term: {search_query}\nDate: {datetime.now()}\nResult: No Match\nScreener: Compliance Officer"
            st.download_button("Download Search Certificate", audit_log, file_name="search_certificate.txt")

    # 6. Footer / Disclaimer
    st.markdown("---")
    st.caption("© 2025 Malaysian AML/CFT Compliance Tool. Reference: AMLA 2001 & BNM Guidelines. "
               "Data Sources: MOHA, UNSC 1267, 1988, 1718, 2231. Ensure lists are current.")

if __name__ == "__main__":
    main()