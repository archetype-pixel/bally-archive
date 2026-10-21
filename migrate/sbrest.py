"""Minimal Supabase REST client (PostgREST + Storage) using requests.
Works with both legacy JWT keys and the new sb_secret_... keys."""
import time

import requests


class Query:
    def __init__(self, base, key, table):
        self.base, self.key, self.t = base, key, table
        self.params, self.filters = {}, []
        self._body, self._method, self._prefer = None, "GET", ""

    def select(self, cols="*"):
        self.params["select"] = cols
        return self

    def eq(self, col, val):
        self.filters.append((col, f"eq.{val}"))
        return self

    def in_(self, col, vals):
        q = ",".join('"' + str(v).replace('"', '\\"') + '"' for v in vals)
        self.filters.append((col, f"in.({q})"))
        return self

    def order(self, col):
        self.params["order"] = col
        return self

    def limit(self, n):
        self.params["limit"] = str(n)
        return self

    def upsert(self, rows):
        self._method, self._body = "POST", rows
        self._prefer = "resolution=merge-duplicates,return=minimal"
        return self

    def update(self, row):
        self._method, self._body = "PATCH", row
        self._prefer = "return=minimal"
        return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.t}"
        params = dict(self.params)
        params.update({c: v for c, v in self.filters})
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if self._prefer:
            h["Prefer"] = self._prefer
        for attempt in range(4):
            try:
                r = requests.request(self._method, url, params=params, json=self._body, headers=h, timeout=120)
                if r.status_code >= 500 or r.status_code == 429:
                    raise RuntimeError(f"{r.status_code} {r.text[:200]}")
                if r.status_code >= 400:
                    raise ValueError(f"{r.status_code} {r.text[:300]}")
                res = type("R", (), {})()
                res.data = r.json() if self._method == "GET" and r.text else []
                return res
            except (requests.RequestException, RuntimeError):
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))


class Storage:
    def __init__(self, base, key, bucket):
        self.base, self.key, self.b = base, key, bucket

    def upload(self, path, data, opts=None):
        opts = opts or {}
        url = f"{self.base}/storage/v1/object/{self.b}/{path}"
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
             "Content-Type": opts.get("content-type", "application/octet-stream"), "x-upsert": "true"}
        for attempt in range(4):
            try:
                r = requests.post(url, data=data, headers=h, timeout=600)
                if r.status_code in (200, 201):
                    return
                if r.status_code >= 500 or r.status_code == 429:
                    raise RuntimeError(f"{r.status_code} {r.text[:200]}")
                raise ValueError(f"storage {r.status_code} {r.text[:300]}")
            except (requests.RequestException, RuntimeError):
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))


class Client:
    def __init__(self, base, key):
        self.base, self.key = base.rstrip("/"), key
        client = self

        class _S:
            @staticmethod
            def from_(bucket):
                return Storage(client.base, client.key, bucket)

        self.storage = _S()

    def table(self, t):
        return Query(self.base, self.key, t)
