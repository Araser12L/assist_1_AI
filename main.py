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
        if e >= 80 and s >= 65:
            return (
                "High energy + high stress can feel like a storm. "
                "Move your body for 6 minutes (walk, stairs, shaking out your arms), then reassess."
            )
        if m <= 35 and s <= 40:
            return (
                "Low mood with low alarm often needs warmth and connection. "
                "Do one sensory comfort (tea, shower, fresh air) and send one low‑stakes message to someone safe."
            )
        return (
            "Let’s keep it doable: check basics (water, food, light, movement). "
            "Then choose the smallest next step you can complete in under 5 minutes."
        )

    def reframe(self, label: str) -> str:
        label = label.strip().lower()
        common = {
            "guilt": "Guilt can be a compass, not a prison. What value is it pointing to, and what repair is realistic today?",
            "shame": "Shame says 'I am bad.' Try swapping to 'I did a thing I regret.' That gives you room to learn and still belong.",
            "anger": "Anger often protects something tender. What boundary was crossed — and what boundary do you need next?",
            "grief": "Grief is love with nowhere to go. Let it have a small place today: one memory, one breath, one honest sentence.",
            "fear": "Fear zooms in. Zoom out: most likely outcome, worst outcome, and what support you’d use if it happened.",
            "lonely": "Loneliness is a signal, not a verdict. Can you make one bid for connection — small, honest, low stakes?",
            "overwhelmed": "Overwhelm means your load exceeded your capacity. Let’s reduce the load first, then rebuild capacity.",
            "numb": "Numb can be protection. If you can’t feel big feelings, aim for small sensations: warmth, texture, movement, sound.",
        }
        if label in common:
            return common[label]
        starters = [
            "Name it precisely, then soften it:",
            "Try giving the feeling a little space:",
            "Let’s separate facts from the story:",
            "Try a kinder translation:",
        ]
        self.rng.shuffle(starters)
        return (
            f"{starters[0]} 'I’m noticing {label}.' "
            "Then ask: what do I need right now — comfort, clarity, protection, or a next step?"
        )

    def validate_crisis(self, text: str) -> tuple[bool, str]:
        t0 = text.lower()
        # This is intentionally conservative. It doesn't call external services.
        triggers = [
            "suicide",
            "kill myself",
            "end it",
            "self harm",
            "self-harm",
            "hurt myself",
            "overdose",
            "i want to die",
        ]
        if any(x in t0 for x in triggers):
            msg = (
                "I’m really glad you said something. I can’t handle emergencies, but you deserve real help right now.\n\n"
                "If you’re in immediate danger, call your local emergency number.\n"
                "If you can, reach out to someone you trust and stay with them.\n"
                "If you want, tell me your country and I’ll help you find a crisis line to call or text."
            )
            return True, msg
        return False, ""

    def gentle_rules(self) -> list[str]:
        return list(PERSONA.gentle_rules)

    def grounding_script(self, seconds: int = 90) -> list[str]:
        # A timed script. The app can display it stepwise.
        seconds = _clamp(int(seconds), 30, 300)
        steps = []
        steps.append(f"Set a timer for {seconds} seconds. We’re not fixing life; we’re settling your nervous system.")
        steps.append("Put one hand on your chest or belly. Feel the contact. That contact is evidence: you are here.")
        steps.append("Inhale through the nose for 4, exhale for 6. If you can’t, just make the exhale longer than the inhale.")
        steps.append("Name 5 things you can see. Say them quietly if you can.")
        steps.append("Name 4 things you can feel (texture, temperature, pressure).")
        steps.append("Name 3 sounds you can hear (near, far, internal).")
        steps.append("Name 2 scents or tastes, even if it’s 'nothing.'")
        steps.append("Name 1 tiny next action that makes you safer or steadier in the next 10 minutes.")
        return steps


ENGINE: "AdviceEngine"


# -----------------------------
# Install ID (stable randomness)
# -----------------------------


_INSTALL_ID_CACHE: str | None = None


def _get_install_id() -> str:
    global _INSTALL_ID_CACHE
    if _INSTALL_ID_CACHE is not None:
        return _INSTALL_ID_CACHE
    # We store it in a small sidecar file, because we want it even before DB init.
    sidecar = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".assistAI.install_id")
    if os.path.exists(sidecar):
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if re.fullmatch(r"[A-Za-z0-9_-]{12,64}", v or ""):
                    _INSTALL_ID_CACHE = v
                    return v
        except Exception:
            pass
    v = _rand_token(24)
    try:
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write(v)
    except Exception:
        # If it fails, just keep it in memory for this run.
        pass
    _INSTALL_ID_CACHE = v
    return v


