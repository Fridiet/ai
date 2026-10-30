"""Regression on the user-provided receipt; not an independent accuracy benchmark."""
import json
import time
from pathlib import Path
import requests


def main():
    path = Path("C:/Users/your-user/OneDrive/바탕 화면/캡스톤/영수증사진/스크린샷 2026-07-17 212620.png")
    started = time.perf_counter()
    response = requests.post("http://127.0.0.1:8000/api/ocr/receipt",
        files={"file":(path.name,path.read_bytes(),"image/png")}, timeout=120)
    response.raise_for_status()
    data = response.json()
    names = [item["name"] for item in data["items"]]
    expected = ["요구르트","양파","대파","당근","쌈채소","콩나물","청양고추","등심","달걀","라면","맥주"]
    report = {"seconds":round(time.perf_counter()-started,3),"names":names,"expected":expected,
              "all_expected":names==expected,"response":data,"independent_test":False}
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    (Path(__file__).resolve().parents[1]/"output/original_receipt_fast_http.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    assert report["all_expected"] and not data["reviewCandidates"]


if __name__ == "__main__":
    main()
