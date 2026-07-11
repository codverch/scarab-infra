#!/usr/bin/env python3
"""Normalize tpch-kit qgen output for PostgreSQL psql execution."""

from __future__ import annotations

import re
import sys


def normalize(text: str) -> str:
    text = re.sub(
        r"interval\s+'([0-9]+)'\s+day\s+\([0-9]+\)",
        r"interval '\1 days'",
        text,
        flags=re.IGNORECASE,
    )
    limits = [int(value) for value in re.findall(r"(?mi)^\s*limit\s+(-?[0-9]+);\s*$", text)]
    text = re.sub(r"(?mi)^\s*limit\s+-?[0-9]+;\s*$", "", text)
    positive = [value for value in limits if value >= 0]
    if positive:
        if len(positive) != 1:
            raise ValueError(f"expected one positive LIMIT, found {positive}")
        final_semicolon = text.rfind(";")
        if final_semicolon < 0:
            raise ValueError("cannot attach LIMIT: query has no semicolon")
        text = (
            text[:final_semicolon].rstrip()
            + f"\nlimit {positive[0]};"
            + text[final_semicolon + 1 :]
        )
    return text.rstrip() + "\n"


if __name__ == "__main__":
    sys.stdout.write(normalize(sys.stdin.read()))