ENGINE = AdviceEngine()


# -----------------------------
# Core operations
# -----------------------------


def db_path_default() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "assistAI_data.sqlite3")


def db_open(path: str) -> DB:
    return DB(path)


def _glyph_for_checkin(mood: int, energy: int, stress: int, intent: str) -> str:
    # Create a stable, compact glyph token; purely local.
    payload = json.dumps({"m": mood, "e": energy, "s": stress, "i": intent}, sort_keys=True).encode("utf-8")
    h = hashlib.blake2s(payload, digest_size=10, key=_get_install_id().encode("utf-8")).digest()
    return _b64url(h)[:16]


def _normalize_intent(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s{2,}", " ", s)
    if not s:
        return "steady myself"
    if len(s) > 64:
        s = s[:64].rstrip()
    return s


def add_checkin(db: DB, mood: int, energy: int, stress: int, intent: str, note: str, tags: list[str]) -> CheckIn:
    created = _now().isoformat()
    ck = CheckIn(
        id="ci_" + _rand_token(20),
        created_at=created,
        day_key=_today_key(),
        mood=_clamp(mood, 0, 100),
        energy=_clamp(energy, 0, 100),
        stress=_clamp(stress, 0, 100),
        intent=_normalize_intent(intent),
        note=(note or "").strip()[:2400],
        glyph=_glyph_for_checkin(mood, energy, stress, intent),
        tags=tags[:28],
    )
    cur = db.conn.cursor()
    cur.execute(
        """
        INSERT INTO checkins(id,created_at,day_key,mood,energy,stress,intent,note,glyph,tags)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ck.id,
            ck.created_at,
            ck.day_key,
            ck.mood,
            ck.energy,
            ck.stress,
            ck.intent,
            ck.note,
            ck.glyph,
            _tags_json(ck.tags),
        ),
    )
    db.conn.commit()
    return ck


def list_checkins(db: DB, limit: int = 30) -> list[CheckIn]:
    limit = _clamp(limit, 1, 500)
    cur = db.conn.cursor()
    cur.execute(
        "SELECT * FROM checkins ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    out: list[CheckIn] = []
    for r in cur.fetchall():
        out.append(
            CheckIn(
                id=r["id"],
                created_at=r["created_at"],
                day_key=r["day_key"],
                mood=int(r["mood"]),
                energy=int(r["energy"]),
                stress=int(r["stress"]),
                intent=r["intent"],
                note=r["note"],
                glyph=r["glyph"],
                tags=_tags_from_json(r["tags"]),
            )
        )
    return out


def get_checkin(db: DB, id_: str) -> CheckIn | None:
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM checkins WHERE id=?", (id_,))
    r = cur.fetchone()
    if not r:
        return None
    return CheckIn(
        id=r["id"],
        created_at=r["created_at"],
        day_key=r["day_key"],
        mood=int(r["mood"]),
        energy=int(r["energy"]),
        stress=int(r["stress"]),
        intent=r["intent"],
        note=r["note"],
        glyph=r["glyph"],
        tags=_tags_from_json(r["tags"]),
    )


def add_journal(db: DB, title: str, body: str, mood_hint: int, energy_hint: int, stress_hint: int, tags: list[str]) -> JournalEntry:
    now = _now().isoformat()
    title = (title or "").strip() or "Untitled"
    title = title[:140]
    body = (body or "").strip()[:24000]
    je = JournalEntry(
        id="jr_" + _rand_token(20),
        created_at=now,
        updated_at=now,
        title=title,
        body=body,
        mood_hint=_clamp(mood_hint, 0, 100),
        energy_hint=_clamp(energy_hint, 0, 100),
        stress_hint=_clamp(stress_hint, 0, 100),
        tags=tags[:40],
    )
    cur = db.conn.cursor()
    cur.execute(
        """
        INSERT INTO journal(id,created_at,updated_at,title,body,mood_hint,energy_hint,stress_hint,tags)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            je.id,
            je.created_at,
            je.updated_at,
            je.title,
            je.body,
            je.mood_hint,
            je.energy_hint,
            je.stress_hint,
            _tags_json(je.tags),
        ),
    )
    db.conn.commit()
    return je


