#!/usr/bin/env python3
"""Translate pinned PostgreSQL qgen syntax into equivalent MySQL 8 syntax."""

from __future__ import annotations

import re
import sys


text = sys.stdin.read()
text = re.sub(r"\bdate\s+'(\d{4}-\d{2}-\d{2})'", r"'\1'", text, flags=re.IGNORECASE)
text = re.sub(
    r"\binterval\s+'(\d+)\s+days?'",
    r"interval \1 day",
    text,
    flags=re.IGNORECASE,
)
text = re.sub(
    r"\binterval\s+'(\d+)'\s+(day|month|year)\b",
    lambda match: f"interval {match.group(1)} {match.group(2).lower()}",
    text,
    flags=re.IGNORECASE,
)
text = re.sub(
    r"substring\(([^()]+?)\s+from\s+(\d+)\s+for\s+(\d+)\)",
    r"substring(\1, \2, \3)",
    text,
    flags=re.IGNORECASE,
)
sys.stdout.write(text)
