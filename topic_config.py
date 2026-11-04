"""Battery-circularity configuration and isolated output paths."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOPIC = "battery"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data" / "battery"
REPORTS = ROOT / "reports" / "battery"
for rule in CONFIG["prefilter_rules"]:
    if not rule or any(group not in CONFIG["topic_groups"] for group in rule):
        raise ValueError(f"Invalid prefilter rule: {rule!r}")
if any("," in query for query in CONFIG["queries"]):
    raise ValueError("Queries must not contain commas")
DATA.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)


def output_paths(topic="battery"):
    if topic != "battery":
        raise ValueError("This package supports battery circularity only")
    return DATA, REPORTS
