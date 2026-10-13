# 🏢 Property Search Tool — Dual Google Sheets

A fast, universal search tool that searches across **2 Google Sheets** (For Rent & For Sale) across **any column** using any keyword or phrase.

---

## 🌟 Key Features

1. **Universal Full-Column Search**:
   - Searches across **every single column** (Property Description, Price, Unit No., Location, Availability, Source Broker, Developer, Category, etc.).
   - Multi-keyword queries match across columns (e.g. searching `"Two Roxas 34A"` matches *"Two Roxas"* in the Description and *"34A"* in the Unit No column).
   - Shows **which columns matched** your query for complete transparency.

2. **Standardized Property Output**:
   Every search result clearly formats the required fields:
   - 📝 **Property Description**
   - 💰 **Price**
   - 🏢 **Unit No. / Address**
   - 🟢 **Availability** (Available, Leased, Sold, etc.)
   - 👤 **Source or Listing** (Broker / Direct / Sheet Origin)
   - 📸 **Photo links** (Google Drive folders)
   - 📋 Full sheet attributes & raw columns view

3. **Google Sheets Permission & Authentication**:
   - Built to handle private Google Sheets requiring access permission.
   - Uses **Google Service Accounts** (`credentials.json`) — share the sheet once with the service account email, and it accesses both sheets automatically.
   - Clear diagnostic error messages if permission is missing (403 Forbidden).
   - **Offline / Local CSV Fallback**: Pre-loaded with local CSV data (`Master_For_Rent.csv` and `Master_For_Sale.csv`, 1,495 properties), meaning the tool **works immediately right now** even before configuring Google Sheets credentials!

4. **Multiple Interfaces**:
   - 🌐 **Modern Web UI Dashboard** with instant search, filters, cards, and CSV export.
   - 💻 **Terminal CLI**: Run fast searches directly from your shell.
   - 🔍 **Interactive REPL Shell**: Type keywords interactively with zero delay.
   - ⚡ **REST API**: Easily integrate into other apps (`GET /api/search?q=...`).

---

## 🚀 Quick Start

### 1. Launch the Web UI Dashboard
```bash
py search_sheets.py --server
```
Then open your browser at: **[http://localhost:5050](http://localhost:5050)**

### 2. Search from Command Line (CLI)
```bash
# Search by building and unit
py search_sheets.py "Two Roxas 34A"

# Search by price
py search_sheets.py "₱450,000"

# Search with filters
py search_sheets.py "Park Central" --sheet sale --avail available

# Export search results as JSON
py search_sheets.py "Makati" --json --limit 5
```

### 3. Interactive Search Shell
```bash
py search_sheets.py --interactive
```

---

## 🔐 How to Connect Private Google Sheets & Grant Permission

If your Google Sheets require permission to be accessed:

### Step 1: Create a Google Cloud Service Account
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Navigate to **APIs & Services > Library** and enable **Google Sheets API** and **Google Drive API**.
4. Navigate to **APIs & Services > Credentials**.
5. Click **Create Credentials > Service Account**.
6. Give it a name (e.g. `sheet-property-searcher`) and click **Done**.
7. Click on the created service account, go to the **Keys** tab, click **Add Key > Create new key > JSON**.
8. Download the JSON file and save it in this project folder as **`credentials.json`**.

### Step 2: Grant Permission on Both Google Sheets
1. Open your downloaded `credentials.json` and copy the `"client_email"` address (e.g. `sheet-searcher@my-project-123.iam.gserviceaccount.com`).
2. Open your **Google Sheet 1 (For Rent)** in your browser:
   - Click the blue **Share** button in the top-right corner.
   - Paste the service account email.
   - Set permission to **Viewer**.
   - Click **Send / Share**.
3. Repeat the exact same step for **Google Sheet 2 (For Sale)**.

### Step 3: Configure Sheet IDs in `config.json` or the Web UI
Open `config.json` (or click **Sheets Config** in the Web UI) and paste your Google Sheet links:

```json
{
  "sheet_rent": {
    "name": "For Rent",
    "spreadsheet_id_or_url": "https://docs.google.com/spreadsheets/d/YOUR_RENT_SHEET_ID/edit",
    "worksheet_name": "",
    "csv_fallback_path": "Master_For_Rent.csv"
  },
  "sheet_sale": {
    "name": "For Sale",
    "spreadsheet_id_or_url": "https://docs.google.com/spreadsheets/d/YOUR_SALE_SHEET_ID/edit",
    "worksheet_name": "",
    "csv_fallback_path": "Master_For_Sale.csv"
  },
  "auth": {
    "auth_mode": "service_account",
    "service_account_file": "credentials.json"
  }
}
```

### Step 4: Sync
Run the sync command:
```bash
py search_sheets.py --sync
```
Or click the **Sync Sheets** button on the Web UI!

---

## 📡 REST API Documentation

The server exposes endpoints on `http://localhost:5050`:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/search?q={query}&sheet={rent\|sale\|all}&avail={available\|leased\|sold\|all}&fuzzy={true\|false}&limit={n}` | Perform multi-column search |
| `GET` | `/api/status` | Get total record counts, sheet sources, and sync timestamp |
| `POST` | `/api/sync` | Trigger an immediate live sync from Google Sheets |
| `POST` | `/api/config` | Update Google Sheet URLs and credentials settings |

---

## 📁 File Structure

- `search_sheets.py`: Main CLI tool and entrypoint (`--server`, `--interactive`, `--sync`, `--status`).
- `search_engine.py`: Multi-column search algorithm with token matching, column highlighting, and ranking.
- `sheets_client.py`: Google Sheets API connector with permission diagnostics & local CSV fallback.
- `server.py`: Built-in HTTP server hosting the Web UI and JSON API.
- `web/index.html`: Responsive single-page search dashboard (Tailwind CSS, search, filters, modals, export).
- `config.json`: Sheet IDs, tab names, and credentials paths.
- `Master_For_Rent.csv` & `Master_For_Sale.csv`: Fallback datasets (1,495 active listings).
