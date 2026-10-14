"""
Multi-Column Property Search Engine
====================================
Accepts keyword / phrase queries and searches across ALL columns of the synced sheets.
Features:
  - Universal column searching (examines every single column in the record)
  - Multi-token searching (e.g. 'Two Roxas 34A' matches across columns)
  - Dedicated Location filtering (e.g. 'Makati', 'Taguig', 'BGC', 'Arca South')
  - Bedroom-aware matching (e.g. '2BR', 'Studio', '3 Bedroom' maps to TYPE column & description)
  - Quick bedroom filter ('studio', '1', '2', '3', '4+')
  - Category filter ('Condominiums', 'House and Lots', 'Office Spaces', 'Commercial')
  - Price range filter (Min / Max price)
  - Column match tracking (reports which columns matched)
  - Relevance ranking (exact matches, title/unit matches score higher)
  - Filter by Sheet (Rent / Sale) and Availability (Available, Leased, Sold, etc.)
"""

import re
from typing import List, Dict, Any, Optional

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


def clean_query_tokens(query: str) -> List[str]:
    """Splits query into normalized search tokens."""
    if not query:
        return []
    tokens = re.findall(r'[A-Za-z0-9₱$€£%+-]+', query.lower())
    return [t.strip('.,') for t in tokens if len(t.strip('.,')) > 0]


def parse_bedroom_tokens(tokens: List[str]) -> Dict[str, str]:
    """
    Detects if user searched for bedroom terms (e.g. '2br', '2 bedroom', 'studio')
    Returns a dict mapping the token to the normalized bedroom value ('studio', '1', '2', '3', '4', etc.)
    """
    detected = {}
    for t in tokens:
        t_clean = t.lower().replace('-', '').replace(' ', '')
        if t_clean in ['studio']:
            detected[t] = 'studio'
        elif t_clean in ['1br', '1bed', '1bedroom']:
            detected[t] = '1'
        elif t_clean in ['2br', '2bed', '2bedroom']:
            detected[t] = '2'
        elif t_clean in ['3br', '3bed', '3bedroom']:
            detected[t] = '3'
        elif t_clean in ['4br', '4bed', '4bedroom']:
            detected[t] = '4'
        elif t_clean in ['5br', '5bed', '5bedroom']:
            detected[t] = '5'
    return detected


def match_bedroom_value(rec: Dict[str, Any], target_bed: str) -> bool:
    """Checks if a record matches a specific bedroom count ('studio', '1', '2', '3', '4+')."""
    target = target_bed.lower().strip()
    type_col = str(rec.get("all_columns", {}).get("TYPE", "")).lower().strip()
    desc = str(rec.get("property_description", "")).lower()

    if target in ["studio"]:
        return type_col == "studio" or "studio" in desc or "studio" in str(rec.get("unit_no_address", "")).lower()
    elif target in ["1", "1br"]:
        return type_col == "1" or any(p in desc for p in ["1br", "1 br", "1 bedroom", "1-bedroom", "one bedroom", "1 bed"])
    elif target in ["2", "2br"]:
        return type_col == "2" or any(p in desc for p in ["2br", "2 br", "2 bedroom", "2-bedroom", "two bedroom", "2 bed"])
    elif target in ["3", "3br"]:
        return type_col == "3" or any(p in desc for p in ["3br", "3 br", "3 bedroom", "3-bedroom", "three bedroom", "3 bed"])
    elif target in ["4", "4+", "4br"]:
        return type_col in ["4", "5", "6"] or any(p in desc for p in ["4br", "4 br", "4 bedroom", "4-bedroom", "four bedroom", "5br", "5 bedroom"])
    return True


def parse_numeric_price(price_str: Any) -> Optional[float]:
    """Extracts a numeric float value from diverse price representations."""
    if price_str is None:
        return None
    s = str(price_str).replace(',', '').strip()
    if not s or s.lower() == 'nan':
        return None
    # Check for Millions (e.g. 175M, 39M)
    m_match = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*m\b', s, re.I)
    if m_match:
        try:
            return float(m_match.group(1)) * 1_000_000
        except ValueError:
            pass
    # Check for Thousands (e.g. 350K, 60K)
    k_match = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*k\b', s, re.I)
    if k_match:
        try:
            return float(k_match.group(1)) * 1_000
        except ValueError:
            pass
    # Standard numbers
    num_match = re.search(r'([0-9]+(?:\.[0-9]+)?)', s)
    if num_match:
        try:
            val = float(num_match.group(1))
            return val
        except ValueError:
            pass
    return None


