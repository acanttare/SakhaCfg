"""Проверка конфигов через Xray на GitHub Actions runner."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from parser import display_name, parse_uri, protocol, xray_config

TEST_URL = os.environ.get("TEST_URL", "https://www.google.com/generate_204")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "12"))
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "10808"))
XRAY_BIN = Path(os.environ["XRAY_BIN"])


def _port_ready(host: str, port: int, attempts: int = 30) -> bool:
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def probe(uri: str) -> tuple[bool, str, int]:
    outbound = parse_uri(uri)
    if not outbound:
        return False, "unsupported protocol", 0

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "xray.json"
        cfg.write_text(json.dumps(xray_config(outbound, SOCKS_PORT)), encoding="utf-8")

        proc = subprocess.Popen(
            [str(XRAY_BIN), "run", "-c", str(cfg)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        t0 = time.time()
        try:
            if not _port_ready("127.0.0.1", SOCKS_PORT):
                return False, "xray start timeout", 0

            proxy = f"socks5h://127.0.0.1:{SOCKS_PORT}"
            resp = requests.get(
                TEST_URL,
                proxies={"http": proxy, "https": proxy},
                timeout=TEST_TIMEOUT,
            )
            ms = int((time.time() - t0) * 1000)
            if resp.status_code in (200, 204):
                return True, "", ms
            return False, f"HTTP {resp.status_code}", ms
        except Exception as exc:
            return False, str(exc), int((time.time() - t0) * 1000)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def verify_all(items: list[dict]) -> tuple[list[dict], list[dict]]:
    working: list[dict] = []
    failed: list[dict] = []
    total = len(items)

    for i, item in enumerate(items, 1):
        uri = item["config"]
        name = display_name(uri)
        proto = protocol(uri)
        print(f"[{i}/{total}] {proto} {name} ...", flush=True)

        ok, err, ms = probe(uri)
        row = {
            "config": uri,
            "protocol": proto,
            "name": name,
            "source": item["source"],
            "latency_ms": ms,
        }
        if ok:
            print(f"  OK ({ms} ms)")
            working.append(row)
        else:
            print(f"  FAIL: {err}")
            failed.append({**row, "error": err})

    return working, failed
