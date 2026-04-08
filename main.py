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
