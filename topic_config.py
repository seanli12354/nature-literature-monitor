"""Shared topic configuration; hydro retains its historical output paths."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def output_paths(topic):
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", topic):
        raise ValueError(f"Invalid topic: {topic!r}")
    suffix = () if topic == "hydro" else (topic,)
    return ROOT.joinpath("data", *suffix), ROOT.joinpath("reports", *suffix)


def load_config(topic):
    path = ROOT / "topics" / topic / "config.json"
    # Legacy configuration is only a fallback for the original hydro topic.
    if topic == "hydro" and not path.exists():
        path = ROOT / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("prefilter_rules", [["direct_hydropower"],
        ["reservoir_energy", "energy_context"],
        ["climate_power", "energy_context", "water_context"]])
    for rule in config["prefilter_rules"]:
        if not rule or any(g not in config["topic_groups"] for g in rule):
            raise ValueError(f"Invalid prefilter rule: {rule!r}")
    if any("," in query for query in config["queries"]):
        raise ValueError("Queries must not contain commas")
    return config


TOPIC = os.environ.get("TOPIC", "hydro")
DATA, REPORTS = output_paths(TOPIC)
CONFIG = load_config(TOPIC)
DATA.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)
