"""Manual caption/rights corrections for the interwar book, and promotion of cleared images to the public bucket."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import library_ai as L  # noqa: E402
import library_ai2 as A  # noqa: E402

BALLY = "Historical Archives of Bally Schuhfabriken AG; photo: Manuel Fabritz, © Bally"
FIX = {
    "iw-p063-1": {"caption_en": "Evening sandals, 1930 (American Vogue, 12 April 1930, p. 104)", "fig_label": "iia", "credit": "American Vogue, © Condé Nast", "rights": "restricted"},
    "iw-p063-2": {"caption_en": "Evening sandals, 1930", "fig_label": "iib", "credit": BALLY, "rights": "public", "photographer": "Manuel Fabritz"},
    "iw-p068-1": {"caption_en": "Ladies’ shoes by Bally, 1940 (American Vogue, 1 January 1940, p. 80 / Historical Archives of Bally Schuhfabriken AG)", "fig_label": "va, vb", "credit": "© Condé Nast / Historical Archives of Bally Schuhfabriken AG", "rights": "restricted"},
    "iw-p070-1": {"caption_en": "Sandals by Bally, 1939 (American Vogue, 1 February 1939, p. 111 / Historical Archives of Bally Schuhfabriken AG)", "fig_label": "viia, viib", "credit": "© Condé Nast / Historical Archives of Bally Schuhfabriken AG", "rights": "restricted"},
    "iw-p116-1": {"caption_en": "Fritz Kühni, ideas scrapbook and designs (1930s)", "fig_label": "xa", "credit": "Historical Archives of Bally Schuhfabriken AG; photo: © Ballyana", "rights": "public"},
    "iw-p118-1": {"caption_en": "Fritz Kühni, ideas scrapbook and designs (1930s)", "fig_label": "xb", "credit": "Historical Archives of Bally Schuhfabriken AG; photo: © Ballyana", "rights": "public"},
    "iw-p119-1": {"caption_en": "Fritz Kühni, ideas scrapbook and designs (1930s)", "fig_label": "xc", "credit": "Historical Archives of Bally Schuhfabriken AG; photo: © Ballyana", "rights": "public"},
}

for pid, body in FIX.items():
    row = L.get("library_plates", {"select": "id,rights,path,thumb_path", "id": f"eq.{pid}"})[0]
    if body["rights"] == "public" and row["rights"] != "public":
        A.put(f"{A.DATA}/plates/{pid[3:]}.jpg", row["path"])
        A.put(f"{A.DATA}/thumbs/{pid[3:]}.jpg", row["thumb_path"])
    L.patch("library_plates", {"id": f"eq.{pid}"}, {**body, "caption_source": "manual"})
    L.log("fixed", pid, body["rights"])
