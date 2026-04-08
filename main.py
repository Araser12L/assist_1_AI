#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assistAI — a local, personal "emotional support + structure" assistant.

What it is:
- A single-file Python app you can run locally (no API keys, no placeholders).
- Stores your entries in a local SQLite database in the same folder.
- Offers mood check-ins, guided exercises, reflection prompts, and exports.

What it isn't:
- Medical advice. If you're in danger, contact local emergency services.

Run:
  python assistAI.py
Optional:
  python assistAI.py --help

Data:
  Creates ./assistAI_data.sqlite3
  Creates ./assistAI_exports/ on export
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import datetime as _dt
import functools
import getpass
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import string
import sys
import textwrap
import time
import traceback
import typing as t


# -----------------------------
# Small utilities (formatting)
# -----------------------------


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _today_key(d: _dt.datetime | None = None) -> str:
    if d is None:
        d = _now()
    return d.date().isoformat()


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _wrap(s: str, width: int = 86) -> str:
    return "\n".join(textwrap.fill(line, width=width) if line.strip() else "" for line in s.splitlines())


def _hr(ch: str = "─", n: int = 86) -> str:
    return ch * n


def _title(s: str) -> str:
    return f"{s}\n{_hr('=')}"


def _soft_prompt(prompt: str) -> str:
    return input(f"{prompt} ").strip()


def _int_prompt(prompt: str, lo: int, hi: int, default: int | None = None) -> int:
    while True:
        raw = _soft_prompt(f"{prompt} [{lo}-{hi}]" + (f" (default {default})" if default is not None else "") + ":")
        if raw == "" and default is not None:
            return default
        try:
            v = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if v < lo or v > hi:
            print(f"Please keep it within {lo}..{hi}.")
            continue
        return v


def _yn(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        raw = _soft_prompt(f"{prompt} ({d})").lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please answer y or n.")


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "untitled"


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _rand_token(nbytes: int = 18) -> str:
    return _b64url(secrets.token_bytes(nbytes))


def _print_box(title: str, body: str) -> None:
    print(_hr())
    print(title)
    print(_hr())
    if body.strip():
        print(_wrap(body))
    print(_hr())


def _pause() -> None:
    _soft_prompt("Press Enter when you're ready to continue…")


# -----------------------------
# App identity / “personality”
# -----------------------------


@dataclasses.dataclass(frozen=True)
class Persona:
    name: str
    voice: str
    boundary_line: str
    gentle_rules: tuple[str, ...]


def _build_persona() -> Persona:
    # Make the assistant feel personal but consistent.
    # No user config required; we pick a stable persona based on machine+user.
    seed_material = f"{os.environ.get('COMPUTERNAME','?')}|{getpass.getuser()}|assistAI|{sys.version_info[:3]}"
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    names = [
        "assistAI",
        "aiden",
        "aleena",
