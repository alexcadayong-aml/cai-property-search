"""
Multi-Column Property Search Engine
====================================
Accepts any keyword or phrase and searches across ALL columns of the synced sheets.
Features:
  - Universal column searching (examines every single column in the record)
  - Multi-token searching (e.g. 'Two Roxas 34A' matches across columns)
  - Column match tracking (reports which columns matched)
  - Relevance ranking (exact matches, title/unit matches score higher)
  - Filter by Sheet (Rent / Sale) and Availability (Available, Leased, Sold, etc.)
  - Highlighting helpers
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
    # Replace common separators with space, but keep standard alphanumeric and currency
    tokens = re.findall(r'[A-Za-z0-9₱$€£.,%+-]+', query.lower())
    return [t.strip('.,') for t in tokens if len(t.strip('.,')) > 0]


class PropertySearchEngine:
    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        self.records = records or []

    def set_records(self, records: List[Dict[str, Any]]):
        self.records = records

    def search(
        self,
        query: str = "",
        sheet_filter: str = "all",       # "all" | "For Rent" | "For Sale"
        availability_filter: str = "all",# "all" | "available" | "leased" | "sold"
        directness_filter: str = "all",  # "all" | "direct" | "one_away"
        limit: int = 100,
        enable_fuzzy: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Search across all records and all columns.
        """
        query_str = (query or "").strip().lower()
        tokens = clean_query_tokens(query_str)

        results = []

        for rec in self.records:
            # 1. Filter by sheet
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

            # If empty query, return all matching filtered records
            if not tokens:
                results.append({
                    **rec,
                    "matched_columns": [],
                    "relevance_score": 100
                })
                continue

            # 3. Search across all columns
            matched_columns = set()
            tokens_found = {t: False for t in tokens}
            relevance_score = 0

            # Gather all searchable text fields
            all_cols = rec.get("all_columns", {})
            
            # Check normalized top fields first for higher scoring
            top_fields = [
                ("unit_no_address", rec.get("unit_no_address", ""), 25),
                ("property_description", rec.get("property_description", ""), 20),
                ("price", rec.get("price", ""), 15),
                ("availability", rec.get("availability", ""), 10),
                ("source_or_listing", rec.get("source_or_listing", ""), 10),
                ("sheet_source", rec.get("sheet_source", ""), 5)
            ]

            for field_name, field_val, weight in top_fields:
                val_lower = str(field_val).lower()
                if not val_lower:
                    continue
                # Full exact phrase match
                if query_str and query_str in val_lower:
                    relevance_score += weight * 2
                    matched_columns.add(field_name.replace("_", " ").title())
                for t in tokens:
                    if t in val_lower:
                        tokens_found[t] = True
                        relevance_score += weight
                        matched_columns.add(field_name.replace("_", " ").title())

            # Now check all original raw columns
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

            # Check if all tokens were matched somewhere across any column
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
                results.append({
                    **rec,
                    "matched_columns": sorted(list(matched_columns)),
                    "relevance_score": relevance_score
                })

        # Sort by relevance score descending
        results.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)

        if limit and limit > 0:
            return results[:limit]
        return results
