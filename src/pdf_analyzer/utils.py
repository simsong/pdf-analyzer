import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_question(value: str) -> str:
    return normalize_whitespace(value).casefold()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "project"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_hashes(path: Path) -> tuple[str, str, int]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()  # noqa: S324 - useful compatibility checksum
    size_bytes = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size_bytes += len(chunk)
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest(), size_bytes


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_sort_year(values: list[str], fallback_text: str = "") -> int | None:
    text = " ".join(values)
    if fallback_text:
        text = f"{text} {fallback_text}"
    matches = re.findall(r"\b(?:18|19|20)\d{2}\b", text)
    if not matches:
        return None
    return min(int(match) for match in matches)


def unique_copy_name(filename: str, used_names: set[str]) -> str:
    candidate = filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    index = 1
    while candidate in used_names:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used_names.add(candidate)
    return candidate


def apply_current_umask_file_mode(path: Path) -> None:
    current_umask = os.umask(0)
    os.umask(current_umask)
    path.chmod(0o666 & ~current_umask)


def clone_or_copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        subprocess.run(
            ["cp", "-c", str(source), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        apply_current_umask_file_mode(target)
        return
    except (subprocess.CalledProcessError, OSError):
        shutil.copyfile(source, target)
    apply_current_umask_file_mode(target)


def coerce_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(payload, list):
        return [str(item) for item in payload if item]
    return []
