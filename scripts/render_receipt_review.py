"""Reprocess cached OCR and build a private, offline annotation page."""
import argparse
import hashlib
import json
from pathlib import Path
from app import product_pipeline
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items

PAGE = r'''<!doctype html><html lang="ko"><meta charset="utf-8">
<title>로컬 영수증 정답 검수</title><style>
body{font:16px system-ui;margin:20px;background:#f6f6f1}header{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
main{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}img{width:100%;height:80vh;object-fit:contain;object-position:top;background:white}
textarea{width:95%;height:260px;font:14px monospace}pre{white-space:pre-wrap;max-height:180px;overflow:auto;background:white;padding:12px}button,select{padding:10px}label{display:block;margin:10px 0}
</style><header><button id="prev">이전</button><span id="position"></span><button id="next">다음</button><button id="download">검수 결과 JSON 저장</button><label>이전 검수 불러오기 <input id="restore" type="file" accept=".json"></label></header>
<p>자동 예측은 정답이 아닙니다. 식품 누락·비식품·상품명·수량을 이미지와 대조하세요. 변경 내용은 JSON 저장 버튼으로 보관하세요. 서버 전송은 없습니다.</p>
<main><section><img id="receipt" alt="원본 영수증"></section><section>
<h2 id="filename"></h2><label>매장 유형 <select id="store"><option value="">미확인</option><option value="grocery">마트/식료품점</option><option value="other">기타</option></select></label>
<h3>자동 예측 (참고용)</h3><pre id="predicted"></pre>
<h3>정답 상품 목록 JSON</h3><p>예: [{"product_name":"상품 원문", "canonical_name":"당근", "is_food":true, "quantity":2, "unit":"개"}]<br>모르는 수량·단위는 null로 입력하세요. 식품이 아닌 행도 is_food:false로 기록하세요.</p>
<textarea id="answer" spellcheck="false"></textarea><label><input type="checkbox" id="checked">이미지의 모든 상품 행과 대조 완료</label>
<p id="notice"></p></section></main><script type="application/json" id="payload">__DATA__</script>
<script>
const payload=JSON.parse(document.getElementById('payload').textContent), rows=payload.records;
const el=id=>document.getElementById(id);let index=0;
function save(){const r=rows[index];try{const a=JSON.parse(el('answer').value);if(!Array.isArray(a))throw Error('정답은 JSON 배열이어야 합니다.');
for(const item of a){if(!item||typeof item!=='object'||typeof item.product_name!=='string'||!item.product_name.trim()||typeof item.is_food!=='boolean')throw Error('각 행의 product_name과 is_food를 확인하세요.');
if(item.quantity!=null&&(typeof item.quantity!=='number'||!Number.isFinite(item.quantity)||item.quantity<=0))throw Error('수량은 양수 또는 null이어야 합니다.');
if(item.is_food&&(typeof item.canonical_name!=='string'||!item.canonical_name.trim()))throw Error('식품의 표준 재료명을 입력하세요.');}
if(el('checked').checked&&!el('store').value)throw Error('매장 유형을 선택하세요.');
r.expected_items=a;r.reviewed=el('checked').checked;r.store_type=el('store').value||null;el('notice').textContent='';return true;
}catch(e){el('notice').textContent=e.message;return false;}}
function show(){const r=rows[index];el('position').textContent=`${index+1} / ${rows.length}`;el('filename').textContent=r.filename;
el('receipt').src=r.image_uri;el('predicted').textContent=JSON.stringify(r.predicted_items,null,2);el('answer').value=JSON.stringify(r.expected_items||[],null,2);
el('checked').checked=!!r.reviewed;el('store').value=r.store_type||'';}
el('prev').onclick=()=>{if(save()){index=Math.max(0,index-1);show();}};
el('next').onclick=()=>{if(save()){index=Math.min(rows.length-1,index+1);show();}};
el('download').onclick=()=>{if(!save())return;const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
const a=document.createElement('a');a.href=url;a.download='receipt_annotations.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
el('restore').onchange=async event=>{try{const data=JSON.parse(await event.target.files[0].text());if(!Array.isArray(data.records))throw Error('records가 없습니다.');
const byHash=new Map(data.records.map(r=>[r.image_sha256,r]));let restored=0;
for(const r of rows){const old=byHash.get(r.image_sha256);if(old&&Array.isArray(old.expected_items)){r.expected_items=old.expected_items;r.reviewed=old.reviewed===true;r.store_type=['grocery','other'].includes(old.store_type)?old.store_type:null;restored++;}}
show();el('notice').textContent=`${restored}장 검수 내용을 불러왔습니다.`;}catch(e){el('notice').textContent=e.message;}};
if(rows.length)show();else el('notice').textContent='완료된 이미지가 없습니다.';
</script></html>'''


def render(source: Path, destination: Path):
    payload = json.loads(source.read_text(encoding="utf-8"))
    records = []
    for row in payload["records"]:
        if row["status"] != "ok":
            continue
        raw = json.loads(Path(row["raw_cache"]).read_text(encoding="utf-8"))
        analysis = product_pipeline.analyze_ocr_items(raw["items"])
        row["predicted_items"] = build_receipt_items(build_inventory_candidates(analysis))
        row["product_candidates"] = analysis["products"]
        row["image_uri"] = Path(row["image_path"]).as_uri()
        row["evaluation_use"] = "development_review_not_independent_test"
        records.append(row)
    payload["records"] = records
    payload["pipeline_sha256"] = hashlib.sha256(Path(product_pipeline.__file__).read_bytes()).hexdigest()
    payload["training_performed"] = False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(PAGE.replace("__DATA__", json.dumps(payload, ensure_ascii=False, default=str).replace("<", "\\u003c")), encoding="utf-8")
    destination.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"검수 화면: {destination.resolve()} ({len(records)}장)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("output/local_receipt_review/review.json"))
    parser.add_argument("--output", type=Path, default=Path("output/local_receipt_review/annotation.html"))
    args = parser.parse_args()
    if args.output.with_suffix(".json").resolve() == args.source.resolve():
        parser.error("원본 분석을 보존하려면 다른 출력 이름(예: annotation.html)을 지정하세요.")
    render(args.source, args.output)
