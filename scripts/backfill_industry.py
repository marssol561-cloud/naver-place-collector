"""
SP-6: Industry backfill — normalize stores.industry from crawl_data->>'category'.

Default mode = dry-run (ZERO writes).
Pass --execute only after CEO approval.

Usage:
    python scripts/backfill_industry.py            # dry-run (default)
    python scripts/backfill_industry.py --execute  # actual UPDATE (CEO approval required)
"""
import argparse
import io
import os
import sys
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

import requests
from db.master_db import _auth_headers, normalize_industry

MASTER_DB_URL = os.getenv("MASTER_DB_URL")


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def _fetch_target_rows() -> list[dict]:
    """Fetch stores where crawl_data->>'category' is non-empty (client-side filter)."""
    resp = requests.get(
        f"{MASTER_DB_URL}/rest/v1/stores",
        params={"select": "store_id,store_name,industry,crawl_data"},
        headers=_auth_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return [
        r for r in rows
        if r.get("crawl_data") and r["crawl_data"].get("category")
    ]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _build_plan(rows: list[dict]) -> list[dict]:
    """Classify each row: UPDATE / SKIP-unmapped / SKIP-equal."""
    plan = []
    for row in rows:
        raw = row["crawl_data"]["category"]
        current = row.get("industry") or ""
        new_val, hit = normalize_industry(raw)

        if not hit:
            action = "SKIP-unmapped"
        elif new_val == current:
            action = "SKIP-equal"
        else:
            action = "UPDATE"

        plan.append({
            "store_id":   row["store_id"],
            "store_name": (row.get("store_name") or "")[:20],
            "raw":        raw,
            "current":    current,
            "new":        new_val if hit else raw,
            "action":     action,
        })
    return plan


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_table(plan: list[dict], mode: str) -> None:
    cols = {
        "store_id":    10,
        "store_name":  20,
        "raw":         18,
        "current":     18,
        "new":         18,
        "action":      14,
    }
    header = (
        f"{'STORE_ID':10}  {'STORE_NAME':20}  {'RAW_CATEGORY':18}  "
        f"{'CURRENT_INDUSTRY':18}  {'NEW_INDUSTRY':18}  {'ACTION':14}"
    )
    sep = "-" * len(header)
    print(f"\n[backfill] {mode} mode  --{len(plan)} target rows\n")
    print(sep)
    print(header)
    print(sep)
    for p in plan:
        sid  = p["store_id"][:8]
        name = p["store_name"][:20]
        raw  = p["raw"][:18]
        cur  = p["current"][:18]
        new  = p["new"][:18]
        act  = p["action"]
        print(f"{sid:10}  {name:20}  {raw:18}  {cur:18}  {new:18}  {act:14}")
    print(sep)


def _print_summary(plan: list[dict]) -> None:
    updates      = sum(1 for p in plan if p["action"] == "UPDATE")
    skip_unmapped = sum(1 for p in plan if p["action"] == "SKIP-unmapped")
    skip_equal   = sum(1 for p in plan if p["action"] == "SKIP-equal")
    print(
        f"\nSummary: total={len(plan)}"
        f"  update={updates}"
        f"  skip-unmapped={skip_unmapped}"
        f"  skip-equal={skip_equal}"
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

def _execute(plan: list[dict]) -> None:
    updates = [p for p in plan if p["action"] == "UPDATE"]
    if not updates:
        print("\n[backfill] No planned updates — refusing --execute on empty list.")
        sys.exit(1)

    print(f"\n[backfill] Executing {len(updates)} UPDATE(s)...")
    headers = _auth_headers()
    base = f"{MASTER_DB_URL}/rest/v1/stores"

    for p in updates:
        resp = requests.patch(
            f"{base}?store_id=eq.{p['store_id']}",
            json={"industry": p["new"]},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        print(f"  PATCHED  {p['store_id'][:8]}  {p['raw']!r} → {p['new']!r}")

    # Post-state verification
    print(f"\n[backfill] {len(updates)} UPDATEs complete. Verifying post-state...")
    for p in updates:
        vr = requests.get(
            f"{base}?select=store_id,industry&store_id=eq.{p['store_id']}",
            headers=headers,
            timeout=10,
        )
        vr.raise_for_status()
        rows = vr.json()
        verified = rows[0]["industry"] if rows else "NOT FOUND"
        print(f"  VERIFIED {p['store_id'][:8]}  industry={verified!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill stores.industry via industry_naver_map (dry-run by default)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform actual UPDATEs. CEO approval required before use.",
    )
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    rows = _fetch_target_rows()
    plan = _build_plan(rows)

    _print_table(plan, mode)
    _print_summary(plan)

    if args.execute:
        _execute(plan)
    else:
        print("\n[DRY-RUN] Zero writes performed. Pass --execute to apply (CEO approval required).")


if __name__ == "__main__":
    main()
