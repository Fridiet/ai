from scripts.partition_new_receipts import build


def test_identical_and_near_old_images_never_enter_holdout(tmp_path, monkeypatch):
    folder=tmp_path/'new'
    folder.mkdir()
    old=tmp_path/'old.png'
    old.write_bytes(b'old-image')
    (folder/'copy.png').write_bytes(b'old-image')
    (folder/'near.png').write_bytes(b'new-encoding')
    (folder/'different.png').write_bytes(b'different')
    monkeypatch.setattr('scripts.partition_new_receipts.dhash',
        lambda p: (2**64-1) if p.name=='different.png' else 1 if p.name=='near.png' else 0)
    report=build(folder,[old])
    rows={r['filename']:r for r in report['records']}
    assert rows['copy.png']['split']=='development_overlap'
    assert rows['near.png']['split']=='development_overlap'
    assert rows['different.png']['split']!='development_overlap'
    assert not any(r['training_eligible'] for r in rows.values())
    assert build(folder,[old])==report
