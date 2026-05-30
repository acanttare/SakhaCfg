"""Загрузка источников и извлечение конфигов из подписок."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote

import requests

CONFIG_PATTERN = re.compile(
    r"(?:vless|vmess|trojan|ss|hysteria2|tuic)://[^\s\"\'<>]+",
    re.IGNORECASE,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.txt"
USER_AGENT = "ConfigChecker/1.0"


def load_sources(path: Path = SOURCES_FILE) -> list[str]:
    if not path.exists():
        return []
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def fetch_url(url: str, timeout: int = 30) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def decode_subscription_body(text: str) -> str:
    cleaned = "".join(text.split())
    if not cleaned or "://" in cleaned:
        return text

    padding = (-len(cleaned)) % 4
    try:
        decoded = base64.b64decode(cleaned + "=" * padding).decode("utf-8", errors="ignore")
        if CONFIG_PATTERN.search(decoded):
            return decoded
    except Exception:
        pass
    return text


def extract_configs(text: str) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for cfg in CONFIG_PATTERN.findall(text):
        cfg = unquote(cfg.strip())
        if cfg not in seen:
            seen.add(cfg)
            unique.append(cfg)
    return unique


def fetch_all_configs(sources: list[str] | None = None) -> list[dict]:
    sources = sources or load_sources()
    results: list[dict] = []
    seen_configs: set[str] = set()

    for source_url in sources:
        try:
            raw = fetch_url(source_url)
            decoded = decode_subscription_body(raw)
            for cfg in extract_configs(decoded):
                if cfg in seen_configs:
                    continue
                seen_configs.add(cfg)
                results.append({"source": source_url, "config": cfg})
        except Exception as exc:
            print(f"[WARN] {source_url}: {exc}")

    return results


if __name__ == "__main__":
    items = fetch_all_configs()
    print(f"Найдено конфигов: {len(items)}")
