#!/usr/bin/env python3
"""
Точка входа для GitHub Actions.
Запускается только на ubuntu-latest runner — локально не предназначен.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

from checker import verify_all
from fetcher import collect, load_sources

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"
OUT_TXT = OUT_DIR / "working.txt"
OUT_JSON = OUT_DIR / "working.json"
OUT_FAILED_JSON = OUT_DIR / "failed.json"
SUBSCRIPTION_TITLE = os.environ.get("SUBSCRIPTION_TITLE", "SakhaCfg Subscription")
SUBSCRIPTION_EXPIRES_AT = os.environ.get("SUBSCRIPTION_EXPIRES_AT", "2026-12-31")
SUBSCRIPTION_LIMIT_GB = os.environ.get("SUBSCRIPTION_LIMIT_GB", "100")
MAX_WORKING_CONFIGS = int(os.environ.get("MAX_WORKING_CONFIGS", "50"))
GEO_TIMEOUT = float(os.environ.get("GEO_TIMEOUT", "5"))
GEO_URL = os.environ.get("GEO_URL", "https://ipwho.is/{host}")


def _require_github_env() -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        print("Этот скрипт запускается только в GitHub Actions.", file=sys.stderr)
        sys.exit(1)
    if not os.environ.get("XRAY_BIN"):
        print("Не задана переменная XRAY_BIN.", file=sys.stderr)
        sys.exit(1)
    if not Path(os.environ["XRAY_BIN"]).is_file():
        print(f"Xray не найден: {os.environ['XRAY_BIN']}", file=sys.stderr)
        sys.exit(1)


def _flag_from_country_code(code: str) -> str:
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🏳️"
    return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))


def _host_from_uri(uri: str) -> str:
    scheme = uri.split("://", 1)[0].lower()
    if scheme == "vmess":
        try:
            body = uri.split("://", 1)[1]
            pad = (-len(body)) % 4
            data = json.loads(base64.b64decode(body + "=" * pad))
            return str(data.get("add", "")).strip()
        except Exception:
            return ""
    try:
        return urllib.parse.urlparse(uri).hostname or ""
    except Exception:
        return ""


def _country_for_host(host: str, cache: dict[str, tuple[str, str]], lock: threading.Lock) -> tuple[str, str]:
    if not host:
        return "UN", "Unknown"
    with lock:
        if host in cache:
            return cache[host]
    try:
        url = GEO_URL.format(host=urllib.parse.quote(host, safe=""))
        resp = requests.get(url, timeout=GEO_TIMEOUT)
        data = resp.json()
        code = str(data.get("country_code", "")).upper() or "UN"
        name = str(data.get("country", "")).strip() or "Unknown"
        result = (code, name)
    except Exception:
        result = ("UN", "Unknown")
    with lock:
        cache[host] = result
    return result


def _label_row(row: dict, cache: dict[str, tuple[str, str]], lock: threading.Lock) -> dict:
    host = _host_from_uri(row["config"])
    cc, country = _country_for_host(host, cache, lock)
    flag = _flag_from_country_code(cc)
    label = f"{row['protocol']}({flag} {cc})"
    row["country_code"] = cc
    row["country"] = country
    row["flag"] = flag
    row["name"] = label
    row["config"] = _apply_label(row["config"], label)
    return row


def _apply_label(uri: str, label: str) -> str:
    scheme = uri.split("://", 1)[0].lower()
    if scheme == "vmess":
        try:
            body = uri.split("://", 1)[1]
            pad = (-len(body)) % 4
            data = json.loads(base64.b64decode(body + "=" * pad))
            data["ps"] = label
            raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            encoded = base64.b64encode(raw).decode("utf-8").rstrip("=")
            return f"vmess://{encoded}"
        except Exception:
            return uri
    base = uri.split("#", 1)[0]
    return f"{base}#{urllib.parse.quote(label)}"


def main() -> None:
    _require_github_env()

    configs, source_errors = collect()
    sources_count = len(load_sources())
    print(f"Источников: {sources_count}, ошибок загрузки: {len(source_errors)}")
    print(f"Конфигов для проверки: {len(configs)}")
    print(f"Потоков: {os.environ.get('PARALLEL_WORKERS', '30')}\n")

    if not configs:
        print("Нет конфигов. Добавь ссылки в sources.txt")
        working, failed = [], []
    else:
        working, failed = verify_all(configs)
        working = working[:MAX_WORKING_CONFIGS]

    geo_cache: dict[str, tuple[str, str]] = {}
    geo_lock = threading.Lock()
    if working:
        with ThreadPoolExecutor(max_workers=12) as pool:
            working = list(pool.map(lambda r: _label_row(r, geo_cache, geo_lock), working))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "subscription": {
            "title": SUBSCRIPTION_TITLE,
            "expires_at": SUBSCRIPTION_EXPIRES_AT,
            "limit_gb": int(SUBSCRIPTION_LIMIT_GB),
        },
        "runner": os.environ.get("RUNNER_OS", "Linux"),
        "total_sources": sources_count,
        "total_checked": len(configs),
        "total_working": len(working),
        "total_failed": len(failed),
        "source_errors": source_errors,
        "working": working,
        "failed": failed,
    }

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FAILED_JSON.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# title: {SUBSCRIPTION_TITLE}",
        f"# expires_at: {SUBSCRIPTION_EXPIRES_AT}",
        f"# limit_gb: {SUBSCRIPTION_LIMIT_GB}",
        "",
    ]
    lines.extend(w["config"] for w in working)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nИтог: {len(working)}/{len(configs)} рабочих")
    print(f"→ {OUT_TXT}")
    print(f"→ {OUT_JSON}")
    print(f"→ {OUT_FAILED_JSON}")

    # GitHub Actions summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Результат проверки\n\n")
            f.write(f"- Проверено: **{len(configs)}**\n")
            f.write(f"- Рабочих: **{len(working)}**\n")
            f.write(f"- Нерабочих: **{len(failed)}**\n\n")
            if working:
                f.write("### Рабочие конфиги\n\n")
                for w in working:
                    f.write(f"- `{w['protocol']}` {w['name']} — {w['latency_ms']} ms\n")


if __name__ == "__main__":
    main()
