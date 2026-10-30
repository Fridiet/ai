from scripts.prepare_receipt_images import image_manifest


def test_images_are_unlabeled_test_data_and_duplicates_share_hash(tmp_path):
    (tmp_path / "first.png").write_bytes(b"same-image")
    (tmp_path / "second.PNG").write_bytes(b"same-image")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    rows = image_manifest(tmp_path)
    assert len(rows) == 2
    assert rows[0]["image_sha256"] == rows[1]["image_sha256"]
    assert all(r["source_split"] == "test" and not r["training_eligible"] for r in rows)
    assert all(r["expected_items"] is None and not r["reviewed"] for r in rows)


def test_review_page_escapes_untrusted_text(tmp_path):
    import json
    from scripts.render_receipt_review import render
    raw = tmp_path / "raw.json"
    raw.write_text('{"items": []}', encoding="utf-8")
    source = tmp_path / "review.json"
    source.write_text(json.dumps({"records": [{"status": "ok", "raw_cache": str(raw),
        "image_path": str(tmp_path / "receipt.png"), "filename": "</script><script>alert(1)</script>",
        "training_eligible": False, "reviewed": False, "expected_items": None}]}), encoding="utf-8")
    destination = tmp_path / "annotation.html"
    render(source, destination)
    assert "</script><script>alert(1)</script>" not in destination.read_text(encoding="utf-8")
    result = json.loads(destination.with_suffix(".json").read_text(encoding="utf-8"))
    assert result["training_performed"] is False
    assert not result["records"][0]["training_eligible"]
