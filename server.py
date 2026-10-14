"""
Property Search Server & REST API
=================================
Hosts the responsive web dashboard and JSON REST endpoints for multi-column search.
No external server dependencies needed (uses Python built-in ThreadingHTTPServer).
"""

import os
import sys
import json
import urllib.parse

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sheets_client import SheetsClient
from search_engine import PropertySearchEngine

# Global instances
sheets_client = SheetsClient()
sync_data_cache = sheets_client.sync_data(force_refresh=False)
search_engine = PropertySearchEngine(sync_data_cache.get("records", []))


class SearchRequestHandler(BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query_params = urllib.parse.parse_qs(parsed.query)

            # 1. API: Search
            if path == "/api/search":
                q = query_params.get("q", [""])[0]
                sheet = query_params.get("sheet", ["all"])[0]
                loc = query_params.get("loc", [""])[0]
                bedrooms = query_params.get("bedrooms", ["all"])[0]
                category = query_params.get("category", ["all"])[0]
                min_price_raw = query_params.get("min_price", [""])[0]
                max_price_raw = query_params.get("max_price", [""])[0]

                min_price = None
                if min_price_raw:
                    try:
                        min_price = float(min_price_raw.replace(",", "").strip())
                    except ValueError:
                        pass

                max_price = None
                if max_price_raw:
                    try:
                        max_price = float(max_price_raw.replace(",", "").strip())
                    except ValueError:
                        pass

                sheet = query_params.get("sheet", ["all"])[0]
                avail = query_params.get("avail", ["all"])[0]
                direct = query_params.get("direct", ["all"])[0]
                fuzzy = query_params.get("fuzzy", ["false"])[0].lower() == "true"
                try:
                    limit = int(query_params.get("limit", [150])[0])
                except ValueError:
                    limit = 150

                results = search_engine.search(
                    query=q,
                    location_query=loc,
                    bedroom_filter=bedrooms,
                    category_filter=category,
                    sheet_filter=sheet,
                    availability_filter=avail,
                    directness_filter=direct,
                    min_price=min_price,
                    max_price=max_price,
                    limit=limit,
                    enable_fuzzy=fuzzy
                )

                response_data = {
                    "query": q,
                    "location_query": loc,
                    "filters": {
                        "sheet": sheet,
                        "availability": avail,
                        "direct": direct,
                        "bedrooms": bedrooms,
                        "category": category,
                        "min_price": min_price,
                        "max_price": max_price,
                        "fuzzy": fuzzy
                    },
                    "total_matches": len(results),
                    "results": results
                }

                body_bytes = json.dumps(response_data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(body_bytes)
                return

            # 2. API: Status
            elif path == "/api/status":
                global sync_data_cache
                report = {
                    "total_records": len(search_engine.records),
                    "sheets": sync_data_cache.get("sheets", {}),
                    "service_account_email": sheets_client.service_account_email,
                    "timestamp": sync_data_cache.get("timestamp", ""),
                    "config": sheets_client.config
                }

                body_bytes = json.dumps(report, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(body_bytes)
                return

            # 3. Serve Web UI
            elif path in ["", "/", "/index.html"]:
                web_file = os.path.join(os.path.dirname(__file__), "web", "index.html")
                if os.path.exists(web_file):
                    with open(web_file, "rb") as f:
                        body_bytes = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(body_bytes)
                    return

            self.send_response(404)
            self.end_headers()
            return
        except Exception as e:
            print("Error in do_GET:", e, flush=True)
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        global sync_data_cache
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. API: Trigger Sync
        if path == "/api/sync":
            try:
                sync_data_cache = sheets_client.sync_data(force_refresh=True)
                search_engine.set_records(sync_data_cache.get("records", []))
                
                resp = {
                    "success": True,
                    "total_records": len(search_engine.records),
                    "sheets": sync_data_cache.get("sheets", {}),
                    "errors": sync_data_cache.get("errors", [])
                }
                status_code = 200
            except Exception as e:
                resp = {"success": False, "error": str(e)}
                status_code = 500

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            return

        # 2. API: Update Config
        elif path == "/api/config":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                new_cfg = json.loads(body)
                
                current = sheets_client.config
                if "sheet_rent" in new_cfg:
                    current["sheet_rent"].update(new_cfg["sheet_rent"])
                if "sheet_sale" in new_cfg:
                    current["sheet_sale"].update(new_cfg["sheet_sale"])
                if "auth" in new_cfg:
                    current["auth"].update(new_cfg["auth"])

                sheets_client.save_config(current)
                resp = {"success": True, "config": current}
                status_code = 200
            except Exception as e:
                resp = {"success": False, "error": str(e)}
                status_code = 500

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            return

        # 3. API: Browser Sync with Google Account
        elif path == "/api/browser-sync":
            try:
                import subprocess
                print("Launching browser_sync.py in dedicated process...", flush=True)
                proc = subprocess.run([sys.executable, "browser_sync.py"], timeout=360)
                if proc.returncode == 0:
                    sync_data_cache = sheets_client.sync_data(force_refresh=True)
                    search_engine.set_records(sync_data_cache.get("records", []))
                    resp = {"success": True, "total_records": len(search_engine.records)}
                    status_code = 200
                else:
                    resp = {"success": False, "error": f"Browser sync exited with code {proc.returncode}"}
                    status_code = 500
            except Exception as e:
                resp = {"success": False, "error": str(e)}
                status_code = 500

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
        # 4. API: Admin Push Data (for cloud instances to receive updates from local sync)
        elif path == "/api/admin/push-data":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                new_data = json.loads(body)
                if "records" in new_data:
                    sync_data_cache = new_data
                    search_engine.set_records(new_data["records"])
                    cache_file = sheets_client._resolve_path("sheets_cache.json")
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(new_data, f, ensure_ascii=False, indent=2)
                    resp = {"success": True, "total_records": len(search_engine.records)}
                    status_code = 200
                else:
                    resp = {"success": False, "error": "Missing 'records' field in payload"}
                    status_code = 400
            except Exception as e:
                resp = {"success": False, "error": str(e)}
                status_code = 500

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def run_server(host: str = "0.0.0.0", port: int = 5055):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    server = ThreadingHTTPServer((host, port), SearchRequestHandler)
    print("\n=======================================================")
    print("Property Search Dashboard is running!")
    print(f"Open in browser: http://localhost:{port} or http://127.0.0.1:{port}")
    print(f"Database: {len(search_engine.records):,} total property listings loaded.")
    print("Press Ctrl+C to stop.")
    print("=======================================================\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port=port)
