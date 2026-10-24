"""Inventory the parts of the Azure share the first copy skipped, so images that never reached the old front end
can be found. Lists the share root and DAM top level (printed), then walks every directory under the share that is
not already in azure_files (xCOVERS, APS, xPROJECTS, Products, Trash, export/import temp, and any non-DAM roots)
and upserts each file into azure_files. Read-only on Azure.
Env: AZ_ACCOUNT, AZ_SHARE, AZ_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, RUN_SECONDS (default 20000), WORKERS (16)."""
import os, sys, time
from concurrent.futures import ThreadPoolExecutor
from azure.storage.fileshare import ShareServiceClient
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
RUN = int(os.environ.get("RUN_SECONDS", "20000"))
WORKERS = int(os.environ.get("WORKERS", "16"))
T0 = time.time()
share = ShareServiceClient(account_url=f"https://{os.environ['AZ_ACCOUNT']}.file.core.windows.net",
                           credential={"account_name": os.environ["AZ_ACCOUNT"], "account_key": os.environ["AZ_KEY"]}
                           ).get_share_client(os.environ["AZ_SHARE"])
# already fully inventoried by copy_files.py
DONE = ("DAM/PRJ", "DAM/xHOTFOLDERS", "DAM/Shots", "DAM/Shot_Export", "DAM/MOVIES", "DAM/AST")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ls(d):
    for t in range(4):
        try:
            return list(share.get_directory_client(d).list_directories_and_files())
        except Exception as e:
            if t == 3:
                log("list failed", d, type(e).__name__)
                return []
            time.sleep(2 + t * 3)


def upsert(rows):
    for i in range(0, len(rows), 500):
        for t in range(4):
            r = requests.post(f"{SB}/rest/v1/azure_files", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"},
                              json=rows[i:i + 500], timeout=120)
            if r.ok:
                break
            time.sleep(3)


def main():
    root = ls("")
    log("SHARE ROOT:", [(x["name"], "dir" if x["is_directory"] else x.get("size")) for x in root])
    dam = ls("DAM")
    log("DAM TOP:", [(x["name"], "dir" if x["is_directory"] else x.get("size")) for x in dam])
    stack = [x["name"] for x in root if x["is_directory"] and x["name"] != "DAM"]
    stack += [f"DAM/{x['name']}" for x in dam if x["is_directory"] and f"DAM/{x['name']}" not in DONE]
    if os.environ.get("ONLY"):
        stack = [d for d in os.environ["ONLY"].split(",") if d]
    loose = [{"path": f"DAM/{x['name']}", "dir": "DAM", "name": x["name"], "size": x.get("size")} for x in dam if not x["is_directory"]]
    loose += [{"path": x["name"], "dir": "", "name": x["name"], "size": x.get("size")} for x in root if not x["is_directory"]]
    upsert(loose)
    log("walking", stack)
    from collections import Counter
    per_top, nfiles = Counter(), 0
    with ThreadPoolExecutor(WORKERS) as ex:
        while stack and time.time() - T0 < RUN:
            batch, stack = stack[:WORKERS * 4], stack[WORKERS * 4:]
            rows = []
            for d, items in zip(batch, ex.map(ls, batch)):
                for x in items:
                    p = f"{d}/{x['name']}"
                    if x["is_directory"]:
                        stack.append(p)
                    else:
                        rows.append({"path": p, "dir": d, "name": x["name"], "size": x.get("size")})
                        per_top["/".join(p.split("/")[:2])] += 1
            upsert(rows)
            nfiles += len(rows)
            if nfiles and nfiles % 5000 < len(rows):
                log(nfiles, "files;", len(stack), "dirs queued")
    log("DONE files", nfiles, "dirs left", len(stack))
    for k, v in per_top.most_common():
        log("  ", k, v)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
