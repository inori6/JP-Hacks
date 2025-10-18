#!/usr/bin/env python3
"""Optional import probing to identify failing dependencies at runtime."""
from __future__ import annotations

import json
import os
import traceback
from pathlib import Path
from typing import Iterable, List

AUDIT_PATH = Path("deps/deps_audit.json")
DEFAULT_LIMIT = 10


def load_candidate_packages(limit: int = DEFAULT_LIMIT) -> List[str]:
    if not AUDIT_PATH.exists():
        return []
    try:
        data = json.loads(AUDIT_PATH.read_text())
    except Exception:  # pragma: no cover - defensive
        return []
    sorted_entries = sorted(
        data,
        key=lambda e: (-int(e.get("import_count", 0)), e.get("name", "")),
    )
    names = []
    for entry in sorted_entries:
        name = entry.get("name")
        if not name or name in {"python3", "python", "kivy"}:
            continue
        names.append(name)
        if len(names) >= limit:
            break
    return names


def probe_imports(packages: Iterable[str]) -> None:
    for pkg in packages:
        try:
            __import__(pkg)
            print(f"[probe] OK: {pkg}", flush=True)
        except Exception:  # pragma: no cover - diagnostic logging only
            print(f"[probe] FAIL: {pkg}", flush=True)
            traceback.print_exc()


def maybe_schedule_probe(limit: int = DEFAULT_LIMIT) -> None:
    if os.environ.get("PROBE_IMPORTS") != "1":
        return
    from kivy.clock import Clock  # local import to avoid early binding

    pkgs = load_candidate_packages(limit)
    if not pkgs:
        print("[probe] No candidate packages found for probing.", flush=True)
        return

    def _run(*_):
        probe_imports(pkgs)

    Clock.schedule_once(_run, 0)

