import hashlib
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import bootstrap_data
from scripts.bootstrap_data import BootstrapError, prepare_catalog, validate_database


def create_database(path: Path, product_count: int = 1) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE product_search (name_key TEXT NOT NULL);
        """
    )
    connection.executemany(
        "INSERT INTO products(name) VALUES (?)",
        [(f"상품 {index}",) for index in range(product_count)],
    )
    connection.commit()
    connection.close()


def write_manifest(
    path: Path,
    database_sha256: str = "",
    archive_sha256: str = "",
    github_release: dict | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "catalog": {
                    "version": "test",
                    "source": "K-FIND:test",
                    "source_page": "https://example.test/kfind",
                    "database": {
                        "url": "",
                        "github_release": github_release or {},
                        "archive_member": "products.sqlite3",
                        "archive_sha256": archive_sha256,
                        "sha256": database_sha256,
                    },
                    "xlsx": {"path": "missing.xlsx", "sha256": ""},
                },
            }
        ),
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_existing_database_is_reused(tmp_path: Path):
    database_path = tmp_path / "products.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    create_database(database_path, product_count=2)
    write_manifest(manifest_path)

    method, count = prepare_catalog(manifest_path, database_path)

    assert method == "existing"
    assert count == 2


def test_zip_database_is_downloaded_and_verified(tmp_path: Path):
    source_database = tmp_path / "source.sqlite3"
    archive_path = tmp_path / "products.sqlite3.zip"
    destination = tmp_path / "output" / "products.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    create_database(source_database, product_count=3)
    with ZipFile(archive_path, "w") as archive:
        archive.write(source_database, arcname="products.sqlite3")
    write_manifest(
        manifest_path,
        database_sha256=file_sha256(source_database),
        archive_sha256=file_sha256(archive_path),
    )

    method, count = prepare_catalog(
        manifest_path,
        destination,
        database_url=archive_path.as_uri(),
    )

    assert method == "download"
    assert count == 3
    assert validate_database(destination) == 3


def test_invalid_checksum_does_not_install_database(tmp_path: Path):
    source_database = tmp_path / "source.sqlite3"
    destination = tmp_path / "output.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    create_database(source_database)
    write_manifest(manifest_path, database_sha256="0" * 64)

    with pytest.raises(BootstrapError, match="SHA-256"):
        prepare_catalog(
            manifest_path,
            destination,
            database_url=source_database.as_uri(),
        )

    assert not destination.exists()


def test_private_github_release_uses_authenticated_downloader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_database = tmp_path / "source.sqlite3"
    archive_path = tmp_path / "release-asset.zip"
    destination = tmp_path / "installed" / "products.sqlite3"
    manifest_path = tmp_path / "manifest.json"
    create_database(source_database, product_count=4)
    with ZipFile(archive_path, "w") as archive:
        archive.write(source_database, arcname="products.sqlite3")
    write_manifest(
        manifest_path,
        database_sha256=file_sha256(source_database),
        archive_sha256=file_sha256(archive_path),
        github_release={
            "repository": "owner/private-repo",
            "tag": "kfind-test",
            "asset": "products.sqlite3.zip",
        },
    )

    calls = []

    def fake_download(repository: str, tag: str, asset: str, output: Path):
        calls.append((repository, tag, asset))
        output.write_bytes(archive_path.read_bytes())

    monkeypatch.setattr(bootstrap_data, "_download_github_release", fake_download)

    method, count = prepare_catalog(manifest_path, destination)

    assert method == "github_release"
    assert count == 4
    assert calls == [("owner/private-repo", "kfind-test", "products.sqlite3.zip")]
    assert validate_database(destination) == 4
