"""Local HTTP regression using a generated Korean receipt fixture (not the user's original image)."""
import io
import json
import time
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont


def main():
    image = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 32)
    for y, name, price in [(50, "친환경복주머니양파", "3,980"), (140, "송송어린대파", "2,980")]:
        draw.text((30,y), name, font=font, fill="black")
        draw.text((650,y), price, font=font, fill="black")
        draw.text((830,y), "1", font=font, fill="black")
        draw.text((960,y), price, font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    results = []
    for route in ("/api/ocr/receipt", "/ocr/inventory-candidates"):
        started = time.perf_counter()
        response = requests.post("http://127.0.0.1:8000" + route,
            files={"file": ("generated-regression.png", buffer.getvalue(), "image/png")}, timeout=180)
        response.raise_for_status()
        data = response.json()
        if "items" in data:
            names = [item["name"] for item in data["items"]]
            assert not data["reviewCandidates"], data
        else:
            names = [item["ingredient_master_candidate"]["canonical_name"] for item in data["inventory_candidates"]]
            assert all(item["ingredient_master_candidate"]["source"] == "receipt_fresh_suffix_rule" for item in data["inventory_candidates"])
        result = dict(route=route, seconds=round(time.perf_counter()-started,3), names=names, response=data)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert names == ["양파", "대파"], result
    target = Path(__file__).resolve().parents[1] / "output/receipt_name_fix_http.json"
    target.write_text(json.dumps({"fixture":"generated Korean text, not original user receipt", "results":results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
