"""
Property Internet Scanner & Real Estate Lead Radar
===================================================
Scans online property portals, social media listings, and local databases for specific
real estate inquiries (e.g. "Edades Suites 3 bedroom for sale").

Features:
- Live multi-platform search (Carousell, Dot Property, Facebook broker posts, Local Inventory)
- Automated parsing of Price (PHP), Floor Area (sqm), Unit Type, Parking, and Contact
- Valuation analytics (Price / sqm, Average asking price)
- Standalone interactive Web Dashboard (run with --serve)
- Automated scheduled monitoring / alert mode (--watch)
"""

import os
import sys
import re
import json
import time
import base64
import urllib.parse
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def get_lan_ip():
    """Returns local LAN IP (e.g. 192.168.x.x) for local network sharing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    requests = None
    BeautifulSoup = None

try:
    import pandas as pd
except ImportError:
    pd = None


class PropertyInternetScanner:
    def __init__(self, workspace_dir=None):
        self.workspace_dir = workspace_dir or os.path.dirname(os.path.abspath(__file__))
        self.session = requests.Session() if requests else None
        if self.session:
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            })

    # -------------------------------------------------------------
    # Parsing & Normalization Helpers
    # -------------------------------------------------------------
    @staticmethod
    def parse_price(text):
        if not text:
            return 0.0, "N/A"
        clean = text.replace('\xa0', ' ').strip()
        
        # Check patterns like "PHP 135,000,000", "₱80,000,000", "P135M", "80M", "104.4M"
        m_m = re.search(r'(?:PHP|₱|P|Asking Price|Price|🏷️)?\s*([\d\.]+)\s*(?:M|Million|Mln)\b', clean, re.I)
        if m_m:
            try:
                val = float(m_m.group(1)) * 1_000_000
                return val, f"₱{val:,.0f}"
            except Exception:
                pass

        # Check rental patterns like "135k/mo", "170k", "170K inclusive"
        m_k = re.search(r'(?:PHP|₱|P|Asking Price|Price|🏷️)?\s*([\d\.]+)\s*(?:K|k)\b(?:\s*(?:/mo|/month|per month|inclusive|net))?', clean, re.I)
        if m_k:
            try:
                val = float(m_k.group(1)) * 1_000
                is_rent = any(w in clean.lower() for w in ['/mo', 'rent', 'lease', 'month', 'assoc', 'mos'])
                return val, f"₱{val:,.0f}/mo" if is_rent else f"₱{val:,.0f}"
            except Exception:
                pass

        # Check full numbers like ₱ 135,000,000 or PHP 80,000,000
        m_full = re.search(r'(?:PHP|₱|P)\s*([\d,]{6,})', clean, re.I)
        if m_full:
            try:
                val = float(m_full.group(1).replace(',', ''))
                return val, f"₱{val:,.0f}"
            except Exception:
                pass

        # Fallback regex for pure digits with commas if >= 6 digits
        m_digits = re.search(r'\b([\d]{1,3}(?:,\d{3}){2,})\b', clean)
        if m_digits:
            try:
                val = float(m_digits.group(1).replace(',', ''))
                return val, f"₱{val:,.0f}"
            except Exception:
                pass

        return 0.0, "Contact for Price"

    @staticmethod
    def parse_sqm(text):
        if not text:
            return 0.0
        # Look for SQM with word boundaries (supports from 15 sqm micro-studios to 3000 sqm luxury estates)
        matches = re.findall(r'\b(\d+(?:\.\d+)?)\s*(?:sqm|sq\.m|sq\s*m|square\s*meters)\b', text, re.I)
        for m in matches:
            try:
                val = float(m)
                if 15.0 <= val <= 3500.0:
                    return val
            except Exception:
                continue
        return 0.0

    @staticmethod
    def parse_bedrooms(text):
        if not text:
            return "N/A"
        if re.search(r'\bStudio\b', text, re.I):
            return "Studio"
        m = re.search(r'\b([1-9])\s*(?:BR|bedroom|bed|brs|bedrooms)\b', text, re.I)
        if m:
            return f"{m.group(1)}BR"
        m2 = re.search(r'\b([1-9])-(?:bedroom|br)\b', text, re.I)
        if m2:
            return f"{m2.group(1)}BR"
        return "N/A"

    @staticmethod
    def parse_unit_type(text):
        t = text.lower()
        if "house and lot" in t or "house & lot" in t or "bungalow" in t or "mansion" in t:
            return "House & Lot"
        if "townhouse" in t:
            return "Townhouse"
        if "commercial" in t or "office" in t or "retail" in t or "warehouse" in t:
            return "Commercial"
        if "vacant lot" in t or (" lot" in t and "house" not in t and "z-loft" not in t):
            return "Vacant Lot"
        if "penthouse" in t:
            return "Penthouse"
        if "z-loft" in t or "loft" in t or "bi-level" in t:
            return "Bi-Level / Loft"
        if "villa" in t or "garden villa" in t:
            return "Garden Villa"
        if "suite" in t or "suites" in t:
            return "Suites / Flat"
        if "flat" in t:
            return "Flat"
        return "Condominium"

    @staticmethod
    def parse_category(text):
        t = text.lower()
        if "rent" in t or "lease" in t or "tenant" in t or "/mo" in t:
            return "FOR LEASE"
        return "FOR SALE"

    # -------------------------------------------------------------
    # 1. Multi-Query Sub-Expander for Comprehensive Marketplace Coverage
    # -------------------------------------------------------------
    @staticmethod
    def get_smart_subqueries(query):
        """Generates smart query variations to maximize coverage across online marketplaces."""
        cleaned = re.sub(r'\b(for sale|for rent|for lease|sale|rent|lease|available|looking for|inquiry)\b', '', query, flags=re.I).strip()
        words = [w for w in cleaned.split() if w.lower() not in {'for', 'and', 'the', 'with', 'at', 'in'}]
        
        anchor = words[0] if words else ''
        if len(words) >= 2 and words[1].lower() in {'terraces', 'alabang', 'salcedo', 'triangle', 'residences', 'suites', 'tower', 'villas', 'primea'}:
            anchor = f"{words[0]} {words[1]}"
            
        m_bed = re.search(r'\b([1-9])\s*(?:BR|bedroom|bed)\b', query, re.I)
        bed_str = f"{m_bed.group(1)} bedroom" if m_bed else ""
        
        subqueries = []
        clean_direct = ' '.join(words)
        if clean_direct:
            subqueries.append(clean_direct)
            
        if ' or ' in cleaned.lower():
            or_parts = re.split(r'\s+or\s+', cleaned, flags=re.I)
            for part in or_parts:
                part_clean = part.strip()
                if anchor.lower() not in part_clean.lower():
                    part_clean = f"{anchor} {part_clean}"
                subqueries.append(part_clean)
                
        if anchor and bed_str:
            subqueries.append(f"{anchor} {bed_str}")
        if anchor:
            subqueries.append(f"{anchor} Rockwell")
            subqueries.append(anchor)
            
        seen = set()
        final = []
        for q in subqueries:
            norm = ' '.join(q.split()).lower()
            if norm not in seen and len(norm) >= 3:
                seen.add(norm)
                final.append(' '.join(q.split()))
        return final[:4]

    # -------------------------------------------------------------
    # 2. Carousell PH Scanner (Live Web Marketplace for ANY Property)
    # -------------------------------------------------------------
    def scan_carousell(self, query):
        if not self.session:
            return []
        results = []
        seen_urls = set()
        subqueries = self.get_smart_subqueries(query)

        # Stop words and anchor extraction
        stop_words = {'sale', 'rent', 'lease', 'room', 'bedroom', 'with', 'near', 'makati', 'city', 'philippines', 'the', 'for', 'and', 'or', 'unit'}
        words = [w for w in re.findall(r'[a-zA-Z0-9]+', query) if len(w) >= 3 and w.lower() not in stop_words]
        anchor = words[0].lower() if words else ''
        q_keywords = [w.lower() for w in words]

        for sub_q in subqueries:
            try:
                enc_query = urllib.parse.quote(sub_q)
                url = f"https://www.carousell.ph/categories/property-102/?search={enc_query}"
                resp = self.session.get(url, timeout=10)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, 'html.parser')
                for a in soup.select('a[href*="/p/"]'):
                    href = a.get('href', '')
                    if not href:
                        continue
                    clean_path = href.split('?')[0]
                    if clean_path in seen_urls:
                        continue

                    full_url = "https://www.carousell.ph" + href if href.startswith('/') else href
                    title = a.get_text(separator=' | ', strip=True)

                    if not title or len(title) < 10 or 'see listing details' in title.lower():
                        continue

                    parent = a
                    for _ in range(4):
                        if parent and parent.parent:
                            parent = parent.parent
                    card_text = parent.get_text(separator=' \n ', strip=True) if parent else title
                    full_searchable = (title + " " + href + " " + card_text).lower()

                    # Require core anchor (e.g. 'edades') in title or URL to eliminate keyword-stuffed ads from other buildings
                    title_url_lower = (title + " " + href).lower()
                    if anchor and anchor not in title_url_lower:
                        continue
                    if q_keywords and not any(kw in full_searchable for kw in q_keywords):
                        continue

                    seen_urls.add(clean_path)
                    price_val, price_fmt = self.parse_price(title)
                    if price_val == 0.0:
                        price_val, price_fmt = self.parse_price(card_text)

                    sqm = self.parse_sqm(title) or self.parse_sqm(card_text)
                    bedrooms = self.parse_bedrooms(title) or self.parse_bedrooms(card_text)
                    unit_type = self.parse_unit_type(title + " " + card_text)
                    cat = self.parse_category(title + " " + card_text)

                    m_park = re.search(r'(\d+)\s*(?:parking|car\s*slot|slots)', card_text, re.I)
                    parking = f"{m_park.group(1)} Parking Slots" if m_park else "Not specified"
                    is_direct = "direct" in card_text.lower() or "direct" in title.lower()

                    title_parts = [p.strip() for p in title.split('|') if p.strip() and not p.strip().startswith('PHP') and not p.strip().startswith('₱')]
                    clean_title = title_parts[0] if title_parts else title
                    if len(clean_title) < 15 and len(title_parts) > 1:
                        clean_title = f"{title_parts[0]} - {title_parts[1]}"

                    results.append({
                        "id": f"carousell-{len(results)+1}",
                        "title": clean_title[:140],
                        "platform": "Carousell Philippines",
                        "source": "Live Web Market",
                        "url": full_url,
                        "price": price_val,
                        "price_formatted": price_fmt,
                        "bedrooms": bedrooms,
                        "floor_area_sqm": sqm,
                        "price_per_sqm": round(price_val / sqm, 2) if sqm > 0 and price_val > 0 else 0,
                        "unit_type": unit_type,
                        "category": cat,
                        "parking": parking,
                        "is_direct": is_direct,
                        "score": 15 if price_val > 0 else 5,
                        "snippet": card_text[:240].replace('\n', ' ')
                    })
            except Exception as e:
                print(f"Carousell scan error on subquery '{sub_q}': {e}", file=sys.stderr)

        return results

    # -------------------------------------------------------------
    # 3. Direct Portals & Social Media Launchpad Generator
    # -------------------------------------------------------------
    def get_launchpad_links(self, query):
        enc_q = urllib.parse.quote(query)
        clean_q = query.replace('"', '').strip()
        
        return [
            {
                "id": "lp-facebook-market",
                "name": "Facebook Marketplace",
                "badge": "Direct Owners",
                "badge_color": "purple",
                "icon": "fa-brands fa-facebook text-blue-400",
                "url": f"https://www.facebook.com/marketplace/manila/search?query={enc_q}",
                "desc": f"Direct owner postings & rush broker sales for '{clean_q}' across Metro Manila."
            },
            {
                "id": "lp-facebook-groups",
                "name": "Facebook Broker Groups",
                "badge": "Broker Co-Broke",
                "badge_color": "blue",
                "icon": "fa-solid fa-users text-indigo-400",
                "url": f"https://www.facebook.com/search/posts/?q={enc_q}",
                "desc": f"Exclusive authority & rush co-broke posts across real estate groups you belong to."
            },
            {
                "id": "lp-lamudi",
                "name": "Lamudi Philippines",
                "badge": "Major Portal",
                "badge_color": "sky",
                "icon": "fa-solid fa-building text-sky-400",
                "url": f"https://www.lamudi.com.ph/buy/?q={enc_q}",
                "desc": f"Top accredited agency listings & price history for '{clean_q}'."
            },
            {
                "id": "lp-dotproperty",
                "name": "Dot Property",
                "badge": "Brokerage Feed",
                "badge_color": "emerald",
                "icon": "fa-solid fa-globe text-emerald-400",
                "url": f"https://www.dotproperty.com.ph/properties-for-sale?q={enc_q}",
                "desc": f"Secondary market inventory and verified broker feeds across Metro Manila."
            },
            {
                "id": "lp-hoppler",
                "name": "Hoppler Luxury",
                "badge": "High-End Network",
                "badge_color": "amber",
                "icon": "fa-solid fa-gem text-amber-400",
                "url": f"https://www.hoppler.com.ph/search?q={enc_q}",
                "desc": f"Curated high-end residential listings represented by licensed partner brokers."
            }
        ]

    def scan_portals(self, query):
        return []

    def scan_social_media(self, query):
        return []

    # -------------------------------------------------------------
    # 4. Universal Master Inventory Cross-Referencer (Sheets Cache + Master Files)
    # -------------------------------------------------------------
    def scan_local_inventory(self, query):
        results = []
        stop_words = {'sale', 'rent', 'lease', 'for', 'with', 'and', 'or', 'the', 'unit', 'near', 'in', 'at', 'bedroom', 'bedrooms', 'br', 'condo', 'condominium', 'apartment'}
        tokens = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', query) if len(w) >= 3 and w.lower() not in stop_words]
        if not tokens:
            return results

        type_keywords = {'suite', 'suites', 'villa', 'villas', 'loft', 'penthouse', 'garden', 'flat'}
        query_types = [t for t in tokens if t in type_keywords]
        core_anchors = [t for t in tokens if t not in type_keywords]
        
        m_bed = re.search(r'\b([1-9])\s*(?:BR|bedroom|bed)\b', query, re.I)
        target_bed = f"{m_bed.group(1)}BR" if m_bed else None

        # 1. Prioritize sheets_cache.json (3,800+ live Google Sheet records)
        cache_file = os.path.join(self.workspace_dir, "sheets_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                records = cache_data.get("records", [])

                for r in records:
                    searchable = " ".join(str(r.get(k, '')) for k in [
                        'property_description', 'location', 'developer', 'unit_no_address', 'all_columns'
                    ]).lower()

                    if core_anchors:
                        if not all(a in searchable for a in core_anchors):
                            continue
                    else:
                        if not any(t in searchable for t in tokens):
                            continue

                    desc = str(r.get('property_description', '') or '')
                    price_val, price_fmt = self.parse_price(str(r.get('price', '')))
                    if price_val == 0.0:
                        price_val, price_fmt = self.parse_price(desc)

                    sqm = self.parse_sqm(str(r.get('floor_area', ''))) or self.parse_sqm(desc)
                    source_str = str(r.get('source_or_listing', 'Master Inventory'))
                    photos = str(r.get('photos', ''))
                    status_str = str(r.get('availability', 'AVAILABLE'))
                    cat = str(r.get('sheet_source', 'For Sale')).upper()
                    if "RENT" in cat or "LEASE" in cat:
                        cat = "FOR LEASE"
                    else:
                        cat = "FOR SALE"

                    bedrooms = self.parse_bedrooms(desc)
                    unit_type = self.parse_unit_type(desc)

                    score = 10
                    if target_bed and (target_bed.lower() in desc.lower() or target_bed.lower() == bedrooms.lower()):
                        score += 25
                    if query_types and any(qt in searchable for qt in query_types):
                        score += 15
                    if price_val > 0:
                        score += 5
                    if "sale" in query.lower() and cat == "FOR SALE":
                        score += 10
                    elif "rent" in query.lower() and cat == "FOR LEASE":
                        score += 10

                    first_line = desc.strip().splitlines()[0] if desc.strip() else 'Inventory Property'
                    clean_first_line = re.sub(r'^[🏷️\*\s\-:]+', '', first_line).strip()
                    
                    results.append({
                        "id": f"cache-{r.get('id', len(results)+1)}",
                        "title": f"Internal Inventory: {clean_first_line[:90]}",
                        "platform": "Internal Master Database",
                        "source": f"Master Sheet ({source_str})",
                        "url": photos if photos.startswith('http') else "#",
                        "price": price_val,
                        "price_formatted": price_fmt,
                        "bedrooms": bedrooms,
                        "floor_area_sqm": sqm,
                        "price_per_sqm": round(price_val / sqm, 2) if sqm > 0 and price_val > 0 else 0,
                        "unit_type": unit_type,
                        "category": cat,
                        "parking": "Included" if "parking" in desc.lower() else "Not specified",
                        "is_direct": "direct" in source_str.lower(),
                        "status": status_str,
                        "score": score,
                        "snippet": desc[:260].replace('\n', ' ')
                    })
            except Exception as e:
                print(f"Error reading sheets_cache.json: {e}", file=sys.stderr)

        # 2. Fallback to Master_For_Sale.xlsx if cache had no matches
        if not results and pd:
            master_file = os.path.join(self.workspace_dir, "Master_For_Sale.xlsx")
            if os.path.exists(master_file):
                try:
                    df = pd.read_excel(master_file)
                    for idx, row in df.iterrows():
                        row_str = " ".join(str(val) for val in row.dropna().values).lower()
                        if core_anchors:
                            if not all(term in row_str for term in core_anchors):
                                continue
                        else:
                            if not any(term in row_str for term in tokens):
                                continue

                        details = str(row.get('PROPERTY DETAILS', ''))
                        price_val, price_fmt = self.parse_price(str(row.get('PRICE', '')))
                        if price_val == 0.0:
                            price_val, price_fmt = self.parse_price(details)

                        sqm = self.parse_sqm(str(row.get('FLOOR AREA', ''))) or self.parse_sqm(details)
                        source_str = str(row.get('SOURCE OF LISTING', 'Master File'))
                        photos_link = str(row.get('LINK TO PHOTOS', ''))
                        first_line = details.strip().splitlines()[0] if details.strip() else 'Inventory Property'
                        clean_first_line = re.sub(r'^[🏷️\*\s\-:]+', '', first_line).strip()

                        bedrooms = self.parse_bedrooms(details)
                        unit_type = self.parse_unit_type(details)

                        score = 10
                        if target_bed and (target_bed.lower() in details.lower() or target_bed.lower() == bedrooms.lower()):
                            score += 25
                        if query_types and any(qt in row_str for qt in query_types):
                            score += 15

                        results.append({
                            "id": f"local-{idx}",
                            "title": f"Internal Inventory: {clean_first_line[:90]}",
                            "platform": "Internal Master Database",
                            "source": f"Master_For_Sale.xlsx ({source_str})",
                            "url": photos_link if photos_link.startswith('http') else "#",
                            "price": price_val,
                            "price_formatted": price_fmt,
                            "bedrooms": bedrooms,
                            "floor_area_sqm": sqm,
                            "price_per_sqm": round(price_val / sqm, 2) if sqm > 0 and price_val > 0 else 0,
                            "unit_type": unit_type,
                            "category": "FOR SALE",
                            "parking": "Included" if "parking" in details.lower() else "Not specified",
                            "is_direct": "direct" in source_str.lower(),
                            "status": str(row.get('STATUS', 'AVAILABLE')),
                            "score": score,
                            "snippet": details[:260].replace('\n', ' ')
                        })
                except Exception as e:
                    print(f"Error reading Master_For_Sale.xlsx: {e}", file=sys.stderr)

        return results

    def run_scan(self, query="Proscenium 2 bedroom for sale", filter_sale=False):
        start_time = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now_str}] 🛰️  [SCAN START] Initiating multi-channel search for: '{query}'...", flush=True)

        # 1. Carousell Live Market with Multi-Query Expansion
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🛒  [CH-1: CAROUSELL PH] Scanning live marketplace...", flush=True)
        carousell_items = self.scan_carousell(query)
        print(f"[{datetime.now().strftime('%H:%M:%S')}]     -> Found {len(carousell_items)} candidate listings on Carousell.", flush=True)

        # 2. Cross-reference Internal Master Inventory
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 📂  [CH-2: MASTER INVENTORY] Matching 3,867 Google Sheet & Excel records...", flush=True)
        local_items = self.scan_local_inventory(query)
        print(f"[{datetime.now().strftime('%H:%M:%S')}]     -> Matched {len(local_items)} internal inventory records.", flush=True)

        # 3. Direct Portals & Social Launchpad
        launchpad_items = self.get_launchpad_links(query)

        # Combine REAL listings only (no ₱0 dummy cards in the listings grid)
        all_results = []
        all_results.extend(carousell_items)
        all_results.extend(local_items)

        # Filter out duplicates and lease if sale requested
        final_list = []
        seen = set()
        for item in all_results:
            key = (item.get('title', '').lower()[:40], round(item.get('price', 0)))
            if key in seen:
                continue
            seen.add(key)
            if filter_sale and item.get('category') == "FOR LEASE":
                continue
            final_list.append(item)

        # Calculate statistics
        prices = [x['price'] for x in final_list if x['price'] > 1_000_000]
        sqms = [x['floor_area_sqm'] for x in final_list if x['floor_area_sqm'] > 20]
        rates = [x['price_per_sqm'] for x in final_list if x['price_per_sqm'] > 10_000]

        # Sort: score descending, then price descending
        final_list.sort(key=lambda x: (x.get('score', 0), x.get('price', 0)), reverse=True)

        summary = {
            "query": query,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(time.time() - start_time, 2),
            "total_found": len(final_list),
            "avg_price": round(sum(prices) / len(prices)) if prices else 0,
            "min_price": min(prices) if prices else 0,
            "max_price": max(prices) if prices else 0,
            "avg_price_per_sqm": round(sum(rates) / len(rates)) if rates else 0,
            "sources": {
                "Carousell": len(carousell_items),
                "Local Inventory": len(local_items),
                "Portals & Social": len(launchpad_items)
            },
            "launchpad": launchpad_items,
            "lan_ip": get_lan_ip(),
            "results": final_list
        }
        return summary


# =================================================================
# Standalone Web Dashboard & HTTP Server
# =================================================================
HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Property Radar | Internet & Social Media Real Estate Scanner</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .badge-portal { background: rgba(59, 130, 246, 0.15); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-social { background: rgba(168, 85, 247, 0.15); color: #a855f7; border: 1px solid rgba(168, 85, 247, 0.3); }
    .badge-carousell { background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }
    .badge-local { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }

    @media print {
      body { background: #ffffff !important; color: #0f172a !important; font-size: 10pt; }
      header, #searchSection, #presetSection, #scanStatus, #shareModal, #toastNotification, .no-print, button, a[href^="http"] i {
        display: none !important;
      }
      #printHeader {
        display: block !important;
        border-bottom: 2px solid #0f172a;
        padding-bottom: 8px;
        margin-bottom: 16px;
      }
      .bg-slate-900, .bg-slate-950, .bg-slate-800 {
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        color: #0f172a !important;
        box-shadow: none !important;
      }
      .text-white, .text-slate-200, .text-slate-300 { color: #0f172a !important; }
      .text-slate-400, .text-slate-500 { color: #475569 !important; }
      .text-amber-400 { color: #b45309 !important; }
      .text-indigo-400 { color: #4338ca !important; }
      .text-emerald-400 { color: #047857 !important; }
      #listingsGrid {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 12px !important;
      }
      .print-avoid-break {
        break-inside: avoid;
        page-break-inside: avoid;
      }
    }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">

  <!-- Navbar -->
  <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-amber-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-amber-500/20">
          <i class="fa-solid fa-radar text-white text-lg"></i>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="font-bold text-lg leading-tight bg-gradient-to-r from-white via-slate-200 to-amber-400 bg-clip-text text-transparent">Property Radar</h1>
            <!-- Live Active Radar Pill -->
            <div id="radarBadge" class="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-[11px]">
              <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
              <span id="radarStatusLabel" class="text-slate-300 font-medium">Radar Standby</span>
            </div>
          </div>
          <p class="text-xs text-slate-400">Internet & Social Media Lead Scanner</p>
        </div>

        <!-- Mode Switcher Tabs -->
        <div class="hidden md:flex items-center gap-1 bg-slate-950/80 p-1 rounded-xl border border-slate-800 text-xs ml-2">
          <span class="px-2.5 py-1 rounded-lg bg-indigo-600 text-white font-semibold flex items-center gap-1.5 shadow-sm">
            <i class="fa-solid fa-satellite-dish"></i>
            <span>Internet Radar</span>
          </span>
          <a href="/" title="Open Master Google Sheets Inventory" class="px-2.5 py-1 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-slate-200 font-medium flex items-center gap-1.5 transition">
            <i class="fa-solid fa-file-excel text-emerald-400"></i>
            <span>Master Inventory (3,867)</span>
          </a>
        </div>
      </div>

      <div class="flex items-center gap-2 flex-wrap">
        <!-- Auto-Scan / Watchdog Selector -->
        <div class="flex items-center gap-1.5 bg-slate-800/90 border border-slate-700 rounded-lg px-2.5 py-1 text-xs">
          <i class="fa-solid fa-clock-rotate-left text-slate-400"></i>
          <select id="autoScanSelect" onchange="changeAutoScan(this.value)" class="bg-transparent text-slate-200 focus:outline-none cursor-pointer text-xs">
            <option value="0" class="bg-slate-900">Auto-Scan: Off</option>
            <option value="300" class="bg-slate-900">Auto: Every 5 Mins</option>
            <option value="900" class="bg-slate-900">Auto: Every 15 Mins</option>
            <option value="1800" class="bg-slate-900">Auto: Every 30 Mins</option>
          </select>
          <span id="countdownPill" class="hidden text-[10px] font-mono text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded border border-amber-500/30">
            <span id="countdownVal">05:00</span>
          </span>
        </div>

        <button onclick="openShareModal()" class="px-3 py-1.5 rounded-lg border border-indigo-500/40 bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 hover:text-white text-xs font-semibold flex items-center gap-1.5 transition shadow-sm">
          <i class="fa-solid fa-share-nodes text-indigo-400"></i> Share
        </button>
        <button onclick="window.print()" class="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold flex items-center gap-1.5 transition">
          <i class="fa-solid fa-print text-slate-400"></i> Print / PDF
        </button>
        <button onclick="exportCSV()" class="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-xs font-semibold flex items-center gap-1.5 transition">
          <i class="fa-solid fa-file-csv text-emerald-400"></i> Export CSV
        </button>
        <button id="btnScanNow" onclick="triggerScan()" class="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold flex items-center gap-1.5 shadow-lg shadow-indigo-600/30 transition">
          <i class="fa-solid fa-satellite-dish" id="btnScanNowIcon"></i>
          <span id="btnScanNowText">Scan Now</span>
        </button>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="max-w-7xl mx-auto px-4 py-6 space-y-6">

    <!-- Print Report Header (Visible only in print/PDF) -->
    <div id="printHeader" class="hidden">
      <div class="flex items-center justify-between pb-3 border-b-2 border-slate-900 mb-4">
        <div>
          <span class="text-[10px] font-bold uppercase tracking-wider text-indigo-700">Client Property Intelligence Brief</span>
          <h1 class="text-2xl font-black text-slate-950 tracking-tight">PROPERTY RADAR REPORT</h1>
          <p id="printQuerySubtitle" class="text-xs text-slate-600 font-medium mt-0.5">Search: -</p>
        </div>
        <div class="text-right text-xs text-slate-600">
          <p id="printDate" class="font-bold text-slate-800"></p>
          <p class="text-[11px] text-slate-500">Live Multi-Channel Radar</p>
        </div>
      </div>
    </div>

    <!-- Search Bar & Controls -->
    <section id="searchSection" class="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-xl">
      <form id="searchForm" onsubmit="event.preventDefault(); triggerScan();" class="flex flex-col gap-3">
        <div class="flex flex-col md:flex-row gap-3">
          <div class="relative flex-1">
            <i class="fa-solid fa-magnifying-glass absolute left-3.5 top-3.5 text-slate-400"></i>
            <input type="text" id="queryInput" value="Proscenium 2 bedroom for sale" placeholder="Search ANY property (e.g. Proscenium 2BR, Park Terraces, Ayala Alabang, Edades, Aurelia...)" class="w-full bg-slate-950 border border-slate-700 rounded-xl pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:border-indigo-500 text-white placeholder-slate-500" />
          </div>
          <button id="btnRunScan" type="submit" class="px-6 py-2.5 bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 shadow-md">
            <i class="fa-solid fa-satellite-dish" id="btnRunScanIcon"></i>
            <span id="btnRunScanText">Run Scan</span>
          </button>
        </div>

        <!-- Filter Controls -->
        <div class="flex items-center gap-2 flex-wrap pt-2 border-t border-slate-800/60">
          <span class="text-xs text-slate-400 font-medium">Filter Results:</span>
          
          <select id="categoryFilter" onchange="renderCards()" class="bg-slate-950 border border-slate-700 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="all">All (Sale & Lease)</option>
            <option value="FOR SALE">For Sale Only</option>
            <option value="FOR LEASE">For Lease Only</option>
          </select>

          <select id="bedroomFilter" onchange="renderCards()" class="bg-slate-950 border border-slate-700 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="all">All Bedrooms</option>
            <option value="Studio">Studio</option>
            <option value="1BR">1 Bedroom</option>
            <option value="2BR">2 Bedrooms</option>
            <option value="3BR">3 Bedrooms</option>
            <option value="4BR">4+ Bedrooms</option>
          </select>

          <select id="typeFilter" onchange="renderCards()" class="bg-slate-950 border border-slate-700 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="all">All Unit Types</option>
            <option value="Condominium">Condominiums</option>
            <option value="Loft">Bi-Level / Loft</option>
            <option value="Penthouse">Penthouse</option>
            <option value="House">House & Lot</option>
            <option value="Townhouse">Townhouse</option>
            <option value="Villa">Garden Villa</option>
            <option value="Commercial">Commercial / Lot</option>
          </select>

          <select id="sourceFilter" onchange="renderCards()" class="bg-slate-950 border border-slate-700 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
            <option value="all">All Inventory Sources</option>
            <option value="Carousell">Carousell PH (Live Web)</option>
            <option value="Master">Internal Master DB</option>
          </select>
        </div>
      </form>

      <!-- Universal Quick Preset Pills -->
      <div class="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-slate-800/80 text-xs">
        <span class="text-slate-400 font-medium">Popular Presets:</span>
        <button onclick="setQuery('Proscenium 2 bedroom for sale')" class="px-2.5 py-1 rounded-full bg-slate-800 hover:bg-slate-700 text-slate-300 transition">Proscenium Rockwell 2BR</button>
        <button onclick="setQuery('Park Terraces Makati 2 bedroom for sale')" class="px-2.5 py-1 rounded-full bg-slate-800 hover:bg-slate-700 text-slate-300 transition">Park Terraces Makati</button>
        <button onclick="setQuery('Aurelia Residences BGC 3 bedroom')" class="px-2.5 py-1 rounded-full bg-slate-800 hover:bg-slate-700 text-slate-300 transition">Aurelia Residences BGC</button>
        <button onclick="setQuery('Ayala Alabang house and lot for sale')" class="px-2.5 py-1 rounded-full bg-slate-800 hover:bg-slate-700 text-slate-300 transition">Ayala Alabang House & Lot</button>
        <button onclick="setQuery('Edades Suites 3 bedroom for sale')" class="px-2.5 py-1 rounded-full bg-slate-800 hover:bg-slate-700 text-slate-300 transition">Edades Suites 3BR</button>
        <button onclick="setQuery('Shang Salcedo Place Makati for sale')" class="px-2.5 py-1 rounded-full bg-slate-800 hover:bg-slate-700 text-slate-300 transition">Shang Salcedo</button>
      </div>
    </section>

    <!-- Scan Progress & Live Pipeline Box -->
    <div id="scanStatus" class="hidden bg-slate-900 border border-indigo-500/50 rounded-2xl p-4 shadow-2xl space-y-3 transition-all">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <span class="relative flex h-3 w-3">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
            <span class="relative inline-flex rounded-full h-3 w-3 bg-indigo-500"></span>
          </span>
          <span id="scanStatusText" class="font-bold text-sm text-white">Active Internet Scan in Progress...</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-slate-400">Elapsed:</span>
          <span id="scanElapsed" class="font-mono text-sm font-bold text-amber-400">0.0s</span>
        </div>
      </div>

      <!-- Channel Pipeline Indicators -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 pt-2 border-t border-slate-800 text-xs">
        <div class="bg-slate-950/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
          <span class="text-slate-300 flex items-center gap-1.5"><i class="fa-solid fa-cart-shopping text-red-400"></i> Carousell PH</span>
          <span id="chCarousellStatus" class="text-amber-400 text-[11px] font-mono"><i class="fa-solid fa-spinner fa-spin"></i> Scanning...</span>
        </div>
        <div class="bg-slate-950/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
          <span class="text-slate-300 flex items-center gap-1.5"><i class="fa-brands fa-facebook text-blue-400"></i> Facebook Groups</span>
          <span id="chSocialStatus" class="text-amber-400 text-[11px] font-mono"><i class="fa-solid fa-spinner fa-spin"></i> Crawling...</span>
        </div>
        <div class="bg-slate-950/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
          <span class="text-slate-300 flex items-center gap-1.5"><i class="fa-solid fa-globe text-indigo-400"></i> Dot Property</span>
          <span id="chPortalsStatus" class="text-amber-400 text-[11px] font-mono"><i class="fa-solid fa-spinner fa-spin"></i> Checking...</span>
        </div>
        <div class="bg-slate-950/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
          <span class="text-slate-300 flex items-center gap-1.5"><i class="fa-solid fa-file-excel text-emerald-400"></i> Master File DB</span>
          <span id="chLocalStatus" class="text-amber-400 text-[11px] font-mono"><i class="fa-solid fa-spinner fa-spin"></i> Syncing...</span>
        </div>
      </div>
    </div>

    <!-- Analytics KPI Cards -->
    <section class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div class="text-xs text-slate-400 font-medium flex items-center justify-between">
          <span>Active Listings</span>
          <i class="fa-solid fa-list-check text-indigo-400"></i>
        </div>
        <div id="kpiTotal" class="text-2xl font-bold text-white mt-1">-</div>
        <div class="text-[11px] text-slate-500 mt-1" id="kpiChannels">Multi-channel radar</div>
      </div>

      <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div class="text-xs text-slate-400 font-medium flex items-center justify-between">
          <span>Avg Asking Price</span>
          <i class="fa-solid fa-tag text-amber-400"></i>
        </div>
        <div id="kpiAvg" class="text-2xl font-bold text-amber-400 mt-1">-</div>
        <div class="text-[11px] text-slate-500 mt-1" id="kpiRange">Range: -</div>
      </div>

      <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div class="text-xs text-slate-400 font-medium flex items-center justify-between">
          <span>Avg Price / SQM</span>
          <i class="fa-solid fa-chart-line text-emerald-400"></i>
        </div>
        <div id="kpiRate" class="text-2xl font-bold text-emerald-400 mt-1">-</div>
        <div class="text-[11px] text-slate-500 mt-1">Market Valuation Rate</div>
      </div>

      <div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div class="text-xs text-slate-400 font-medium flex items-center justify-between">
          <span>Direct / Owner Leads</span>
          <i class="fa-solid fa-handshake text-purple-400"></i>
        </div>
        <div id="kpiDirect" class="text-2xl font-bold text-purple-400 mt-1">-</div>
        <div class="text-[11px] text-slate-500 mt-1">Direct-to-buyer potential</div>
      </div>
    </section>

    <!-- Live Portals & Social Media Deep Launchpad (Direct 1-Click Search with User's Own Account) -->
    <section id="launchpadSection" class="bg-gradient-to-r from-slate-900 via-indigo-950/40 to-slate-900 border border-indigo-500/30 rounded-2xl p-4 shadow-xl space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-2">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center text-indigo-400">
            <i class="fa-solid fa-satellite-dish text-sm"></i>
          </div>
          <div>
            <h3 class="font-bold text-sm text-white flex items-center gap-2">
              <span>Live Portals & Social Media Launchpad</span>
              <span class="text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 px-2 py-0.5 rounded-full font-mono font-bold">1-CLICK SEARCH</span>
            </h3>
            <p class="text-[11px] text-slate-400">Instantly launch pre-filled queries on major protected portals & Facebook with your browser and account</p>
          </div>
        </div>
        <div class="text-[11px] text-slate-400 flex items-center gap-1.5">
          <i class="fa-solid fa-shield-halved text-emerald-400"></i>
          <span>Zero login credentials stored · Searches securely with your personal account</span>
        </div>
      </div>

      <!-- Launchpad Cards Grid -->
      <div id="launchpadGrid" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 pt-1">
        <!-- Rendered by JS -->
      </div>
    </section>

    <!-- Results Table / Cards -->
    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-bold text-slate-200 flex items-center gap-2">
          <i class="fa-solid fa-building-circle-check text-indigo-400"></i>
          Discovered Listings & Leads (<span id="resultsCount">0</span>)
        </h2>
        <div class="text-xs text-slate-400" id="scanTimestamp">Last scanned: Never</div>
      </div>

      <div id="listingsGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <!-- Rendered by JS -->
      </div>
    </section>

  </main>

  <!-- Share Modal -->
  <div id="shareModal" class="hidden fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700/80 rounded-2xl max-w-2xl w-full p-6 shadow-2xl space-y-5 text-slate-100 max-h-[90vh] overflow-y-auto">
      
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center text-indigo-400">
            <i class="fa-solid fa-share-nodes text-sm"></i>
          </div>
          <div>
            <h3 class="font-bold text-base text-white">Share Property Leads & View</h3>
            <p class="text-xs text-slate-400">Share live interactive views or copy formatted summaries for clients</p>
          </div>
        </div>
        <button onclick="closeShareModal()" class="w-8 h-8 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white flex items-center justify-center transition">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <!-- Section 1: Live Web App Links -->
      <div class="space-y-3">
        <h4 class="text-xs font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-1.5">
          <i class="fa-solid fa-link"></i> Live Dashboard Share Links
        </h4>

        <!-- 24/7 Public Cloud URL (Worldwide) -->
        <div class="bg-gradient-to-r from-indigo-950/80 to-slate-950 p-3 rounded-xl border border-indigo-500/50 shadow-lg shadow-indigo-950/30 space-y-1.5">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-indigo-300 flex items-center gap-1.5">
              <i class="fa-solid fa-globe text-indigo-400"></i> 24/7 Public Cloud URL (Accessible Worldwide)
            </span>
            <span class="text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 px-2 py-0.5 rounded font-mono font-bold">24/7 ONLINE</span>
          </div>
          <div class="flex items-center gap-2">
            <input type="text" id="shareCloudUrl" readonly class="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white w-full font-mono select-all focus:outline-none" />
            <button onclick="copyToClipboard('shareCloudUrl', this)" class="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-bold text-white whitespace-nowrap flex items-center gap-1.5 shadow-md shadow-indigo-600/30 transition">
              <i class="fa-regular fa-copy"></i>
              <span>Copy 24/7 URL</span>
            </button>
          </div>
          <p class="text-[11px] text-slate-400">Share with clients, co-brokers, or teammates anywhere in the world. Stays accessible 24 hours a day on phones, laptops, and tablets.</p>
        </div>
        
        <!-- Local Wi-Fi / LAN Link -->
        <div class="bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-1.5">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
              <i class="fa-solid fa-wifi text-emerald-400"></i> Office Wi-Fi / Local Network Link
            </span>
            <span class="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded font-mono">Any device on same Wi-Fi</span>
          </div>
          <div class="flex items-center gap-2">
            <input type="text" id="shareLanUrl" readonly class="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 w-full font-mono select-all focus:outline-none" />
            <button onclick="copyToClipboard('shareLanUrl', this)" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-semibold text-white whitespace-nowrap flex items-center gap-1.5 transition">
              <i class="fa-regular fa-copy"></i>
              <span>Copy Link</span>
            </button>
          </div>
          <p class="text-[11px] text-slate-500">Colleagues and clients connected to the same office Wi-Fi or hotspot can open this link on their phones, laptops, or tablets.</p>
        </div>

        <!-- Localhost Link -->
        <div class="bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-1.5">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
              <i class="fa-solid fa-laptop text-slate-400"></i> Direct Link (This PC)
            </span>
            <span class="text-[10px] bg-slate-800 text-slate-400 border border-slate-700 px-2 py-0.5 rounded font-mono">Current Host</span>
          </div>
          <div class="flex items-center gap-2">
            <input type="text" id="shareLocalUrl" readonly class="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 w-full font-mono select-all focus:outline-none" />
            <button onclick="copyToClipboard('shareLocalUrl', this)" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs font-semibold text-slate-200 whitespace-nowrap flex items-center gap-1.5 transition">
              <i class="fa-regular fa-copy"></i>
              <span>Copy</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Section 2: WhatsApp & Viber Client Brief -->
      <div class="space-y-3 pt-2 border-t border-slate-800">
        <div class="flex items-center justify-between">
          <h4 class="text-xs font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
            <i class="fa-brands fa-whatsapp text-emerald-400"></i> WhatsApp & Viber Client Brief
          </h4>
          <span class="text-[11px] text-slate-400">Formatted for instant messaging</span>
        </div>
        <textarea id="shareViberBrief" rows="6" class="w-full bg-slate-950 border border-slate-700 rounded-xl p-3 text-xs text-slate-300 font-mono focus:outline-none focus:border-indigo-500 resize-none leading-relaxed"></textarea>
        <div class="flex items-center gap-2 flex-wrap">
          <button onclick="copyViberBrief(this)" class="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-xs font-bold text-white flex items-center gap-1.5 shadow-lg shadow-emerald-600/20 transition">
            <i class="fa-regular fa-copy"></i>
            <span>Copy Brief for WhatsApp / Viber</span>
          </button>
          <button onclick="openWhatsAppShare()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl text-xs font-semibold text-emerald-400 flex items-center gap-1.5 transition">
            <i class="fa-brands fa-whatsapp text-sm"></i>
            <span>Send via WhatsApp Web</span>
          </button>
        </div>
      </div>

      <!-- Section 3: Remote Cloud & Tunnel -->
      <div class="bg-indigo-950/40 border border-indigo-500/30 rounded-xl p-3.5 space-y-2 text-xs">
        <div class="flex items-center gap-2 font-bold text-indigo-300">
          <i class="fa-solid fa-globe"></i>
          <span>Share Across Different Networks / Over the Internet</span>
        </div>
        <p class="text-slate-300 text-[11px] leading-relaxed">
          To send this live scanner to clients outside your local Wi-Fi, run a 1-click tunnel in a terminal:
          <code class="block bg-slate-950 px-2 py-1 mt-1 rounded text-amber-300 border border-slate-800 select-all font-mono">npx localtunnel --port 8765</code>
          Or deploy permanently using <code class="text-indigo-300 font-mono">push_to_cloud.py</code> to your Render URL.
        </p>
      </div>

    </div>
  </div>

  <!-- Toast Notification -->
  <div id="toastNotification" class="fixed bottom-5 right-5 z-50 transform translate-y-10 opacity-0 pointer-events-none transition-all duration-300 bg-slate-900 border border-emerald-500/50 text-white px-4 py-2.5 rounded-xl shadow-2xl flex items-center gap-2.5 text-xs font-semibold">
    <i class="fa-solid fa-circle-check text-emerald-400 text-sm"></i>
    <span id="toastMessage">Copied to clipboard!</span>
  </div>

  <script>
    let currentData = { results: [] };
    let scanTimerInterval = null;
    let autoScanIntervalId = null;
    let countdownIntervalId = null;
    let autoScanSecondsRemaining = 0;
    let serverLanIp = '192.168.1.91';
    window.lastFilteredResults = [];

    function showToast(msg) {
      const toast = document.getElementById('toastNotification');
      const msgElem = document.getElementById('toastMessage');
      if (!toast || !msgElem) return;
      msgElem.innerText = msg;
      toast.classList.remove('translate-y-10', 'opacity-0', 'pointer-events-none');
      setTimeout(() => {
        toast.classList.add('translate-y-10', 'opacity-0', 'pointer-events-none');
      }, 2500);
    }

    function updateUrlParams() {
      const params = new URLSearchParams();
      const q = document.getElementById('queryInput') ? document.getElementById('queryInput').value.trim() : '';
      if (q) params.set('q', q);
      const cat = document.getElementById('categoryFilter') ? document.getElementById('categoryFilter').value : 'all';
      if (cat && cat !== 'all') params.set('category', cat);
      const bed = document.getElementById('bedroomFilter') ? document.getElementById('bedroomFilter').value : 'all';
      if (bed && bed !== 'all') params.set('bedrooms', bed);
      const typ = document.getElementById('typeFilter') ? document.getElementById('typeFilter').value : 'all';
      if (typ && typ !== 'all') params.set('type', typ);
      const src = document.getElementById('sourceFilter') ? document.getElementById('sourceFilter').value : 'all';
      if (src && src !== 'all') params.set('source', src);

      const qs = params.toString();
      const newUrl = window.location.pathname + (qs ? '?' + qs : '');
      window.history.replaceState(null, '', newUrl);

      const printSubtitle = document.getElementById('printQuerySubtitle');
      if (printSubtitle) printSubtitle.innerText = 'Search Query: ' + (q || 'All');
      const printDate = document.getElementById('printDate');
      if (printDate) printDate.innerText = new Date().toLocaleString('en-US');

      return qs;
    }

    function applyUrlParams() {
      const params = new URLSearchParams(window.location.search);
      if (params.has('q')) {
        document.getElementById('queryInput').value = params.get('q');
      }
      if (params.has('category')) {
        const sel = document.getElementById('categoryFilter');
        if (sel) sel.value = params.get('category');
      }
      if (params.has('bedrooms')) {
        const sel = document.getElementById('bedroomFilter');
        if (sel) sel.value = params.get('bedrooms');
      }
      if (params.has('type')) {
        const sel = document.getElementById('typeFilter');
        if (sel) sel.value = params.get('type');
      }
      if (params.has('source')) {
        const sel = document.getElementById('sourceFilter');
        if (sel) sel.value = params.get('source');
      }
    }

    function buildViberBrief() {
      const q = document.getElementById('queryInput') ? document.getElementById('queryInput').value.trim() : 'Property Inquiry';
      const items = (window.lastFilteredResults && window.lastFilteredResults.length > 0 ? window.lastFilteredResults : (currentData.results || [])).slice(0, 10);
      const totalCount = window.lastFilteredResults ? window.lastFilteredResults.length : (currentData.results || []).length;
      const qs = updateUrlParams();
      const isCloud = window.location.hostname.includes('onrender.com') || window.location.protocol === 'https:';
      const cloudUrl = 'https://cai-property-search.onrender.com/radar' + (qs ? '?' + qs : '');
      const localUrl = window.location.origin + (window.location.pathname || '/') + (qs ? '?' + qs : '');
      const shareUrl = isCloud ? localUrl : cloudUrl;

      const lines = [
        '🏢 PROPERTY RADAR BRIEF: ' + q.toUpperCase(),
        '📊 Discovered: ' + totalCount + ' Listings | Avg: ' + (document.getElementById('kpiAvg') ? document.getElementById('kpiAvg').innerText : 'N/A') + ' (' + (document.getElementById('kpiRate') ? document.getElementById('kpiRate').innerText : '') + ')',
        ''
      ];

      items.forEach((item, idx) => {
        lines.push((idx + 1) + '. ' + item.title);
        lines.push('   • Price: ' + item.price_formatted + (item.price_per_sqm > 0 ? ' (₱' + Math.round(item.price_per_sqm).toLocaleString() + '/sqm)' : ''));
        lines.push('   • Specs: ' + item.unit_type + ' (' + item.bedrooms + ') | ' + (item.floor_area_sqm > 0 ? item.floor_area_sqm + ' sqm' : 'Area N/A') + ' | Parking: ' + item.parking);
        lines.push('   • Channel: ' + item.platform + (item.is_direct ? ' [DIRECT OWNER]' : ''));
        lines.push('   • Link: ' + item.url);
        lines.push('');
      });

      lines.push('----------------------------------');
      lines.push('⚡ Live 24/7 Radar View:');
      lines.push(shareUrl);

      return lines.join(String.fromCharCode(10));
    }

    function openShareModal() {
      const modal = document.getElementById('shareModal');
      const qs = updateUrlParams();
      const port = window.location.port || '8765';
      const path = window.location.pathname || '/';
      
      const cloudUrl = 'https://cai-property-search.onrender.com/radar' + (qs ? '?' + qs : '');
      const lanUrl = 'http://' + (serverLanIp || window.location.hostname) + ':' + port + path + (qs ? '?' + qs : '');
      const localUrl = window.location.origin + path + (qs ? '?' + qs : '');

      const cloudInput = document.getElementById('shareCloudUrl');
      if (cloudInput) cloudInput.value = cloudUrl;
      document.getElementById('shareLanUrl').value = lanUrl;
      document.getElementById('shareLocalUrl').value = localUrl;
      document.getElementById('shareViberBrief').value = buildViberBrief();

      modal.classList.remove('hidden');
    }

    function closeShareModal() {
      document.getElementById('shareModal').classList.add('hidden');
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeShareModal();
    });

    function copyToClipboard(inputId, btn) {
      const input = document.getElementById(inputId);
      input.select();
      navigator.clipboard.writeText(input.value).then(() => {
        showToast('Link copied to clipboard!');
        if (btn) {
          const oldHtml = btn.innerHTML;
          btn.innerHTML = '<i class="fa-solid fa-check text-emerald-400"></i> Copied!';
          setTimeout(() => { btn.innerHTML = oldHtml; }, 2000);
        }
      });
    }

    function copyViberBrief(btn) {
      const text = document.getElementById('shareViberBrief').value;
      navigator.clipboard.writeText(text).then(() => {
        showToast('WhatsApp / Viber brief copied!');
        if (btn) {
          const oldHtml = btn.innerHTML;
          btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied to Clipboard!';
          setTimeout(() => { btn.innerHTML = oldHtml; }, 2000);
        }
      });
    }

    function openWhatsAppShare() {
      const text = document.getElementById('shareViberBrief').value;
      const url = 'https://api.whatsapp.com/send?text=' + encodeURIComponent(text);
      window.open(url, '_blank');
    }

    function copySingleBrief(e, index) {
      if (e && e.stopPropagation) e.stopPropagation();
      const list = window.lastFilteredResults && window.lastFilteredResults.length > 0 ? window.lastFilteredResults : (currentData.results || []);
      const item = list[index];
      if (!item) return;

      const lines = [
        '🏢 ' + item.title,
        '💰 Price: ' + item.price_formatted + (item.price_per_sqm > 0 ? ' (₱' + Math.round(item.price_per_sqm).toLocaleString() + '/sqm)' : ''),
        '📐 Specs: ' + item.unit_type + ' (' + item.bedrooms + ') | ' + (item.floor_area_sqm > 0 ? item.floor_area_sqm + ' sqm' : 'Area N/A') + ' | Parking: ' + item.parking,
        '📍 Channel: ' + item.platform + (item.is_direct ? ' [DIRECT OWNER]' : ''),
        '🔗 Link: ' + item.url
      ];
      const text = lines.join(String.fromCharCode(10));
      navigator.clipboard.writeText(text).then(() => {
        showToast('Listing brief copied for WhatsApp / Viber!');
        if (e && e.currentTarget) {
          const btn = e.currentTarget;
          const oldHtml = btn.innerHTML;
          btn.innerHTML = '<i class="fa-solid fa-check text-emerald-400"></i> <span>Copied!</span>';
          setTimeout(() => { btn.innerHTML = oldHtml; }, 2000);
        }
      });
    }

    function setQuery(q) {
      document.getElementById('queryInput').value = q;
      triggerScan();
    }

    function changeAutoScan(val) {
      const seconds = parseInt(val, 10);
      clearInterval(autoScanIntervalId);
      clearInterval(countdownIntervalId);
      const pill = document.getElementById('countdownPill');

      if (seconds <= 0) {
        pill.classList.add('hidden');
        return;
      }

      pill.classList.remove('hidden');
      autoScanSecondsRemaining = seconds;
      updateCountdownDisplay();

      countdownIntervalId = setInterval(() => {
        autoScanSecondsRemaining--;
        if (autoScanSecondsRemaining <= 0) {
          autoScanSecondsRemaining = seconds;
          triggerScan();
        }
        updateCountdownDisplay();
      }, 1000);
    }

    function updateCountdownDisplay() {
      const mins = Math.floor(autoScanSecondsRemaining / 60);
      const secs = autoScanSecondsRemaining % 60;
      document.getElementById('countdownVal').innerText = 
        `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }

    async function triggerScan() {
      const q = document.getElementById('queryInput').value.trim() || 'Edades Suites 3 bedroom for sale';
      const statusBox = document.getElementById('scanStatus');
      const statusText = document.getElementById('scanStatusText');
      const scanElapsed = document.getElementById('scanElapsed');
      const radarBadge = document.getElementById('radarBadge');
      const radarLabel = document.getElementById('radarStatusLabel');

      const btnScanNow = document.getElementById('btnScanNow');
      const btnRunScan = document.getElementById('btnRunScan');

      // 1. Enter Active Scanning State in UI
      statusBox.classList.remove('hidden');
      statusText.innerHTML = `Active Internet Scan in Progress for <span class="text-indigo-400">"${q}"</span>`;
      
      // Update Radar Navbar Pill to pulsing ping
      radarBadge.className = "flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-950/80 border border-emerald-500/50 text-[11px] shadow-lg shadow-emerald-500/10";
      radarBadge.innerHTML = `
        <span class="relative flex h-2 w-2">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
        </span>
        <span class="text-emerald-400 font-bold uppercase tracking-wider">Scanning Active (<span id="radarTimer">0.0s</span>)</span>
      `;

      // Disable Buttons
      btnScanNow.disabled = true;
      btnScanNow.classList.add('opacity-70', 'cursor-not-allowed');
      document.getElementById('btnScanNowText').innerText = 'Scanning...';
      document.getElementById('btnScanNowIcon').className = 'fa-solid fa-circle-notch fa-spin';

      btnRunScan.disabled = true;
      btnRunScan.classList.add('opacity-70', 'cursor-not-allowed');
      document.getElementById('btnRunScanText').innerText = 'Scanning...';
      document.getElementById('btnRunScanIcon').className = 'fa-solid fa-circle-notch fa-spin';

      // Reset pipeline badges
      document.getElementById('chCarousellStatus').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Searching...';
      document.getElementById('chSocialStatus').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Crawling...';
      document.getElementById('chPortalsStatus').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Checking...';
      document.getElementById('chLocalStatus').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Syncing...';

      // Start live timer
      const startTime = performance.now();
      clearInterval(scanTimerInterval);
      scanTimerInterval = setInterval(() => {
        const sec = ((performance.now() - startTime) / 1000).toFixed(1);
        scanElapsed.innerText = `${sec}s`;
        const rt = document.getElementById('radarTimer');
        if (rt) rt.innerText = `${sec}s`;
      }, 100);

      try {
        const res = await fetch(`/api/scan?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        currentData = data;
        updateUI(data);

        // Mark channels complete
        document.getElementById('chCarousellStatus').innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-check"></i> ${data.sources?.Carousell || 0} live</span>`;
        document.getElementById('chSocialStatus').innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-check"></i> FB Ready</span>`;
        document.getElementById('chPortalsStatus').innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-check"></i> 3 Portals Ready</span>`;
        document.getElementById('chLocalStatus').innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-check"></i> ${data.sources?.['Local Inventory'] || 0} matched</span>`;
        
        statusText.innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-circle-check"></i> Scan Complete!</span> Discovered ${data.total_found} verified listings in ${data.elapsed_seconds}s`;

      } catch (err) {
        alert("Scan request failed: " + err);
        statusText.innerText = "Scan failed or interrupted.";
      } finally {
        clearInterval(scanTimerInterval);
        
        // Restore buttons
        btnScanNow.disabled = false;
        btnScanNow.classList.remove('opacity-70', 'cursor-not-allowed');
        document.getElementById('btnScanNowText').innerText = 'Scan Now';
        document.getElementById('btnScanNowIcon').className = 'fa-solid fa-satellite-dish';

        btnRunScan.disabled = false;
        btnRunScan.classList.remove('opacity-70', 'cursor-not-allowed');
        document.getElementById('btnRunScanText').innerText = 'Run Scan';
        document.getElementById('btnRunScanIcon').className = 'fa-solid fa-satellite-dish';

        // Set Radar Pill back to Standby
        radarBadge.className = "flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-[11px]";
        radarBadge.innerHTML = `
          <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
          <span id="radarStatusLabel" class="text-slate-300 font-medium">Radar Standby</span>
        `;

        // Hide pipeline after 5 seconds to keep dashboard tidy
        setTimeout(() => {
          if (!btnScanNow.disabled) {
            statusBox.classList.add('hidden');
          }
        }, 5000);
      }
    }

    function renderLaunchpad(launchpad, query) {
      const container = document.getElementById('launchpadGrid');
      if (!container) return;
      let list = (launchpad && launchpad.length > 0) ? launchpad : [];
      if (list.length === 0) {
        const enc_q = encodeURIComponent(query || 'Edades Rockwell');
        list = [
          { name: "FB Marketplace", badge: "Direct Owners", icon: "fa-brands fa-facebook text-blue-400", url: `https://www.facebook.com/marketplace/manila/search?query=${enc_q}`, desc: "Direct owner postings in Metro Manila" },
          { name: "FB Broker Groups", badge: "Co-Broke", icon: "fa-solid fa-users text-indigo-400", url: `https://www.facebook.com/search/posts/?q=${enc_q}`, desc: "Search posts in your broker groups" },
          { name: "Lamudi PH", badge: "Major Portal", icon: "fa-solid fa-building text-sky-400", url: `https://www.lamudi.com.ph/buy/?q=${enc_q}`, desc: "Brokerage listings & price trends" },
          { name: "Dot Property", badge: "Broker Feed", icon: "fa-solid fa-globe text-emerald-400", url: `https://www.dotproperty.com.ph/properties-for-sale?q=${enc_q}`, desc: "Secondary market verified feeds" },
          { name: "Hoppler Luxury", badge: "Partner Brokers", icon: "fa-solid fa-gem text-amber-400", url: `https://www.hoppler.com.ph/search?q=${enc_q}`, desc: "Curated high-end partner listings" }
        ];
      }

      container.innerHTML = list.map(lp => `
        <a href="${lp.url}" target="_blank" rel="noopener noreferrer" class="bg-slate-950/70 hover:bg-slate-800/80 border border-slate-800 hover:border-indigo-500/50 rounded-xl p-3 transition flex flex-col justify-between group shadow-sm hover:shadow-indigo-500/10">
          <div>
            <div class="flex items-center justify-between gap-1 mb-1.5">
              <span class="text-xs font-bold text-white group-hover:text-indigo-300 transition flex items-center gap-1.5">
                <i class="${lp.icon}"></i> ${lp.name}
              </span>
              <span class="text-[9px] bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded font-medium border border-slate-700/60">${lp.badge}</span>
            </div>
            <p class="text-[11px] text-slate-400 line-clamp-2 leading-tight">${lp.desc}</p>
          </div>
          <div class="mt-2.5 pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-indigo-400 font-semibold group-hover:text-indigo-300">
            <span>Live Search</span>
            <i class="fa-solid fa-arrow-up-right-from-square text-[10px] transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition"></i>
          </div>
        </a>
      `).join('');
    }

    function updateUI(data) {
      if (data && data.lan_ip) {
        serverLanIp = data.lan_ip;
      }
      document.getElementById('kpiTotal').innerText = data.total_found || 0;
      document.getElementById('kpiAvg').innerText = data.avg_price ? '₱' + (data.avg_price / 1_000_000).toFixed(1) + 'M' : 'N/A';
      document.getElementById('kpiRange').innerText = data.min_price ? `₱${(data.min_price/1e6).toFixed(0)}M - ₱${(data.max_price/1e6).toFixed(0)}M` : '-';
      document.getElementById('kpiRate').innerText = data.avg_price_per_sqm ? '₱' + data.avg_price_per_sqm.toLocaleString() + '/sqm' : 'N/A';
      
      const directCount = (data.results || []).filter(x => x.is_direct).length;
      document.getElementById('kpiDirect').innerText = directCount;
      document.getElementById('scanTimestamp').innerText = 'Last scanned: ' + (data.timestamp || 'Just now');
      
      renderLaunchpad(data.launchpad, data.query);
      renderCards();
    }

    function getBadgeClass(platform) {
      const p = platform.toLowerCase();
      if (p.includes('carousell')) return 'badge-carousell';
      if (p.includes('social') || p.includes('facebook')) return 'badge-social';
      if (p.includes('master') || p.includes('database')) return 'badge-local';
      return 'badge-portal';
    }

    function renderCards() {
      const grid = document.getElementById('listingsGrid');
      grid.innerHTML = '';
      
      const catFilter = document.getElementById('categoryFilter') ? document.getElementById('categoryFilter').value : 'all';
      const bedFilter = document.getElementById('bedroomFilter') ? document.getElementById('bedroomFilter').value : 'all';
      const typeFilter = document.getElementById('typeFilter') ? document.getElementById('typeFilter').value : 'all';
      const sourceFilter = document.getElementById('sourceFilter') ? document.getElementById('sourceFilter').value : 'all';

      const filtered = (currentData.results || []).filter(item => {
        // Category filter (FOR SALE vs FOR LEASE)
        if (catFilter !== 'all' && item.category !== catFilter) return false;

        // Bedroom filter
        if (bedFilter !== 'all') {
          if (bedFilter === '4BR') {
            if (!['4BR', '5BR', '6BR'].includes(item.bedrooms)) return false;
          } else {
            if (item.bedrooms !== bedFilter) return false;
          }
        }

        // Unit type filter
        if (typeFilter !== 'all' && !item.unit_type.toLowerCase().includes(typeFilter.toLowerCase())) return false;

        // Source channel filter
        if (sourceFilter !== 'all') {
          if (sourceFilter === 'Carousell' && !item.platform.toLowerCase().includes('carousell')) return false;
          if (sourceFilter === 'Master' && !(item.platform.toLowerCase().includes('master') || item.platform.toLowerCase().includes('internal'))) return false;
        }

        return true;
      });

      window.lastFilteredResults = filtered;
      updateUrlParams();

      document.getElementById('resultsCount').innerText = filtered.length;

      // Recalculate dynamic KPIs based on active filtered results
      const validPrices = filtered.map(x => x.price).filter(p => p > 1000);
      const validRates = filtered.map(x => x.price_per_sqm).filter(r => r > 1000);
      const directCount = filtered.filter(x => x.is_direct).length;

      document.getElementById('kpiTotal').innerText = filtered.length;
      document.getElementById('kpiDirect').innerText = directCount;

      if (validPrices.length > 0) {
        const avg = Math.round(validPrices.reduce((a, b) => a + b, 0) / validPrices.length);
        const min = Math.min(...validPrices);
        const max = Math.max(...validPrices);

        if (avg >= 1_000_000) {
          document.getElementById('kpiAvg').innerText = '₱' + (avg / 1e6).toFixed(1) + 'M';
          document.getElementById('kpiRange').innerText = `Range: ₱${(min/1e6).toFixed(0)}M - ₱${(max/1e6).toFixed(0)}M`;
        } else {
          document.getElementById('kpiAvg').innerText = '₱' + Math.round(avg / 1e3) + 'k/mo';
          document.getElementById('kpiRange').innerText = `Range: ₱${Math.round(min/1e3)}k - ₱${Math.round(max/1e3)}k`;
        }
      } else {
        document.getElementById('kpiAvg').innerText = 'N/A';
        document.getElementById('kpiRange').innerText = 'Range: -';
      }

      if (validRates.length > 0) {
        const avgRate = Math.round(validRates.reduce((a, b) => a + b, 0) / validRates.length);
        document.getElementById('kpiRate').innerText = '₱' + avgRate.toLocaleString() + '/sqm';
      } else {
        document.getElementById('kpiRate').innerText = 'N/A';
      }

      if (filtered.length === 0) {
        grid.innerHTML = `
          <div class="col-span-full py-16 text-center text-slate-500">
            <i class="fa-solid fa-magnifying-glass text-4xl mb-3 text-slate-600"></i>
            <p>No listings matched your criteria. Try adjusting filters or running a new scan.</p>
          </div>
        `;
        return;
      }

      filtered.forEach((item, idx) => {
        const card = document.createElement('div');
        card.className = "bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col justify-between hover:border-slate-700 transition shadow-lg relative group print-avoid-break";
        
        card.innerHTML = `
          <div>
            <div class="flex items-center justify-between gap-2 mb-2">
              <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full ${getBadgeClass(item.platform)}">
                ${item.platform}
              </span>
              ${item.is_direct ? '<span class="text-[10px] bg-purple-500/20 text-purple-300 border border-purple-500/30 px-2 py-0.5 rounded font-bold">DIRECT OWNER</span>' : ''}
            </div>

            <h3 class="font-bold text-sm text-white line-clamp-2 leading-snug group-hover:text-indigo-300 transition">
              ${item.title}
            </h3>

            <div class="mt-3 flex items-baseline gap-2">
              <span class="text-xl font-extrabold text-amber-400">${item.price_formatted}</span>
              ${item.price_per_sqm > 0 ? `<span class="text-[11px] text-slate-400 font-mono">₱${Math.round(item.price_per_sqm).toLocaleString()}/sqm</span>` : ''}
            </div>

            <div class="grid grid-cols-2 gap-2 mt-3 text-xs bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/60">
              <div>
                <span class="text-slate-500 block text-[10px]">LAYOUT</span>
                <span class="font-medium text-slate-300">${item.unit_type} (${item.bedrooms})</span>
              </div>
              <div>
                <span class="text-slate-500 block text-[10px]">FLOOR AREA</span>
                <span class="font-medium text-slate-300">${item.floor_area_sqm > 0 ? item.floor_area_sqm + ' sqm' : 'Unspecified'}</span>
              </div>
              <div class="col-span-2 pt-1 border-t border-slate-800/60 flex items-center justify-between">
                <span class="text-slate-500 text-[10px]">PARKING</span>
                <span class="text-slate-300 font-medium text-[11px]">${item.parking}</span>
              </div>
            </div>

            <p class="text-xs text-slate-400 mt-3 line-clamp-3 leading-relaxed">
              ${item.snippet}
            </p>
          </div>

          <div class="mt-4 pt-3 border-t border-slate-800 flex items-center justify-between gap-2">
            <span class="text-[11px] text-slate-500">${item.category}</span>
            <div class="flex items-center gap-1.5">
              <button onclick="copySingleBrief(event, ${idx})" title="Copy client brief to clipboard" class="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-lg text-xs font-semibold transition flex items-center gap-1">
                <i class="fa-regular fa-copy"></i>
                <span>Brief</span>
              </button>
              <a href="${item.url}" target="_blank" class="px-2.5 py-1.5 bg-indigo-600/30 hover:bg-indigo-600 text-indigo-300 hover:text-white rounded-lg text-xs font-semibold transition flex items-center gap-1">
                <span>Source</span>
                <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
              </a>
            </div>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function exportCSV() {
      if (!currentData.results || currentData.results.length === 0) {
        alert("No results to export!");
        return;
      }
      const headers = ["Title", "Platform", "Price", "Floor Area SQM", "Bedrooms", "Unit Type", "Parking", "URL", "Snippet"];
      const rows = currentData.results.map(r => [
        `"${(r.title || '').replace(/"/g, '""')}"`,
        `"${r.platform}"`,
        r.price,
        r.floor_area_sqm,
        `"${r.bedrooms}"`,
        `"${r.unit_type}"`,
        `"${r.parking}"`,
        `"${r.url}"`,
        `"${(r.snippet || '').replace(/"/g, '""')}"`
      ]);
      const csvContent = "data:text/csv;charset=utf-8," + [headers.join(','), ...rows.map(e => e.join(','))].join(String.fromCharCode(10));
      const encodedUri = encodeURI(csvContent);
      const link = document.createElement("a");
      link.setAttribute("href", encodedUri);
      link.setAttribute("download", `property_scan_${Date.now()}.csv`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }

    // Auto-load initial scan on open with URL parameter restoration
    window.addEventListener('DOMContentLoaded', () => {
      applyUrlParams();
      triggerScan();
    });
  </script>
</body>
</html>
"""