def list_journal(db: DB, limit: int = 25) -> list[JournalEntry]:
    limit = _clamp(limit, 1, 500)
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM journal ORDER BY created_at DESC LIMIT ?", (limit,))
    out: list[JournalEntry] = []
    for r in cur.fetchall():
        out.append(
            JournalEntry(
                id=r["id"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                title=r["title"],
                body=r["body"],
                mood_hint=int(r["mood_hint"]),
                energy_hint=int(r["energy_hint"]),
                stress_hint=int(r["stress_hint"]),
                tags=_tags_from_json(r["tags"]),
            )
        )
    return out


def get_journal(db: DB, id_: str) -> JournalEntry | None:
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM journal WHERE id=?", (id_,))
    r = cur.fetchone()
    if not r:
        return None
    return JournalEntry(
        id=r["id"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        title=r["title"],
        body=r["body"],
        mood_hint=int(r["mood_hint"]),
        energy_hint=int(r["energy_hint"]),
        stress_hint=int(r["stress_hint"]),
        tags=_tags_from_json(r["tags"]),
    )


def update_journal(db: DB, id_: str, title: str | None = None, body: str | None = None, tags: list[str] | None = None) -> bool:
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM journal WHERE id=?", (id_,))
    r = cur.fetchone()
    if not r:
        return False
    new_title = (title if title is not None else r["title"]).strip()[:140] or "Untitled"
    new_body = (body if body is not None else r["body"]).strip()[:24000]
    new_tags = tags if tags is not None else _tags_from_json(r["tags"])
    now = _now().isoformat()
    cur.execute(
        """
        UPDATE journal
        SET updated_at=?, title=?, body=?, tags=?
        WHERE id=?
        """,
        (now, new_title, new_body, _tags_json(new_tags[:40]), id_),
    )
    db.conn.commit()
    return True


def delete_journal(db: DB, id_: str) -> bool:
    cur = db.conn.cursor()
    cur.execute("DELETE FROM journal WHERE id=?", (id_,))
    changed = cur.rowcount > 0
    db.conn.commit()
    return changed


def add_exercise_log(db: DB, kind: str, payload: dict[str, t.Any]) -> ExerciseLog:
    now = _now().isoformat()
    ex = ExerciseLog(
        id="ex_" + _rand_token(20),
        created_at=now,
        kind=kind[:40],
        payload=payload,
    )
    cur = db.conn.cursor()
    cur.execute(
        "INSERT INTO exercises(id,created_at,kind,payload_json) VALUES(?,?,?,?)",
        (ex.id, ex.created_at, ex.kind, json.dumps(ex.payload, ensure_ascii=False)),
    )
    db.conn.commit()
    return ex


def list_exercises(db: DB, kind: str | None = None, limit: int = 25) -> list[ExerciseLog]:
    limit = _clamp(limit, 1, 500)
    cur = db.conn.cursor()
    if kind:
        cur.execute("SELECT * FROM exercises WHERE kind=? ORDER BY created_at DESC LIMIT ?", (kind, limit))
    else:
        cur.execute("SELECT * FROM exercises ORDER BY created_at DESC LIMIT ?", (limit,))
    out: list[ExerciseLog] = []
    for r in cur.fetchall():
        try:
            payload = json.loads(r["payload_json"])
            if not isinstance(payload, dict):
                payload = {"raw": r["payload_json"]}
        except Exception:
            payload = {"raw": r["payload_json"]}
        out.append(ExerciseLog(id=r["id"], created_at=r["created_at"], kind=r["kind"], payload=payload))
    return out


# -----------------------------
# Exercises (CBT-ish, grounding)
# -----------------------------


def _exercise_breath_box() -> dict[str, t.Any]:
    # A structured 4-4-4-4 box breathing session.
    rounds = random.choice([4, 5, 6, 7])
    counts = random.choice([(4, 4, 4, 4), (4, 6, 4, 6), (3, 5, 3, 5)])
    return {"rounds": rounds, "counts": {"inhale": counts[0], "hold1": counts[1], "exhale": counts[2], "hold2": counts[3]}}


def run_breathing(db: DB) -> None:
    _say("Okay. We’ll do a short breathing pattern. Not to 'fix' you — just to give you room.")
    p = _exercise_breath_box()
    rounds = int(p["rounds"])
    c = p["counts"]
    inh, h1, exh, h2 = int(c["inhale"]), int(c["hold1"]), int(c["exhale"]), int(c["hold2"])
    _say(f"Pattern: inhale {inh}, hold {h1}, exhale {exh}, hold {h2}. Rounds: {rounds}.")
    _say("If any holds feel bad, skip them. The only rule is: exhale a little longer than inhale.")
    _pause()

    t0 = _now().isoformat()
    for i in range(1, rounds + 1):
        print(_hr("·"))
        _say(f"Round {i}/{rounds}. Inhale…")
        time.sleep(min(inh, 9))
        if h1 > 0:
            _say("Hold…")
            time.sleep(min(h1, 9))
        _say("Exhale…")
        time.sleep(min(exh, 10))
        if h2 > 0:
            _say("Hold…")
            time.sleep(min(h2, 9))
    print(_hr("·"))
    _say("Nice. Notice if your shoulders dropped even 1%. That counts.")

    add_exercise_log(
        db,
        "breathing",
        {"started_at": t0, "ended_at": _now().isoformat(), "rounds": rounds, "counts": {"inhale": inh, "hold1": h1, "exhale": exh, "hold2": h2}},
    )
    _pause()


def run_grounding(db: DB) -> None:
    seconds = random.choice([60, 75, 90, 105, 120, 135])
    steps = ENGINE.grounding_script(seconds=seconds)
    _say("Let’s do a grounding reset. I’ll guide you line by line.")
    _pause()
    t0 = _now().isoformat()
    for s in steps:
        _say(s)
        _pause()
    add_exercise_log(db, "grounding", {"started_at": t0, "ended_at": _now().isoformat(), "seconds": seconds, "steps": steps})
    _say("If you want, do one tiny next action now. Then come back.")
    _pause()


def _choose_from(prompt: str, options: list[str], default: int = 1) -> int:
    print(_wrap(prompt))
    for i, o in enumerate(options, 1):
        print(f"{i:2d}) {o}")
    return _int_prompt("Choose", 1, len(options), default=default)


def run_reframe(db: DB) -> None:
    opts = ["guilt", "shame", "anger", "grief", "fear", "lonely", "overwhelmed", "numb", "something else"]
    ix = _choose_from("Which feeling fits closest right now?", opts, default=3)
    label = opts[ix - 1]
    if label == "something else":
        label = _soft_prompt("Name it in 1–3 words:").strip().lower()[:40] or "a lot"
    msg = ENGINE.reframe(label)
    _print_box("Reframe", msg)
    add_exercise_log(db, "reframe", {"label": label, "message": msg, "at": _now().isoformat()})
    _pause()


def run_thought_record(db: DB) -> None:
    _say("Thought record time. Short and honest. You’re not on trial.")
    situation = _soft_prompt("Situation (1 line):")[:300]
    feelings = _soft_prompt("Feelings (e.g., anxious 70, sad 40):")[:300]
    auto_thought = _soft_prompt("Automatic thought (what your brain yelled):")[:800]
    evidence_for = _soft_prompt("Evidence FOR that thought (short):")[:800]
    evidence_against = _soft_prompt("Evidence AGAINST (short):")[:800]
    alt_thought = _soft_prompt("More balanced thought (one sentence):")[:800]
    next_step = _soft_prompt("Next step (tiny action):")[:300]
    payload = {
        "situation": situation,
        "feelings": feelings,
        "automatic_thought": auto_thought,
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "balanced_thought": alt_thought,
        "next_step": next_step,
    }
    add_exercise_log(db, "thought_record", payload)
    _say("That was brave. Balanced thoughts aren’t 'positive' thoughts. They’re honest thoughts.")
    _pause()


def run_values_clarifier(db: DB) -> None:
    _say("Let’s find what matters to you. Not what you *should* care about. What you actually care about.")
    domains = [
        "relationships",
        "health",
        "learning",
        "work / craft",
        "community",
        "creativity",
        "stability",
        "adventure",
        "spirituality",
        "service",
    ]
    self_rng = random.Random(int(hashlib.sha256(("values|" + _get_install_id()).encode("utf-8")).hexdigest()[:16], 16))
    self_rng.shuffle(domains)
    chosen = domains[:5]
    _say_list("Pick 2 domains that feel important right now:", chosen)
    a = _int_prompt("First pick", 1, 5, default=1)
    b = _int_prompt("Second pick (different)", 1, 5, default=2)
    if b == a:
        b = 5 if a != 5 else 4
    d1, d2 = chosen[a - 1], chosen[b - 1]
    v1 = _soft_prompt(f"In '{d1}', what do you want to stand for? (3-8 words):")[:80].strip()
    v2 = _soft_prompt(f"In '{d2}', what do you want to stand for? (3-8 words):")[:80].strip()
    act = _soft_prompt("One tiny action that matches one of those values (today or tomorrow):")[:120].strip()
    payload = {"domains_presented": chosen, "picked": [d1, d2], "value_lines": [v1, v2], "tiny_action": act, "at": _now().isoformat()}
    add_exercise_log(db, "values", payload)
    _say("Values are a lighthouse, not a whip. If your action is small, it’s still aligned.")
    _pause()


def run_safety_plan(db: DB) -> None:
    _say("Let’s make a mini safety plan. This is for rough moments — a script you can follow.")
    warn = _soft_prompt("Warning signs (what tells you you're sliding):")[:600]
    coping = _soft_prompt("Coping steps (things you can do alone):")[:600]
    people = _soft_prompt("People you can contact (names / initials):")[:600]
    places = _soft_prompt("Places that help (where you feel safer):")[:600]
    remove = _soft_prompt("Reduce risk (what to move away / lock / avoid):")[:600]
    reason = _soft_prompt("One reason to stay (even small):")[:300]

    body = json.dumps(
        {
            "warning_signs": warn,
            "coping_steps": coping,
            "people": people,
            "places": places,
            "reduce_risk": remove,
            "reason_to_stay": reason,
            "created_at": _now().isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )
    sid = "sf_" + _rand_token(20)
    cur = db.conn.cursor()
    cur.execute(
        "INSERT INTO safety_notes(id,created_at,kind,body) VALUES(?,?,?,?)",
        (sid, _now().isoformat(), "safety_plan", body),
    )
    db.conn.commit()
    _say("Saved. You don’t need this plan often — but when you do, you’ll be glad it exists.")
    _pause()


def view_safety_plans(db: DB) -> None:
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM safety_notes WHERE kind='safety_plan' ORDER BY created_at DESC LIMIT 10")
    rows = cur.fetchall()
    if not rows:
        _say("No safety plans saved yet.")
        _pause()
        return
    _say("Here are your most recent safety plans:")
    for i, r in enumerate(rows, 1):
        print(f"{i:2d}) {r['created_at']}  ({r['id']})")
    ix = _int_prompt("Open which one", 1, len(rows), default=1)
    r = rows[ix - 1]
    try:
        obj = json.loads(r["body"])
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        pretty = r["body"]
    _print_box("Safety plan", pretty)
    _pause()


# -----------------------------
# Insights / analytics (local)
# -----------------------------


def _avg(nums: list[int]) -> float:
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def _median(nums: list[int]) -> float:
    if not nums:
        return 0.0
    a = sorted(nums)
    n = len(a)
    mid = n // 2
    if n % 2 == 1:
        return float(a[mid])
    return (a[mid - 1] + a[mid]) / 2.0


def _trend(nums: list[int]) -> float:
    # Tiny linear trend estimate (slope) normalized.
    n = len(nums)
    if n < 3:
        return 0.0
    xs = list(range(n))
    xbar = sum(xs) / n
    ybar = sum(nums) / n
    num = sum((x - xbar) * (y - ybar) for x, y in zip(xs, nums))
    den = sum((x - xbar) ** 2 for x in xs) or 1.0
    return num / den


def show_insights(db: DB) -> None:
    items = list_checkins(db, limit=120)
    if not items:
        _say("No check-ins yet. Add one and I’ll start spotting patterns.")
        _pause()
        return

    moods = [c.mood for c in items][::-1]
    energies = [c.energy for c in items][::-1]
    stresses = [c.stress for c in items][::-1]
    last = items[0]

    _say("Here’s what I’m noticing from your recent check-ins.")
    print(_hr())
    print(_wrap(f"Latest: mood {last.mood}/100, energy {last.energy}/100, stress {last.stress}/100 — intent: {last.intent}"))
    print(_hr())

    def _fmt(name: str, arr: list[int]) -> str:
        return (
            f"{name:7s}  avg {(_avg(arr)):.1f}   median {(_median(arr)):.1f}   trend {(_trend(arr)):+.2f} per check-in"
        )

    print(_fmt("mood", moods))
    print(_fmt("energy", energies))
    print(_fmt("stress", stresses))
    print(_hr())

    # Gentle interpretation
    mood_tr = _trend(moods)
    stress_tr = _trend(stresses)
    if mood_tr > 0.4 and stress_tr < -0.3:
        _say("This looks like you’re climbing out of something. Keep the supports that helped — don’t stop them because you feel better.")
    elif mood_tr < -0.4 and stress_tr > 0.3:
        _say("This looks like your load is rising. Let’s reduce demands and add support. Want a grounding script or a thought record?")
    elif stress_tr > 0.35:
        _say("Stress is trending up. If you can, protect sleep and simplify commitments for a few days.")
    elif mood_tr < -0.35:
        _say("Mood is trending down. That’s a signal to add warmth and contact, not to punish yourself with 'shoulds.'")
    else:
        _say("Your pattern looks relatively stable. Stability is a win. Don’t underestimate it.")
    _pause()


# -----------------------------
# Exports (JSON / Markdown)
# -----------------------------


def _export_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "assistAI_exports")
    _safe_mkdir(out)
    return out


def export_json(db: DB) -> str:
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM checkins ORDER BY created_at ASC")
    checkins = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM journal ORDER BY created_at ASC")
    journal = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM exercises ORDER BY created_at ASC")
    exercises = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM meta")
    meta = {r["k"]: r["v"] for r in cur.fetchall()}

    payload = {
        "exported_at": _now().isoformat(),
        "install_id": _get_install_id(),
        "persona": dataclasses.asdict(PERSONA),
        "meta": meta,
        "checkins": checkins,
        "journal": journal,
        "exercises": exercises,
    }

    fn = f"assistAI_export_{_today_key()}_{_rand_token(8)}.json"
    path = os.path.join(_export_dir(), fn)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def export_markdown(db: DB) -> str:
    items = list_checkins(db, limit=400)
    journal = list_journal(db, limit=250)
    exercises = list_exercises(db, limit=250)

    fn = f"assistAI_export_{_today_key()}_{_rand_token(8)}.md"
    path = os.path.join(_export_dir(), fn)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# assistAI export\n\n")
        f.write(f"- exported_at: `{_now().isoformat()}`\n")
        f.write(f"- install_id: `{_get_install_id()}`\n")
        f.write(f"- persona: `{PERSONA.name}` ({PERSONA.voice})\n\n")

        f.write("## Check-ins\n\n")
        for c in items[::-1]:
            f.write(f"- `{c.created_at}` mood {c.mood} energy {c.energy} stress {c.stress}  intent: **{c.intent}**  glyph `{c.glyph}`\n")
            if c.tags:
                f.write(f"  - tags: {', '.join(c.tags)}\n")
            if c.note.strip():
                note = c.note.strip().replace("\n", " ").strip()
                if len(note) > 240:
                    note = note[:240].rstrip() + "…"
                f.write(f"  - note: {note}\n")
        f.write("\n")

        f.write("## Journal\n\n")
        for j in journal[::-1]:
            f.write(f"### {j.title}\n\n")
            f.write(f"- id: `{j.id}`\n")
            f.write(f"- created_at: `{j.created_at}`\n")
            f.write(f"- updated_at: `{j.updated_at}`\n")
            f.write(f"- hints: mood {j.mood_hint} energy {j.energy_hint} stress {j.stress_hint}\n")
            if j.tags:
                f.write(f"- tags: {', '.join(j.tags)}\n")
            f.write("\n")
            f.write(j.body.strip() + "\n\n")

        f.write("## Exercises\n\n")
        for e in exercises[::-1]:
            f.write(f"- `{e.created_at}` **{e.kind}** `{e.id}`\n")
            f.write("  ```json\n")
            f.write(json.dumps(e.payload, ensure_ascii=False, indent=2))
            f.write("\n  ```\n")
        f.write("\n")

    return path


# -----------------------------
# Menu UI
# -----------------------------


def _banner(db: DB) -> None:
    install_id = _get_install_id()
    _print_box(
        _title(f"{PERSONA.name} — local support assistant"),
        (
            f"Voice: {PERSONA.voice}\n\n"
            f"{PERSONA.boundary_line}\n\n"
            f"Install ID: {install_id}\n"
            f"Database: {db.path}"
        ),
    )


def _menu() -> list[tuple[str, str]]:
    return [
        ("1", "Check-in (mood/energy/stress) + micro advice"),
        ("2", "Journal: write a new entry"),
        ("3", "Journal: list + read entries"),
        ("4", "Exercise: grounding script"),
        ("5", "Exercise: breathing rounds"),
        ("6", "Exercise: reframe a feeling"),
        ("7", "Exercise: thought record"),
        ("8", "Exercise: values clarifier"),
        ("9", "Safety: create a mini safety plan"),
        ("10", "Safety: view saved safety plans"),
        ("11", "Insights: patterns from check-ins"),
        ("12", "Export: JSON + Markdown"),
        ("13", "Settings: small preferences"),
        ("0", "Exit"),
    ]


def _show_menu() -> None:
    print(_hr())
    for k, label in _menu():
        print(f"{k:>2}  {label}")
    print(_hr())


def do_checkin_flow(db: DB) -> None:
    _say("Let’s do a check-in. No judgement. Just data.")
    mood = _int_prompt("Mood", 0, 100, default=50)
    energy = _int_prompt("Energy", 0, 100, default=50)
    stress = _int_prompt("Stress", 0, 100, default=50)
    intent = _soft_prompt("Intent (what you want from this moment):")[:80]
    note = _soft_prompt("Note (optional, 1-2 lines):")[:700]
    tags = _split_tags(_soft_prompt("Tags (optional, comma/space separated):"))

    crisis, crisis_msg = ENGINE.validate_crisis(" ".join([intent, note]))
    if crisis:
        _print_box("Important", crisis_msg)

    ck = add_checkin(db, mood, energy, stress, intent, note, tags)
    _say("Saved. Thank you for being honest with me.")
    _say(f"Your check-in glyph is `{ck.glyph}`. You don’t need it, but it can be a little anchor.")
    msg = ENGINE.micro_advice(ck.mood, ck.energy, ck.stress)
    _print_box("Micro advice", msg)
    _say_list("Gentle rules (today):", ENGINE.gentle_rules())
    _pause()


def _read_multiline(prompt: str, max_chars: int = 24000) -> str:
    _say(prompt)
    _say("Type your text. End with a single line containing only `.`")
    lines: list[str] = []
    n = 0
    while True:
        line = input()
        if line.strip() == ".":
            break
        if n + len(line) + 1 > max_chars:
            _say("Okay, that’s a lot. I’m going to stop there so it stays manageable.")
            break
        lines.append(line)
        n += len(line) + 1
    return "\n".join(lines).strip()


def do_journal_new(db: DB) -> None:
    _say("Let’s write. You can be messy. This is for you.")
    title = _soft_prompt("Title:")[:140]
    mood_hint = _int_prompt("Mood hint", 0, 100, default=50)
    energy_hint = _int_prompt("Energy hint", 0, 100, default=50)
    stress_hint = _int_prompt("Stress hint", 0, 100, default=50)
    tags = _split_tags(_soft_prompt("Tags (optional):"))
    body = _read_multiline("Write your entry now.", max_chars=24000)

    crisis, crisis_msg = ENGINE.validate_crisis(body)
    if crisis:
        _print_box("Important", crisis_msg)

    je = add_journal(db, title, body, mood_hint, energy_hint, stress_hint, tags)
    _say(f"Saved journal entry `{je.id}`.")
    _pause()


def do_journal_list_and_read(db: DB) -> None:
    items = list_journal(db, limit=35)
    if not items:
        _say("No journal entries yet. Want to write one?")
        _pause()
        return
    _say("Recent journal entries:")
    for i, j in enumerate(items, 1):
        short = j.title
        if len(short) > 42:
            short = short[:42].rstrip() + "…"
        print(f"{i:2d}) {j.created_at[:19]}  {short}  ({j.id})")
    ix = _int_prompt("Open which one", 1, len(items), default=1)
    entry = items[ix - 1]
    _print_box(entry.title, entry.body)

    if _yn("Edit this entry?", default=False):
        new_title = _soft_prompt("New title (blank to keep):")
        if not new_title.strip():
            new_title = None
        new_body = _read_multiline("New body (end with '.')", max_chars=24000)
        if not new_body.strip():
            new_body = None
        new_tags = None
        if _yn("Edit tags?", default=False):
            new_tags = _split_tags(_soft_prompt("Tags:"))
        ok = update_journal(db, entry.id, title=new_title, body=new_body, tags=new_tags)
        if ok:
            _say("Updated.")
        else:
            _say("Couldn’t update (entry missing).")
        _pause()

    if _yn("Delete this entry?", default=False):
        if _yn("Are you sure? This can’t be undone.", default=False):
            ok = delete_journal(db, entry.id)
            _say("Deleted." if ok else "Couldn’t delete (entry missing).")
            _pause()


def do_export(db: DB) -> None:
    _say("Exporting your data to files you can keep.")
    jp = export_json(db)
    mp = export_markdown(db)
    _say(f"Done.\n- JSON: {jp}\n- Markdown: {mp}")
    _pause()


def do_settings(db: DB) -> None:
    _say("Settings are intentionally small. This is a support tool, not a configuration project.")
    cur_name = db.get_setting("display_name", "")
    cur_width = int(db.get_setting("wrap_width", "86") or "86")
    print(_hr())
    print(f"1) Display name (shown in exports): {cur_name!r}")
    print(f"2) Wrap width: {cur_width}")
    print("0) Back")
    print(_hr())
    choice = _soft_prompt("Choose:")
    if choice == "1":
        v = _soft_prompt("Display name (blank to clear):")
        db.set_setting("display_name", v.strip())
        _say("Saved.")
        _pause()
    elif choice == "2":
        w = _int_prompt("Wrap width", 60, 120, default=cur_width)
        db.set_setting("wrap_width", str(w))
        _say("Saved. (Restart app to apply everywhere.)")
        _pause()


# -----------------------------
# CLI commands (non-interactive)
# -----------------------------


def cmd_quick_checkin(db: DB, mood: int, energy: int, stress: int, intent: str, note: str, tags: str) -> None:
    ck = add_checkin(db, mood, energy, stress, intent, note, _split_tags(tags))
    msg = ENGINE.micro_advice(ck.mood, ck.energy, ck.stress)
    print(json.dumps(dataclasses.asdict(ck), ensure_ascii=False, indent=2))
    print()
    print(_wrap(msg))


def cmd_export(db: DB, fmt: str) -> None:
    if fmt == "json":
        print(export_json(db))
    elif fmt == "md":
        print(export_markdown(db))
    elif fmt == "both":
        print(export_json(db))
        print(export_markdown(db))
    else:
        raise SystemExit("format must be one of: json, md, both")


def cmd_insights(db: DB) -> None:
    items = list_checkins(db, limit=120)
    if not items:
        print("No check-ins yet.")
        return
    moods = [c.mood for c in items][::-1]
    energies = [c.energy for c in items][::-1]
    stresses = [c.stress for c in items][::-1]
    payload = {
        "count": len(items),
        "mood": {"avg": _avg(moods), "median": _median(moods), "trend": _trend(moods)},
        "energy": {"avg": _avg(energies), "median": _median(energies), "trend": _trend(energies)},
        "stress": {"avg": _avg(stresses), "median": _median(stresses), "trend": _trend(stresses)},
        "latest": dataclasses.asdict(items[0]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# -----------------------------
# Main loop
# -----------------------------


def _apply_runtime_settings(db: DB) -> None:
    # only used for this run (wrap width).
    global _wrap
    w = db.get_setting("wrap_width", None)
    if w is not None:
        with contextlib.suppress(Exception):
            width = int(w)
            width = _clamp(width, 60, 120)

            def _wrap2(s: str, width: int = width) -> str:  # type: ignore[misc]
                return "\n".join(textwrap.fill(line, width=width) if line.strip() else "" for line in s.splitlines())

            _wrap = _wrap2  # type: ignore[assignment]


def interactive(db: DB) -> None:
    _apply_runtime_settings(db)
    _banner(db)
    _say("If you want, start with a check-in. I’ll meet you where you are.")
    _say_list("Today’s gentle rules:", ENGINE.gentle_rules())

    while True:
        _show_menu()
        choice = _soft_prompt("Choose:")
        if choice == "0":
            _say("Okay. Before you go: pick one kind thing you can do for yourself in the next hour.")
            break
        if choice == "1":
            do_checkin_flow(db)
        elif choice == "2":
            do_journal_new(db)
        elif choice == "3":
            do_journal_list_and_read(db)
        elif choice == "4":
            run_grounding(db)
        elif choice == "5":
            run_breathing(db)
        elif choice == "6":
            run_reframe(db)
        elif choice == "7":
            run_thought_record(db)
        elif choice == "8":
            run_values_clarifier(db)
        elif choice == "9":
            run_safety_plan(db)
        elif choice == "10":
            view_safety_plans(db)
        elif choice == "11":
            show_insights(db)
        elif choice == "12":
            do_export(db)
        elif choice == "13":
            do_settings(db)
        else:
            _say("I didn’t catch that. Choose a number from the menu.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="assistAI", description="Local emotional support + structure assistant (no keys needed).")
    p.add_argument("--db", default=db_path_default(), help="Path to SQLite DB (default: ./assistAI_data.sqlite3)")

    sub = p.add_subparsers(dest="cmd")

    q = sub.add_parser("checkin", help="Quick non-interactive check-in.")
    q.add_argument("--mood", type=int, required=True)
