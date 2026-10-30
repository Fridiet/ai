from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from .import_kfind_products import build_index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data_manifest.json"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "app" / "data" / "products.sqlite3"
REQUIRED_TABLES = {"products", "product_search"}


class BootstrapError(RuntimeError):
    """Raised when the local catalog cannot be prepared safely."""


@dataclass(frozen=True)
class CatalogManifest:
    version: str
    source: str
    source_page: str
    database_url: str
    database_sha256: str
    archive_sha256: str
    archive_member: str
    github_repository: str
    github_tag: str
    github_asset: str
    xlsx_path: Path
    xlsx_sha256: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_manifest_path(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def load_manifest(manifest_path: Path) -> CatalogManifest:
    try:
        with manifest_path.open(encoding="utf-8") as file:
            payload = json.load(file)
        catalog = payload["catalog"]
        database = catalog["database"]
        github_release = database.get("github_release") or {}
        xlsx = catalog["xlsx"]
        if not isinstance(github_release, dict):
            raise TypeError("github_release must be an object")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise BootstrapError(f"데이터 매니페스트를 읽을 수 없습니다: {manifest_path}") from error

    return CatalogManifest(
        version=str(catalog["version"]),
        source=str(catalog["source"]),
        source_page=str(catalog["source_page"]),
        database_url=str(database.get("url", "")).strip(),
        database_sha256=str(database.get("sha256", "")).strip().lower(),
        archive_sha256=str(database.get("archive_sha256", "")).strip().lower(),
        archive_member=str(database.get("archive_member", "products.sqlite3")),
        github_repository=str(github_release.get("repository", "")).strip(),
        github_tag=str(github_release.get("tag", "")).strip(),
        github_asset=str(github_release.get("asset", "")).strip(),
        xlsx_path=_resolve_manifest_path(manifest_path, str(xlsx["path"])),
        xlsx_sha256=str(xlsx.get("sha256", "")).strip().lower(),
    )


def validate_database(path: Path) -> int:
    if not path.is_file():
        raise BootstrapError(f"SQLite DB가 없습니다: {path}")

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        missing_tables = REQUIRED_TABLES - tables
        if missing_tables:
            raise BootstrapError(
                f"SQLite DB에 필수 테이블이 없습니다: {', '.join(sorted(missing_tables))}"
            )
        count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    except sqlite3.Error as error:
        raise BootstrapError(f"유효하지 않은 SQLite DB입니다: {path}") from error
    finally:
        if "connection" in locals():
            connection.close()

    if not integrity or integrity[0] != "ok":
        raise BootstrapError(f"SQLite 무결성 검사에 실패했습니다: {path}")
    if count <= 0:
        raise BootstrapError(f"SQLite DB에 상품 데이터가 없습니다: {path}")
    return count


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "fridiet-ai-data-bootstrap/1.0"})
    try:
        with urlopen(request, timeout=60) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0"))
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = downloaded * 100 // total
                    print(
                        f"\r데이터 다운로드: {downloaded / 1024 / 1024:.1f}MB / "
                        f"{total / 1024 / 1024:.1f}MB ({percent}%)",
                        end="",
                        flush=True,
                    )
        print()
    except OSError as error:
        raise BootstrapError(f"데이터를 다운로드할 수 없습니다: {url}") from error


def _verify_checksum(path: Path, expected: str, label: str) -> None:
    if not expected:
        return
    actual = sha256(path)
    if actual.lower() != expected.lower():
        raise BootstrapError(
            f"{label} SHA-256이 일치하지 않습니다. expected={expected}, actual={actual}"
        )


def _archive_suffix(url: str) -> str:
    path = unquote(urlparse(url).path).lower()
    if path.endswith(".zip"):
        return ".zip"
    if path.endswith(".gz"):
        return ".gz"
    return ".sqlite3"


def _extract_database(
    archive_path: Path,
    archive_type: str,
    archive_member: str,
    destination: Path,
) -> None:
    try:
        if archive_type == ".zip":
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                member = archive_member if archive_member in names else ""
                if not member:
                    sqlite_members = [name for name in names if name.lower().endswith(".sqlite3")]
                    if len(sqlite_members) != 1:
                        raise BootstrapError(
                            "ZIP 안에서 products.sqlite3 파일을 하나로 결정할 수 없습니다."
                        )
                    member = sqlite_members[0]
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        elif archive_type == ".gz":
            with gzip.open(archive_path, "rb") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
        else:
            shutil.copyfile(archive_path, destination)
    except (OSError, BadZipFile) as error:
        raise BootstrapError("다운로드한 SQLite 압축 파일을 풀 수 없습니다.") from error


def _prepare_from_download(
    url: str,
    manifest: CatalogManifest,
    database_path: Path,
    archive_sha256: str,
    database_sha256: str,
) -> int:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    archive_type = _archive_suffix(url)
    with tempfile.TemporaryDirectory(dir=database_path.parent) as temporary_directory:
        temporary_path = Path(temporary_directory)
        archive_path = temporary_path / f"catalog{archive_type}"
        extracted_path = temporary_path / "products.sqlite3"
        _download(url, archive_path)
        _verify_checksum(archive_path, archive_sha256, "압축 파일")
        _extract_database(
            archive_path,
            archive_type,
            manifest.archive_member,
            extracted_path,
        )
        _verify_checksum(extracted_path, database_sha256, "SQLite DB")
        count = validate_database(extracted_path)
        extracted_path.replace(database_path)
    return count