class PropertySearchEngine:
    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        self.records = records or []

    def set_records(self, records: List[Dict[str, Any]]):
        self.records = records

    def search(
        self,
        query: str = "",
        location_query: str = "",        # Dedicated location / city / area search
        bedroom_filter: str = "all",     # "all" | "studio" | "1" | "2" | "3" | "4+"
        category_filter: str = "all",    # "all" | "condo" | "house" | "office" | "commercial"
        sheet_filter: str = "all",       # "all" | "For Rent" | "For Sale"
        availability_filter: str = "all",# "all" | "available" | "leased" | "sold"
        directness_filter: str = "all",  # "all" | "direct" | "one_away"
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: str = "date_desc",       # "date_desc" | "relevance" | "price_asc" | "price_desc" | "date_asc"
        limit: int = 150,
        enable_fuzzy: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Search across all records using multi-criteria filtering:
        Primary keywords, Location keywords, Bedroom count, Category, Sheet, Price.
        """
        query_str = (query or "").strip().lower()
        loc_str = (location_query or "").strip().lower()

        tokens = clean_query_tokens(query_str)
        loc_tokens = clean_query_tokens(loc_str)
        bed_token_map = parse_bedroom_tokens(tokens)

        results = []

        for rec in self.records:
            # 1. Filter by sheet (Rent / Sale)
            if sheet_filter and sheet_filter.lower() != "all":
                if sheet_filter.lower() not in rec.get("sheet_source", "").lower():
                    continue

            # 2. Filter by availability
            if availability_filter and availability_filter.lower() != "all":
                rec_avail = rec.get("availability", "").lower()
                target_avail = availability_filter.lower()
                if target_avail == "available":
                    if "avail" not in rec_avail and "vacant" not in rec_avail and "for sale" not in rec_avail and "for lease" not in rec_avail:
                        continue
                elif target_avail == "leased":
                    if "lease" not in rec_avail and "rented" not in rec_avail:
                        continue
                elif target_avail == "sold":
                    if "sold" not in rec_avail:
                        continue
                elif target_avail not in rec_avail:
                    continue

            # 3. Filter by listing directness (Direct vs One Away)
            if directness_filter and directness_filter.lower() != "all":
                df_val = directness_filter.lower()
                is_one_away = rec.get("is_one_away", False)
                if df_val in ["direct", "direct_only"]:
                    if is_one_away:
                        continue
                elif df_val in ["one_away", "one_away_only", "1_away", "one away"]:
                    if not is_one_away:
                        continue

            # 4. Filter by Bedroom criteria (if selected)
            if bedroom_filter and bedroom_filter.lower() != "all":
                if not match_bedroom_value(rec, bedroom_filter):
                    continue

            # 5. Filter by Category criteria (Condo, House & Lot, Office, Commercial)
            if category_filter and category_filter.lower() != "all":
                cat_target = category_filter.lower()
                rec_cat = str(rec.get("category", "")).lower()
                desc = str(rec.get("property_description", "")).lower()
                if cat_target in ["condo", "condominium", "condominiums"]:
                    if not ("condo" in rec_cat or "condo" in desc or "tower" in desc or "building" in desc):
                        continue
                elif cat_target in ["house", "house and lot", "townhouse"]:
                    if not ("house" in rec_cat or "lot" in rec_cat or "townhouse" in rec_cat or "village" in desc or "subdivision" in desc):
                        continue
                elif cat_target in ["office", "office space", "office spaces"]:
                    if not ("office" in rec_cat or "commercial" in rec_cat or "office" in desc):
                        continue
                elif cat_target in ["commercial"]:
                    if not ("commercial" in rec_cat or "commercial" in desc or "retail" in desc):
                        continue

            # 6. Filter by Price Range
            if min_price is not None or max_price is not None:
                numeric_p = parse_numeric_price(rec.get("price"))
                if numeric_p is not None:
                    if min_price is not None and numeric_p < min_price:
                        continue
                    if max_price is not None and numeric_p > max_price:
                        continue

            # 7. Check Location Filter (Search Bar 2)
            if loc_tokens:
                loc_matched_all = True
                loc_text = " ".join([
                    str(rec.get("location", "")),
                    str(rec.get("unit_no_address", "")),
                    str(rec.get("property_description", "")),
                    str(rec.get("all_columns", {}).get("LOCATION", "")),
                    str(rec.get("all_columns", {}).get("CITY", "")),
                    str(rec.get("all_columns", {}).get("AREA", ""))
                ]).lower()

                for lt in loc_tokens:
                    if lt not in loc_text:
                        loc_matched_all = False
                        break
                if not loc_matched_all:
                    continue

            # If empty primary query, include this record
            if not tokens:
                results.append({
                    **rec,
                    "matched_columns": ["Location Filter"] if loc_tokens else [],
                    "relevance_score": 100
                })
                continue

            # 8. Search Primary Query across all columns (Search Bar 1)
            matched_columns = set()
            tokens_found = {t: False for t in tokens}
            relevance_score = 0
            all_cols = rec.get("all_columns", {})
            type_val = str(all_cols.get("TYPE", "")).lower().strip()

            top_fields = [
                ("unit_no_address", rec.get("unit_no_address", ""), 25),
                ("property_description", rec.get("property_description", ""), 20),
                ("price", rec.get("price", ""), 15),
                ("location", rec.get("location", ""), 15),
                ("availability", rec.get("availability", ""), 10),
                ("source_or_listing", rec.get("source_or_listing", ""), 10),
                ("sheet_source", rec.get("sheet_source", ""), 5)
            ]

            for field_name, field_val, weight in top_fields:
                val_lower = str(field_val).lower()
                if not val_lower:
                    continue
                if query_str and query_str in val_lower:
                    relevance_score += weight * 2
                    matched_columns.add(field_name.replace("_", " ").title())
                for t in tokens:
                    if t in val_lower:
                        tokens_found[t] = True
                        relevance_score += weight
                        matched_columns.add(field_name.replace("_", " ").title())

            # Raw columns check + Bedroom-aware check
            for col_name, col_val in all_cols.items():
                if not col_val:
                    continue
                col_lower = str(col_val).lower()
                if query_str and query_str in col_lower:
                    relevance_score += 15
                    matched_columns.add(col_name)
                for t in tokens:
                    if t in col_lower:
                        tokens_found[t] = True
                        relevance_score += 5
                        matched_columns.add(col_name)

            # Check bedroom intent for tokens that didn't match directly
            for t, bed_val in bed_token_map.items():
                if not tokens_found[t]:
                    if match_bedroom_value(rec, bed_val):
                        tokens_found[t] = True
                        relevance_score += 20
                        matched_columns.add("Bedroom Type")

            all_tokens_matched = all(tokens_found.values())

            # Optional fuzzy matching if not all tokens matched
            if not all_tokens_matched and enable_fuzzy and HAS_RAPIDFUZZ:
                combined_text = " ".join([str(v) for v in all_cols.values() if v])
                ratio = fuzz.partial_ratio(query_str, combined_text.lower())
                if ratio >= 80:
                    all_tokens_matched = True
                    relevance_score += int(ratio / 2)
                    matched_columns.add("Fuzzy Match")

            if all_tokens_matched:
                if loc_tokens:
                    matched_columns.add("Location")
                results.append({
                    **rec,
                    "matched_columns": sorted(list(matched_columns)),
                    "relevance_score": relevance_score
                })

        # Sort results (Most recently updated first by default)
        if sort_by == "date_desc":
            results.sort(
                key=lambda r: (1 if r.get("date_updated_iso") else 0, r.get("date_updated_iso", ""), r.get("relevance_score", 0)),
                reverse=True
            )
        elif sort_by == "relevance":
            results.sort(
                key=lambda r: (r.get("relevance_score", 0), 1 if r.get("date_updated_iso") else 0, r.get("date_updated_iso", "")),
                reverse=True
            )
        elif sort_by == "price_asc":
            def p_asc(r):
                p = parse_numeric_price(r.get("price"))
                return (0 if p is not None else 1, p if p is not None else 0)
            results.sort(key=p_asc)
        elif sort_by == "price_desc":
            def p_desc(r):
                p = parse_numeric_price(r.get("price"))
                return (1 if p is not None else 0, p if p is not None else 0)
            results.sort(key=p_desc, reverse=True)
        elif sort_by == "date_asc":
            results.sort(
                key=lambda r: (0 if r.get("date_updated_iso") else 1, r.get("date_updated_iso", "9999-99-99"))
            )
        else:
            results.sort(
                key=lambda r: (1 if r.get("date_updated_iso") else 0, r.get("date_updated_iso", ""), r.get("relevance_score", 0)),
                reverse=True
            )

        if limit and limit > 0:
            return results[:limit]
        return results
