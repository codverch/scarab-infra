#!/usr/bin/env python3
"""Add --power_intf_on 1 to scarab-infra simulation JSON configurations."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWER_FLAG = "--power_intf_on 1"
SKIP_PARTS = {"results"}


def ensure_power(params: str) -> str:
    if re.search(r"--power_intf_on\s+[01]", params):
        return re.sub(r"--power_intf_on\s+0\b", "--power_intf_on 1", params)
    return params.rstrip() + " " + POWER_FLAG


def detect_indent(text: str) -> int:
    return 4 if re.search(r"^\{\n    ", text) else 2


def process_configurations(data: dict) -> bool:
    configs = data.get("configurations")
    if not isinstance(configs, dict):
        return False
    changed = False
    for key, value in configs.items():
        if isinstance(value, dict) and isinstance(value.get("params"), str):
            new = ensure_power(value["params"])
            if new != value["params"]:
                value["params"] = new
                changed = True
        elif isinstance(value, str) and value.startswith("--"):
            new = ensure_power(value)
            if new != value:
                configs[key] = new
                changed = True
    return changed


def main() -> int:
    updated: list[Path] = []
    for path in sorted(ROOT.rglob("*.json")):
        if any(part in path.parts for part in SKIP_PARTS):
            continue
        text = path.read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not process_configurations(data):
            continue
        indent = detect_indent(text)
        path.write_text(json.dumps(data, indent=indent) + "\n")
        updated.append(path)

    for path in updated:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
