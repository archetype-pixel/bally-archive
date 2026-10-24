"""English reader summaries for a Library book: one per chapter (library_parts.summary_en) and one per section
(library_passages.summary_en on the section's heading row). Works from the German original. Env: PUB, BOOK_LABEL, MODEL, WORKERS."""
import os, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import library_ai as L  # noqa: E402

SUM_TOOL = {"name": "record_summary", "description": "Record the English summary.", "input_schema": {"type": "object", "properties": {
    "summary_en": {"type": "string", "description": "Plain-English summary."}}, "required": ["summary_en"]}}


def summarize(text, what, words):
    data, _ = L.call(model=L.MODEL, max_tokens=3000,
                     system=("You write short, accurate English summaries of chapters of a German-language Swiss business history for readers "
                             "of the Bally Heritage Archive. Summarize only what the text says; name the key actors, years and arguments; no "
                             "evaluation, no hype, no em dashes. " + L.GLOSSARY),
                     tools=[SUM_TOOL],
                     messages=[{"role": "user", "content": f"Book: {L.BOOK}. Summarize {what} in {words} words.\n\nGerman text:\n{text}"}])
    return data["summary_en"].replace("—", ", ").replace("–", "-") if data.get("summary_en") else None


def main():
    rows = L.get("library_passages", {"select": "id,seq,part,kind,text_de,sub_de,summary_en", "pub": f"eq.{L.PUB}", "order": "seq"})
    parts = L.get("library_parts", {"select": "no,title_de,summary_en", "pub": f"eq.{L.PUB}", "order": "no"})
    jobs = []
    for p in parts:
        if p["no"] > 7 or p.get("summary_en"):
            continue
        text = "\n\n".join(r["text_de"] for r in rows if r["part"] == p["no"] and r["text_de"])
        jobs.append(("part", p["no"], text[:180000], f"chapter {p['no']} '{p['title_de']}'", "180 to 260"))
    # sections: a section starts at a subheading whose text equals its sub_de
    cur = None
    secs = []
    for r in rows:
        if r["kind"] == "subheading" and r["sub_de"] and r["text_de"] == r["sub_de"]:
            cur = {"id": r["id"], "title": r["text_de"], "texts": [], "done": bool(r.get("summary_en")), "part": r["part"]}
            secs.append(cur)
        elif r["kind"] == "heading":
            cur = None
        elif cur is not None and r["text_de"]:
            cur["texts"].append(r["text_de"])
    for s in secs:
        if not s["done"] and s["texts"] and s["part"] and s["part"] <= 7:
            jobs.append(("section", s["id"], "\n\n".join(s["texts"])[:120000], f"the section '{s['title']}'", "70 to 130"))
    L.log(len(jobs), "summaries to write")

    def one(j):
        kind, key, text, what, words = j
        try:
            s = summarize(text, what, words)
            if kind == "part":
                L.patch("library_parts", {"pub": f"eq.{L.PUB}", "no": f"eq.{key}"}, {"summary_en": s})
            else:
                L.patch("library_passages", {"id": f"eq.{key}"}, {"summary_en": s})
            return 1
        except Exception as e:
            L.log("summary error", kind, key, type(e).__name__, str(e)[:200])
            return 0

    with ThreadPoolExecutor(L.WORKERS) as ex:
        L.log("summaries done", sum(ex.map(one, jobs)))


if __name__ == "__main__":
    main()
