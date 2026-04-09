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
        "nova",
        "ember",
        "sage",
        "mika",
        "solace",
    ]
    voices = [
        "warm and direct",
        "soft and steady",
        "honest and practical",
        "gentle but structured",
        "calm coach energy",
        "kind, boundary-forward",
    ]
    boundary = [
        "I can’t replace professional care, but I can help you get through the next 10 minutes with structure.",
        "I’m not a therapist — I’m a steady tool. We’ll keep it simple and doable.",
        "I’m here for support and clarity, not perfection. Small steps count.",
        "I can’t diagnose or treat, but I can help you slow down and choose your next move.",
    ]
    rules = [
        "Breathe before you decide.",
        "One small step beats ten perfect plans.",
        "Your feelings are data, not commands.",
        "Boundaries are care in concrete form.",
        "If it’s too hard, make it smaller.",
        "You don’t have to earn rest.",
        "Name the need; then pick the next action.",
        "We can be kind and still be honest.",
    ]
    rng.shuffle(rules)
    return Persona(
        name=rng.choice(names),
        voice=rng.choice(voices),
        boundary_line=rng.choice(boundary),
        gentle_rules=tuple(rules[:5]),
    )


PERSONA = _build_persona()


def _say_prefix() -> str:
    return f"{PERSONA.name}: "


def _say(text: str) -> None:
    print(_wrap(_say_prefix() + text))


def _say_list(title: str, items: t.Iterable[str]) -> None:
    _say(title)
    for it in items:
        print(_wrap(f"- {it}"))


# -----------------------------
# Database layer (SQLite)
# -----------------------------


SCHEMA_VERSION = 7


class DB:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    def _init(self) -> None:
        cur = self.conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA foreign_keys=ON;")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meta(
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS checkins(
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                day_key TEXT NOT NULL,
                mood INTEGER NOT NULL,
                energy INTEGER NOT NULL,
                stress INTEGER NOT NULL,
                intent TEXT NOT NULL,
                note TEXT NOT NULL,
                glyph TEXT NOT NULL,
                tags TEXT NOT NULL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_checkins_day_key ON checkins(day_key);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_checkins_created_at ON checkins(created_at);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS journal(
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                mood_hint INTEGER NOT NULL,
                energy_hint INTEGER NOT NULL,
                stress_hint INTEGER NOT NULL,
                tags TEXT NOT NULL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_created_at ON journal(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_updated_at ON journal(updated_at);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS exercises(
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exercises_kind ON exercises(kind);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exercises_created_at ON exercises(created_at);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings(
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS safety_notes(
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL
            );
            """
        )

        ver = self.get_meta_int("schema_version", 0)
        if ver == 0:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("install_id", _rand_token(24))
            self.set_meta("installed_at", _now().isoformat())
        elif ver != SCHEMA_VERSION:
            self._migrate(ver, SCHEMA_VERSION)
        self.conn.commit()

    def _migrate(self, from_v: int, to_v: int) -> None:
        # Minimal migrations. This app keeps schema stable and only adds tables/columns.
        cur = self.conn.cursor()
        v = from_v
        while v < to_v:
            nv = v + 1
            if nv == 5:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_checkins_created_at ON checkins(created_at);")
            if nv == 6:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS safety_notes(
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        body TEXT NOT NULL
                    );
                    """
                )
            if nv == 7:
                # settings table already exists; just ensure it
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS settings(
                        k TEXT PRIMARY KEY,
                        v TEXT NOT NULL
                    );
                    """
                )
            v = nv
        self.set_meta("schema_version", str(to_v))
        self.conn.commit()

    def get_meta(self, k: str, default: str | None = None) -> str | None:
        cur = self.conn.cursor()
        cur.execute("SELECT v FROM meta WHERE k=?", (k,))
        row = cur.fetchone()
        return row["v"] if row else default

    def get_meta_int(self, k: str, default: int) -> int:
        v = self.get_meta(k, None)
        if v is None:
            return default
        with contextlib.suppress(Exception):
            return int(v)
        return default

    def set_meta(self, k: str, v: str) -> None:
        cur = self.conn.cursor()
        cur.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def get_setting(self, k: str, default: str | None = None) -> str | None:
        cur = self.conn.cursor()
        cur.execute("SELECT v FROM settings WHERE k=?", (k,))
        row = cur.fetchone()
        return row["v"] if row else default

    def set_setting(self, k: str, v: str) -> None:
        cur = self.conn.cursor()
        cur.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self.conn.commit()


# -----------------------------
# Data models + serialization
# -----------------------------


def _split_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw)
    out: list[str] = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        p = re.sub(r"[^a-z0-9_-]+", "", p)
        if p and p not in out:
            out.append(p)
    return out[:28]


def _tags_json(tags: list[str]) -> str:
    return json.dumps(tags, ensure_ascii=False, separators=(",", ":"))


def _tags_from_json(s: str) -> list[str]:
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x) for x in v][:64]
    except Exception:
        pass
    return []


@dataclasses.dataclass
class CheckIn:
    id: str
    created_at: str
    day_key: str
    mood: int
    energy: int
    stress: int
    intent: str
    note: str
    glyph: str
    tags: list[str]


@dataclasses.dataclass
class JournalEntry:
    id: str
    created_at: str
    updated_at: str
    title: str
    body: str
    mood_hint: int
    energy_hint: int
    stress_hint: int
    tags: list[str]


@dataclasses.dataclass
class ExerciseLog:
    id: str
    created_at: str
    kind: str
    payload: dict[str, t.Any]


# -----------------------------
# “Emotional advice” engine
# -----------------------------


class AdviceEngine:
    def __init__(self) -> None:
        install_id = _get_install_id()
        seed = int(hashlib.sha256(("assistAI|" + install_id).encode("utf-8")).hexdigest()[:16], 16)
        self.rng = random.Random(seed)

    def micro_advice(self, mood: int, energy: int, stress: int) -> str:
        # mood/energy/stress are 0..100
        m, e, s = mood, energy, stress
        if m <= 20 and s >= 75:
            return (
                "Right now looks like a 'contain the moment' situation. "
                "Try the 3–2–1 reset: 3 slow breaths, name 2 things you can control today, then do 1 small action."
            )
        if e <= 20 and m <= 35:
            return (
                "Your system sounds tired. The goal isn’t productivity — it’s stabilization. "
                "Water, a bite of food, and a 3‑minute cleanup or stretch. Then you’re allowed to stop."
            )
        if s >= 90:
            return (
                "That stress level is loud. Reduce decisions for 15 minutes. "
                "Pick one boundary: 'not now', 'not today', or 'only the minimum'."
            )
        if m >= 80 and e >= 60 and s <= 55:
            return (
                "You’ve got clean momentum. Use it kindly: one meaningful task, no sprinting, no extra promises. "
                "Finish one thing and celebrate it."
            )
        if m >= 70 and s <= 30:
            return (
                "You seem steady. Protect that steadiness. "
                "Keep your day simple, and don’t donate your peace to other people’s chaos."
            )
