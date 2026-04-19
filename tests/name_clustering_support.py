from pathlib import Path

import yaml

from pdf_analyzer.name_clustering_models import NameStringRecord

TESTS_DIR = Path(__file__).resolve().parent
SAMPLE_PATH = TESTS_DIR / "name_clustering_sample.yaml"


def load_name_records(path: Path = SAMPLE_PATH) -> list[NameStringRecord]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = payload.get("records", [])
    return [
        NameStringRecord(
            id=int(row["id"]),
            name_string=str(row["name"]),
            mentions=int(row.get("mentions", 0) or 0),
            time_period=str(row["time_period"]) if row.get("time_period") else None,
            source_filenames=tuple(str(item) for item in row.get("source_filenames", [])),
            context_notes=tuple(str(item) for item in row.get("context_notes", [])),
        )
        for row in rows
    ]
