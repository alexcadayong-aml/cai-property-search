"""
Google Sheets Client & Synchronizer
====================================
Handles authentication, permission verification, and data retrieval from Google Sheets.
Supports:
  - Google Service Account (Recommended for automated scripts/servers)
  - Google OAuth 2.0
  - Public export / CSV fallback
  - Local CSV fallback when credentials/permissions are pending
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("SheetsClient")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]


def parse_date_updated(val: Any) -> Tuple[str, str]:
    """
    Parses various date formats into (iso_str, display_str).
    Handles:
      - '12-Aug-23' -> ('2023-08-12', 'Aug 12, 2023')
      - 'September 12, 2025' -> ('2025-09-12', 'Sep 12, 2025')
      - '45915' (Excel serial) -> ('2025-09-15', 'Sep 15, 2025')
      - '2026-06-05 00:00:00' -> ('2026-06-05', 'Jun 05, 2026')
    """
    if not val or not str(val).strip():
        return "", ""
    s = str(val).strip()
    if s.lower() in ["nan", "none", ".", "null", "-", "na", "n/a"]:
        return "", ""

    # Clean float string representation: '45915.0' -> '45915'
    if re.match(r"^\d+\.0$", s):
        s = s.split(".")[0]

    # Excel serial number (35000 is ~1995, 65000 is ~2078)
    if s.isdigit():
        try:
            n = int(s)
            if 35000 <= n <= 65000:
                dt = datetime(1899, 12, 30) + timedelta(days=n)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%b %d, %Y")
        except Exception:
            pass

    # Clean timestamp suffix: e.g. '2026-06-05 00:00:00' -> '2026-06-05'
    if " " in s and re.match(r"^\d{4}-\d{2}-\d{2}", s):
        s = s.split(" ")[0]

    formats = [
        "%d-%b-%y", "%d-%b-%Y", "%d-%B-%y", "%d-%B-%Y",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d",
        "%d.%m.%Y", "%d-%m-%Y", "%m-%d-%Y"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year < 100:
                dt = dt.replace(year=dt.year + 2000)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%b %d, %Y")
        except ValueError:
            pass

    # Regex fallback for Month DD, YYYY
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d"), dt.strftime("%b %d, %Y")
        except ValueError:
            pass

    return "", s


def extract_spreadsheet_id(url_or_id: str) -> str:
    """Extract Google Spreadsheet ID from a URL or raw ID string."""
    if not url_or_id:
        return ""
    url_or_id = url_or_id.strip()
    # Match /d/<id>/
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    if match:
        return match.group(1)
    # If it's already an ID
    if re.match(r"^[a-zA-Z0-9-_]{20,}$", url_or_id):
        return url_or_id
    return url_or_id


def find_header_row_index(raw_rows: List[List[Any]]) -> int:
    """Find the row index that contains column headers."""
    header_keywords = {"PROPERTY", "PRICE", "UNIT", "AVAILABILITY", "STATUS", "SOURCE", "CATEGORY", "LOCATION"}
    for idx, row in enumerate(raw_rows[:15]):  # check first 15 rows
        row_text = " ".join([str(cell).upper() for cell in row if cell])
        matched_count = sum(1 for kw in header_keywords if kw in row_text)
        if matched_count >= 2:
            return idx
    return 0


def normalize_record(raw_dict: Dict[str, Any], sheet_source: str, row_num: int) -> Dict[str, Any]:
    """
    Standardize a raw row dictionary into the requested fields:
      - property_description
      - price
      - unit_no_address
      - availability
      - source_or_listing
      plus extra metadata and all original columns for universal search.
    """
    # Clean keys
    cleaned_dict = {}
    for k, v in raw_dict.items():
        k_clean = str(k).strip()
        v_clean = str(v).strip() if v is not None and str(v).strip().lower() != 'nan' else ''
        if k_clean and not k_clean.upper().startswith("UNNAMED") and not k_clean.upper().startswith("COLUMN"):
            cleaned_dict[k_clean] = v_clean

    upper_map = {k.upper(): (k, v) for k, v in cleaned_dict.items()}

    def get_first_val(candidates: List[str]) -> str:
        for c in candidates:
            for k_upper, (orig_k, val) in upper_map.items():
                if c in k_upper and val:
                    return val
        return ""

    # 1. Property Description
    prop_desc = get_first_val(["PROPERTY DESCRIPTION", "PROPERTY DETAILS", "DESCRIPTION", "DETAILS"])
    if not prop_desc:
        # Fallback to longest string field
        candidates = [v for k, v in cleaned_dict.items() if len(v) > 25 and not v.startswith("http")]
        if candidates:
            prop_desc = max(candidates, key=len)

    # 2. Price
    price = get_first_val(["PRICE", "RENTAL", "ASKING PRICE", "ASKING"])

    # 3. Unit No. / Address
    unit_no = get_first_val(["UNIT NO./ADDRESS", "UNIT NO", "UNIT NUMBER", "UNIT", "ADDRESS"])
    location = get_first_val(["LOCATION", "CITY", "AREA"])
    if not unit_no and location:
        unit_no = location

    # 4. Availability
    avail = get_first_val(["AVAILABILITY", "STATUS"])
    if not avail:
        avail = "UNKNOWN"

    # 5. Source or Listing
    source = get_first_val(["SOURCE OF LISTINGS", "SOURCE OF LISTING", "SOURCE"])
    if not source:
        source = "DIRECT"

    listing_id = get_first_val(["WEBSITE_LISTING_ID", "LISTING ID", "ID"])
    category = get_first_val(["CATEGORY", "PROPERTY TYPE", "TYPE"])
    photos = get_first_val(["LINK TO PHOTOS", "PHOTOS", "PHOTO", "DRIVE"])
    developer = get_first_val(["DEVELOPER"])
    floor_area = get_first_val(["FLOOR AREA", "FA", "AREA"])
    # Check if this is a "One Away" listing
    status_raw = cleaned_dict.get("STATUS", "")
    check_text = f"{avail} {source} {status_raw}".lower()
    is_one_away = any(term in check_text for term in ["one away", "1 away", "one-away", "1-away"])
    directness = "One Away" if is_one_away else "Direct"

    # Date Updated extraction (strictly check DATE UPDATED, LAST UPDATED, or DATE)
    raw_date = ""
    for cand in ["DATE UPDATED", "LAST UPDATED", "DATE"]:
        for k_upper, (orig_k, val) in upper_map.items():
            if (cand == k_upper or cand in k_upper) and val and not "NO." in k_upper:
                raw_date = val
                break
        if raw_date:
            break

    date_iso, date_display = parse_date_updated(raw_date)
    # Only keep as date_updated if it parsed as a genuine date
    clean_date_updated = date_display if date_iso else ""

    return {
        "id": f"{sheet_source.lower().replace(' ', '_')}_{row_num}",
        "sheet_source": sheet_source,
        "row_number": row_num,
        "property_description": prop_desc,
        "price": price,
        "unit_no_address": unit_no,
        "availability": avail,
        "source_or_listing": source,
        "is_one_away": is_one_away,
        "directness": directness,
        # Date updated attributes
        "date_updated": clean_date_updated,
        "date_updated_iso": date_iso,
        "date_updated_raw": raw_date,
        # Extended helpful attributes
        "listing_id": listing_id,
        "location": location,
        "category": category,
        "photos": photos,
        "developer": developer,
        "floor_area": floor_area,
        # All columns for search & details view
        "all_columns": cleaned_dict
    }


class SheetsClient:
    def __init__(self, config_path: str = "config.json"):
        self.base_dir = os.path.dirname(os.path.abspath(config_path))
        self.config_path = os.path.abspath(config_path)
        self.config = self.load_config()
        self.service_account_email = None
        self._inspect_service_account()

    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config.json: {e}")
        return {
            "sheet_rent": {
                "name": "For Rent",
                "spreadsheet_id_or_url": "",
                "worksheet_name": "",
                "csv_fallback_path": "Master_For_Rent.csv"
            },
            "sheet_sale": {
                "name": "For Sale",
                "spreadsheet_id_or_url": "",
                "worksheet_name": "",
                "csv_fallback_path": "Master_For_Sale.csv"
            },
            "auth": {
                "auth_mode": "service_account",
                "service_account_file": "credentials.json"
            },
            "cache": {
                "enabled": True,
                "cache_file": "sheets_cache.json"
            }
        }

    def save_config(self, new_config: Dict[str, Any]):
        self.config = new_config
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2)
        self._inspect_service_account()

    def _resolve_path(self, rel_or_abs: str) -> str:
        if not rel_or_abs:
            return ""
        if os.path.isabs(rel_or_abs):
            return rel_or_abs
        return os.path.join(self.base_dir, rel_or_abs)

    def _inspect_service_account(self):
        """Read service account email from credentials file if available."""
        creds_file = self._resolve_path(self.config.get("auth", {}).get("service_account_file", "credentials.json"))
        if os.path.exists(creds_file):
            try:
                with open(creds_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.service_account_email = data.get("client_email")
            except Exception:
                self.service_account_email = None

    def get_auth_client(self):
        """Initializes and returns a gspread client."""
        if not HAS_GSPREAD:
            raise RuntimeError("gspread library is not installed. Run: py -m pip install gspread google-auth")

        auth_cfg = self.config.get("auth", {})
        auth_mode = auth_cfg.get("auth_mode", "service_account")

        if auth_mode == "service_account":
            creds_file = self._resolve_path(auth_cfg.get("service_account_file", "credentials.json"))
            if not os.path.exists(creds_file):
                # Try common alternatives
                alt_files = ["service_account.json", "google_credentials.json"]
                found = None
                for alt in alt_files:
                    alt_path = self._resolve_path(alt)
                    if os.path.exists(alt_path):
                        found = alt_path
                        break
                if found:
                    creds_file = found
                else:
                    raise FileNotFoundError(
                        f"Google Service Account key file not found: '{creds_file}'. "
                        "Please place your Service Account JSON file as 'credentials.json' or configure its path."
                    )
            
            creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
            self.service_account_email = creds.service_account_email
            client = gspread.authorize(creds)
            return client

        elif auth_mode == "oauth":
            oauth_file = self._resolve_path(auth_cfg.get("oauth_credentials_file", "oauth_credentials.json"))
            token_file = self._resolve_path(auth_cfg.get("token_file", "token.json"))
            if not os.path.exists(oauth_file):
                raise FileNotFoundError(f"OAuth credentials file not found: '{oauth_file}'")
            return gspread.oauth(credentials_filename=oauth_file, authorized_user_filename=token_file)

        else:
            raise ValueError(f"Unknown auth_mode: {auth_mode}")

    def fetch_google_sheet(self, client, spreadsheet_id_or_url: str, worksheet_name: str = "") -> List[List[Any]]:
        """Fetches all values from a Google Sheet (gspread, Apps Script Web App, or direct export)."""
        clean_url = spreadsheet_id_or_url.strip()

        # 1. Check if user provided a Google Apps Script Web App URL (deployed under their Gmail)
        if "script.google.com/macros/s/" in clean_url:
            import requests
            logger.info("Fetching via Google Apps Script Web App (executed under user's Google account)...")
            res = requests.get(clean_url, allow_redirects=True, timeout=30)
            res.raise_for_status()
            data = res.json()
            if isinstance(data, list):
                return data
            raise ValueError("Apps Script returned unexpected data format. Expected list of rows.")

        sheet_id = extract_spreadsheet_id(clean_url)
        if not sheet_id:
            raise ValueError("No Spreadsheet ID or URL provided.")

        # 2. If client is available (Service Account or OAuth2)
        if client:
            try:
                sh = client.open_by_key(sheet_id)
            except gspread.exceptions.APIError as e:
                code = getattr(e, "response", None) and getattr(e.response, "status_code", None)
                err_msg = str(e)
                if "403" in err_msg or code == 403 or "PERMISSION_DENIED" in err_msg:
                    email_hint = f" '{self.service_account_email}'" if self.service_account_email else " your authorized email"
                    raise PermissionError(
                        f"PERMISSION DENIED (403) for Sheet ID: {sheet_id}\n"
                        f"👉 Action required: Open Google Sheet, click 'Share' (top right), and add{email_hint} as 'Viewer'."
                    ) from e
                elif "404" in err_msg or code == 404 or "NOT_FOUND" in err_msg:
                    raise FileNotFoundError(f"Google Sheet with ID '{sheet_id}' was not found. Please verify the link or ID.") from e
                raise

            # Select worksheet
            if worksheet_name:
                try:
                    ws = sh.worksheet(worksheet_name)
                except gspread.exceptions.WorksheetNotFound:
                    ws = sh.get_worksheet(0)
            else:
                ws = sh.get_worksheet(0)

            return ws.get_all_values()

        # 3. Fallback: Check if sheet is viewable via export link (if Anyone with Link can view)
        import requests
        import csv
        import io
        export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        try:
            logger.info(f"Attempting public CSV export fetch for Sheet ID {sheet_id}...")
            r = requests.get(export_url, timeout=15)
            if r.status_code == 200 and ("text/csv" in r.headers.get("Content-Type", "") or "attachment" in r.headers.get("Content-Disposition", "")):
                csv_reader = csv.reader(io.StringIO(r.text))
                return list(csv_reader)
        except Exception:
            pass

        raise RuntimeError(
            f"Cannot connect to private Sheet '{sheet_id}' without authentication. "
            "Please log in via Google, use Google Apps Script, or provide Service Account credentials."
        )

    def load_from_csv(self, file_path: str) -> List[List[Any]]:
        """Loads sheet rows from a CSV or Excel (.xlsx) file, combining tabs if needed."""
        full_path = self._resolve_path(file_path)
        
        # Check if an .xlsx version exists and is newer or preferred
        base_name = full_path.rsplit('.', 1)[0]
        alt_xlsx = base_name + '.xlsx'
        if os.path.exists(alt_xlsx):
            if not os.path.exists(full_path) or os.path.getmtime(alt_xlsx) >= os.path.getmtime(full_path):
                full_path = alt_xlsx
        elif not os.path.exists(full_path):
            raise FileNotFoundError(f"Source file not found: {full_path}")

        # If it's an Excel workbook (.xlsx / .xls)
        if full_path.lower().endswith(('.xlsx', '.xls')):
            import pandas as pd
            logger.info(f"Loading Excel workbook: {full_path}")
            xl = pd.ExcelFile(full_path)
            combined_rows = []
            
            for sheet_name in xl.sheet_names:
                # Skip helper search tabs
                if 'SEARCH' in sheet_name.upper():
                    continue
                df = xl.parse(sheet_name, header=None)
                sheet_rows = df.fillna("").values.tolist()
                
                # If first sheet, include header row. If subsequent sheets, keep headers or combine
                if not combined_rows:
                    if sheet_rows and len(sheet_rows[0]) > 0 and str(sheet_rows[0][0]).strip() in ['.', '', 'Unnamed: 0', ' ']:
                        sheet_rows[0][0] = 'DATE UPDATED'
                    combined_rows.extend(sheet_rows)
                else:
                    # Append data rows
                    h_idx = find_header_row_index(sheet_rows)
                    combined_rows.extend(sheet_rows[h_idx + 1:])
            
            return combined_rows

        # Standard CSV loader
        import csv
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            return list(reader)

    def parse_rows_into_records(self, raw_rows: List[List[Any]], sheet_source: str) -> List[Dict[str, Any]]:
        """Parses a 2D table of rows into standardized records."""
        if not raw_rows:
            return []

        header_idx = find_header_row_index(raw_rows)
        headers = [str(c).strip() for c in raw_rows[header_idx]]
        if headers and headers[0] in [".", "", "Unnamed: 0", " "]:
            headers[0] = "DATE UPDATED"
        
        # Deduplicate headers
        clean_headers = []
        counts = {}
        for h in headers:
            if not h:
                h = f"COL_{len(clean_headers)+1}"
            counts[h] = counts.get(h, 0) + 1
            if counts[h] > 1:
                clean_headers.append(f"{h}_{counts[h]}")
            else:
                clean_headers.append(h)

        records = []
        for i, row in enumerate(raw_rows[header_idx + 1:], start=header_idx + 2):
            if not any(str(c).strip() for c in row):
                continue  # skip completely blank lines
            row_dict = {}
            for col_idx, col_name in enumerate(clean_headers):
                val = row[col_idx] if col_idx < len(row) else ""
                row_dict[col_name] = str(val).strip()
            
            rec = normalize_record(row_dict, sheet_source, i)
            # Only include if there is some meaningful content (e.g. description, unit, or price)
            if rec["property_description"] or rec["unit_no_address"] or rec["price"]:
                records.append(rec)

        return records

    def sync_data(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Main sync routine:
        1. Tries to connect to Google Sheets if credentials & sheet IDs are configured.
        2. Falls back smoothly to local CSVs if Google is unavailable or permissions are pending.
        3. Saves to cache and returns status report.
        """
        cache_cfg = self.config.get("cache", {})
        cache_file = self._resolve_path(cache_cfg.get("cache_file", "sheets_cache.json"))

        # Check if cache is still fresh and refresh not forced
        if not force_refresh and os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if cached_data.get("records"):
                        logger.info(f"Loaded {len(cached_data['records'])} records from cache ({cache_file})")
                        return cached_data
            except Exception as e:
                logger.warning(f"Failed reading cache: {e}. Re-syncing...")

        all_records = []
        sync_report = {
            "timestamp": datetime.now().isoformat(),
            "sheets": {},
            "errors": [],
            "service_account_email": self.service_account_email,
            "total_records": 0
        }

        # Try Google Client
        client = None
        google_auth_error = None
        try:
            client = self.get_auth_client()
        except Exception as e:
            google_auth_error = str(e)
            logger.info(f"Google Sheets API client not initialized ({e}). Will check CSV fallbacks.")

        # Process Sheet 1: Rent
        rent_cfg = self.config.get("sheet_rent", {})
        rent_name = rent_cfg.get("name", "For Rent")
        rent_id_url = rent_cfg.get("spreadsheet_id_or_url", "").strip()
        rent_csv = rent_cfg.get("csv_fallback_path", "Master_For_Rent.csv")

        rent_rows = None
        rent_source_type = None

        if rent_id_url:
            try:
                logger.info(f"Fetching '{rent_name}' from Google Sheets...")
                rent_rows = self.fetch_google_sheet(client, rent_id_url, rent_cfg.get("worksheet_name", ""))
                rent_source_type = "google_sheets" if client else "apps_script_or_web"
            except Exception as e:
                err_msg = f"Failed to fetch '{rent_name}' from Google: {e}"
                logger.error(err_msg)
                sync_report["errors"].append({"sheet": rent_name, "error": str(e), "type": "permission_or_connection"})

        if not rent_rows and rent_csv:
            try:
                logger.info(f"Loading '{rent_name}' from local CSV fallback: {rent_csv}")
                rent_rows = self.load_from_csv(rent_csv)
                rent_source_type = "local_csv"
            except Exception as e:
                logger.error(f"Failed to load CSV fallback for '{rent_name}': {e}")
                sync_report["errors"].append({"sheet": rent_name, "error": str(e), "type": "csv_missing"})

        if rent_rows:
            recs = self.parse_rows_into_records(rent_rows, rent_name)
            all_records.extend(recs)
            sync_report["sheets"][rent_name] = {
                "source": rent_source_type,
                "count": len(recs),
                "status": "ok"
            }
        else:
            sync_report["sheets"][rent_name] = {
                "source": "none",
                "count": 0,
                "status": "error"
            }

        # Process Sheet 2: Sale
        sale_cfg = self.config.get("sheet_sale", {})
        sale_name = sale_cfg.get("name", "For Sale")
        sale_id_url = sale_cfg.get("spreadsheet_id_or_url", "").strip()
        sale_csv = sale_cfg.get("csv_fallback_path", "Master_For_Sale.csv")

        sale_rows = None
        sale_source_type = None

        if sale_id_url:
            try:
                logger.info(f"Fetching '{sale_name}' from Google Sheets...")
                sale_rows = self.fetch_google_sheet(client, sale_id_url, sale_cfg.get("worksheet_name", ""))
                sale_source_type = "google_sheets" if client else "apps_script_or_web"
            except Exception as e:
                err_msg = f"Failed to fetch '{sale_name}' from Google: {e}"
                logger.error(err_msg)
                sync_report["errors"].append({"sheet": sale_name, "error": str(e), "type": "permission_or_connection"})

        if not sale_rows and sale_csv:
            try:
                logger.info(f"Loading '{sale_name}' from local CSV fallback: {sale_csv}")
                sale_rows = self.load_from_csv(sale_csv)
                sale_source_type = "local_csv"
            except Exception as e:
                logger.error(f"Failed to load CSV fallback for '{sale_name}': {e}")
                sync_report["errors"].append({"sheet": sale_name, "error": str(e), "type": "csv_missing"})

        if sale_rows:
            recs = self.parse_rows_into_records(sale_rows, sale_name)
            all_records.extend(recs)
            sync_report["sheets"][sale_name] = {
                "source": sale_source_type,
                "count": len(recs),
                "status": "ok"
            }
        else:
            sync_report["sheets"][sale_name] = {
                "source": "none",
                "count": 0,
                "status": "error"
            }

        sync_report["total_records"] = len(all_records)
        sync_report["records"] = all_records

        # Save to cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(sync_report, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(all_records)} property records to cache ({cache_file})")
        except Exception as e:
            logger.warning(f"Could not write cache file: {e}")

        return sync_report