def _download_github_release(
    repository: str,
    tag: str,
    asset: str,
    destination: Path,
) -> None:
    gh_path = shutil.which("gh")
    if not gh_path:
        raise BootstrapError(
            "GitHub CLI(gh)가 필요합니다. 설치 후 `gh auth login`을 실행하세요."
        )

    result = subprocess.run(
        [
            gh_path,
            "release",
            "download",
            tag,
            "--repo",
            repository,
            "--pattern",
            asset,
            "--output",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            detail = f"\nGitHub CLI: {detail}"
        raise BootstrapError(
            "비공개 GitHub Release를 다운로드하지 못했습니다. "
            "`gh auth login`과 저장소 접근 권한을 확인하세요."
            f"{detail}"
        )
    if not destination.is_file():
        raise BootstrapError(
            f"GitHub Release에 요청한 파일이 없습니다: {repository} {tag} {asset}"
        )


def _prepare_from_github_release(
    repository: str,
    tag: str,
    asset: str,
    manifest: CatalogManifest,
    database_path: Path,
    archive_sha256: str,
    database_sha256: str,
) -> int:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    archive_type = _archive_suffix(asset)
    with tempfile.TemporaryDirectory(dir=database_path.parent) as temporary_directory:
        temporary_path = Path(temporary_directory)
        archive_path = temporary_path / f"catalog{archive_type}"
        extracted_path = temporary_path / "products.sqlite3"
        print(f"비공개 GitHub Release에서 K-FIND DB를 받습니다: {repository}@{tag}")
        _download_github_release(repository, tag, asset, archive_path)
        _verify_checksum(archive_path, archive_sha256, "압축 파일")
        _extract_database(
            archive_path,
            archive_type,
            manifest.archive_member,
            extracted_path,
        )
        _verify_checksum(extracted_path, database_sha256, "SQLite DB")
        count = validate_database(extracted_path)
        extracted_path.replace(database_path)
    return count


def _prepare_from_xlsx(
    xlsx_path: Path,
    manifest: CatalogManifest,
    database_path: Path,
) -> int:
    if not xlsx_path.is_file():
        raise BootstrapError(f"K-FIND XLSX가 없습니다: {xlsx_path}")
    _verify_checksum(xlsx_path, manifest.xlsx_sha256, "K-FIND XLSX")
    count = build_index(xlsx_path, database_path, manifest.source)
    validate_database(database_path)
    return count


def prepare_catalog(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    database_path: Path = DEFAULT_DATABASE_PATH,
    database_url: str = "",
    github_repository: str = "",
    github_tag: str = "",
    github_asset: str = "",
    archive_sha256: str = "",
    database_sha256: str = "",
    xlsx_path: Path | None = None,
    force: bool = False,
) -> tuple[str, int]:
    manifest = load_manifest(manifest_path)
    database_path = database_path.resolve()

    if database_path.exists() and not force:
        return "existing", validate_database(database_path)

    resolved_url = database_url.strip() or manifest.database_url
    if resolved_url:
        count = _prepare_from_download(
            resolved_url,
            manifest,
            database_path,
            archive_sha256.strip().lower() or manifest.archive_sha256,
            database_sha256.strip().lower() or manifest.database_sha256,
        )
        return "download", count

    resolved_repository = github_repository.strip() or manifest.github_repository
    resolved_tag = github_tag.strip() or manifest.github_tag
    resolved_asset = github_asset.strip() or manifest.github_asset
    github_values = (resolved_repository, resolved_tag, resolved_asset)
    if any(github_values) and not all(github_values):
        raise BootstrapError(
            "GitHub Release 설정에는 repository, tag, asset이 모두 필요합니다."
        )
    if all(github_values):
        count = _prepare_from_github_release(
            resolved_repository,
            resolved_tag,
            resolved_asset,
            manifest,
            database_path,
            archive_sha256.strip().lower() or manifest.archive_sha256,
            database_sha256.strip().lower() or manifest.database_sha256,
        )
        return "github_release", count

    resolved_xlsx_path = xlsx_path.resolve() if xlsx_path else manifest.xlsx_path
    if resolved_xlsx_path.is_file():
        return "xlsx", _prepare_from_xlsx(resolved_xlsx_path, manifest, database_path)

    raise BootstrapError(
        "K-FIND 데이터를 준비할 수 없습니다.\n"
        f"1) 공식 페이지에서 가공식품 DB를 내려받아 {manifest.xlsx_path} 에 두거나\n"
        "2) data_manifest.json에 비공개 GitHub Release 정보를 설정하세요.\n"
        f"공식 다운로드: {manifest.source_page}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download or build the local K-FIND product catalog."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--github-repository", default="")
    parser.add_argument("--github-tag", default="")
    parser.add_argument("--github-asset", default="")
    parser.add_argument("--archive-sha256", default="")
    parser.add_argument("--database-sha256", default="")
    parser.add_argument("--xlsx", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        method, count = prepare_catalog(
            manifest_path=args.manifest,
            database_path=args.output,
            database_url=args.database_url,
            github_repository=args.github_repository,
            github_tag=args.github_tag,
            github_asset=args.github_asset,
            archive_sha256=args.archive_sha256,
            database_sha256=args.database_sha256,
            xlsx_path=args.xlsx,
            force=args.force,
        )
    except BootstrapError as error:
        print(f"[data setup error] {error}", file=sys.stderr)
        return 1

    messages = {
        "existing": "기존 K-FIND SQLite를 사용합니다.",
        "download": "K-FIND SQLite 다운로드를 완료했습니다.",
        "github_release": "비공개 GitHub Release에서 K-FIND SQLite를 받았습니다.",
        "xlsx": "K-FIND XLSX를 SQLite로 변환했습니다.",
    }
    print(f"{messages[method]} ({count:,}개 상품)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
