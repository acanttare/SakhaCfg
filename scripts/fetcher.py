"""Загрузка sources.txt и извлечение конфигов из подписок."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote

import requests

CONFIG_RE = re.compile(
    r"(?:vless|vmess|trojan|ss|hysteria2|tuic)://[^\s\"\'<>\r\n]+",
    re.IGNORECASE,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.txt"
UA = "GitHub-Config-Checker/2.0"


def load_sources() -> list[str]:
    if not SOURCES.exists():
        return []
    urls = []
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def _fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text


def _decode_body(text: str) -> str:
    compact = "".join(text.split())
    if not compact or "://" in compact:
        return text
    pad = (-len(compact)) % 4
    try:
        decoded = base64.b64decode(compact + "=" * pad).decode("utf-8", errors="ignore")
        if CONFIG_RE.search(decoded):
            return decoded
    except Exception:
        pass
    # построчный base64 (некоторые подписки)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pad = (-len(line)) % 4
        try:
            row = base64.b64decode(line + "=" * pad).decode("utf-8", errors="ignore")
            if CONFIG_RE.search(row):
                lines.append(row)
        except Exception:
            lines.append(line)
    return "\n".join(lines) if lines else text


def _extract(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in CONFIG_RE.findall(text):
        cfg = unquote(raw.strip())
        if cfg not in seen:
            seen.add(cfg)
            out.append(cfg)
    return out


def collect() -> tuple[list[dict], list[str]]:
    """Возвращает (конфиги, ошибки загрузки источников)."""
    configs: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()

    for url in load_sources():
        try:
            body = _decode_body(_fetch(url))
            for cfg in _extract(body):
                if cfg in seen:
                    continue
                seen.add(cfg)
                configs.append({"source": url, "config": cfg})
        except Exception as exc:
            msg = f"{url}: {exc}"
            errors.append(msg)
            print(f"::warning::{msg}")

    return configs, errors
