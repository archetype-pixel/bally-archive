"""Upsert a JSON array of rows into a table. Usage: load_rows.py <table> <file.json> [conflict_column]."""
import json, os, sys, requests
SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SERVICE_KEY"]
table, path = sys.argv[1], sys.argv[2]
rows = json.load(open(path))
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
params = {"on_conflict": sys.argv[3]} if len(sys.argv) > 3 else {}
for i in range(0, len(rows), 500):
    r = requests.post(f"{SB}/rest/v1/{table}", headers=H, params=params, json=rows[i:i + 500], timeout=120)
    print(table, i, r.status_code, r.text[:200]); r.raise_for_status()
print("loaded", len(rows))
