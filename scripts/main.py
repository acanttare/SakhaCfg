#!/usr/bin/env python3
"""
Точка входа для GitHub Actions.
Запускается только на ubuntu-latest runner — локально не предназначен.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from checker import verify_all
from fetcher import collect, load_sources

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"
OUT_TXT = OUT_DIR / "working.txt"
OUT_JSON = OUT_DIR / "working.json"


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


def main() -> None:
    _require_github_env()

    configs, source_errors = collect()
    sources_count = len(load_sources())
    print(f"Источников: {sources_count}, ошибок загрузки: {len(source_errors)}")
    print(f"Конфигов для проверки: {len(configs)}\n")

    if not configs:
        print("Нет конфигов. Добавь ссылки в sources.txt")
        working, failed = [], []
    else:
        working, failed = verify_all(configs)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "runner": os.environ.get("RUNNER_OS", "Linux"),
        "total_sources": sources_count,
        "total_checked": len(configs),
        "total_working": len(working),
        "total_failed": len(failed),
        "source_errors": source_errors,
        "working": working,
    }

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_TXT.write_text(
        "\n".join(w["config"] for w in working) + ("\n" if working else ""),
        encoding="utf-8",
    )

    print(f"\nИтог: {len(working)}/{len(configs)} рабочих")
    print(f"→ {OUT_TXT}")
    print(f"→ {OUT_JSON}")

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