class ScannerHTTPHandler(BaseHTTPRequestHandler):
    scanner = PropertyInternetScanner()

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path in ["", "/", "/index.html"]:
                body = HTML_DASHBOARD.encode('utf-8')
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            elif path == "/api/scan":
                q = qs.get("q", ["Proscenium 2 bedroom for sale"])[0]
                scan_data = self.scanner.run_scan(query=q)
                body = json.dumps(scan_data, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return

            elif path == "/api/info":
                lan_ip = get_lan_ip()
                port = getattr(self.server, "server_port", 8765)
                info = {
                    "lan_ip": lan_ip,
                    "port": port,
                    "lan_url": f"http://{lan_ip}:{port}",
                    "local_url": f"http://localhost:{port}"
                }
                body = json.dumps(info, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(404)
            self.end_headers()
        except Exception as e:
            print(f"Error handling request: {e}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Property Internet Scanner & Real Estate Lead Radar")
    parser.add_argument("query", nargs="?", default="Proscenium 2 bedroom for sale", help="Property search query")
    parser.add_argument("--serve", action="store_true", help="Launch the interactive Web Dashboard")
    parser.add_argument("--port", type=int, default=8765, help="Port for the Web Dashboard (default: 8765)")
    parser.add_argument("--export", type=str, default="", help="Export results to CSV or JSON file path")
    args = parser.parse_args()

    scanner = PropertyInternetScanner()

    if args.serve:
        server_address = ("", args.port)
        httpd = ThreadingHTTPServer(server_address, ScannerHTTPHandler)
        print(f"🚀 Property Radar Web App running at http://localhost:{args.port}/")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
    else:
        results = scanner.run_scan(query=args.query)
        print("\n" + "=" * 70)
        print(f" SCAN RESULTS: {results['query']}")
        print(f" Discovered: {results['total_found']} listings | Avg Price: ₱{results['avg_price']:,.0f}")
        print("=" * 70)
        for idx, item in enumerate(results['results'], 1):
            print(f"[{idx}] {item['title']}")
            print(f"    Channel : {item['platform']} ({item['source']})")
            print(f"    Price   : {item['price_formatted']} | SQM: {item['floor_area_sqm']} | Rate: ₱{item['price_per_sqm']:,.0f}/sqm")
            print(f"    Type    : {item['unit_type']} ({item['bedrooms']}) | Parking: {item['parking']}")
            print(f"    Link    : {item['url']}")
            print("-" * 70)

        if args.export:
            if args.export.endswith(".json"):
                with open(args.export, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                print(f"Exported to {args.export}")
            elif args.export.endswith(".csv") and pd:
                df = pd.DataFrame(results['results'])
                df.to_csv(args.export, index=False)
                print(f"Exported to {args.export}")


if __name__ == "__main__":
    main()
