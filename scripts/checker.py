"""Параллельная проверка конфигов через Xray."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue

import requests

from parser import display_name, parse_uri, protocol, xray_config

TEST_URL = os.environ.get("TEST_URL", "https://www.google.com/generate_204")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "8"))
TCP_TIMEOUT = float(os.environ.get("TCP_TIMEOUT", "3"))
XRAY_START_WAIT = float(os.environ.get("XRAY_START_WAIT", "2.0"))
WORKERS = int(os.environ.get("PARALLEL_WORKERS", "30"))
BASE_PORT = int(os.environ.get("BASE_PORT", "10808"))
XRAY_BIN = Path(os.environ["XRAY_BIN"])

_print_lock = threading.Lock()
_done = 0


def _port_ready(host: str, port: int) -> bool:
    deadline = time.time() + XRAY_START_WAIT
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _tcp_alive(uri: str) -> bool:
    try:
        p = urllib.parse.urlparse(uri)
        host = p.hostname
        port = p.port or (443 if p.scheme != "ss" else 8388)
        if not host:
            return True
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT):
            return True
    except OSError:
        return False


def probe(uri: str, socks_port: int) -> tuple[bool, str, int]:
    if not _tcp_alive(uri):
        return False, "host unreachable", 0

    outbound = parse_uri(uri)
    if not outbound:
        return False, "unsupported protocol", 0

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "xray.json"
        cfg.write_text(json.dumps(xray_config(outbound, socks_port)), encoding="utf-8")

        proc = subprocess.Popen(
            [str(XRAY_BIN), "run", "-c", str(cfg)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        t0 = time.time()
        try:
            if not _port_ready("127.0.0.1", socks_port):
                return False, "xray start timeout", 0

            proxy = f"socks5h://127.0.0.1:{socks_port}"
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
            return False, str(exc)[:120], int((time.time() - t0) * 1000)
        finally:
            proc.kill()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


def _check_one(item: dict, socks_port: int, total: int) -> tuple[dict | None, dict | None]:
    global _done
    uri = item["config"]
    name = display_name(uri)
    proto = protocol(uri)

    ok, err, ms = probe(uri, socks_port)

    row = {
        "config": uri,
        "protocol": proto,
        "name": name,
        "source": item["source"],
        "latency_ms": ms,
    }

    with _print_lock:
        _done += 1
        n = _done
        if ok:
            print(f"[{n}/{total}] OK  {proto} {name} ({ms} ms)", flush=True)
            return row, None
        print(f"[{n}/{total}] FAIL {proto} {name} — {err}", flush=True)
        return None, {**row, "error": err}


def verify_all(items: list[dict]) -> tuple[list[dict], list[dict]]:
    total = len(items)
    workers = min(WORKERS, total) if total else 1
    port_pool: Queue[int] = Queue()
    for i in range(workers):
        port_pool.put(BASE_PORT + i)

    print(f"Параллельных потоков: {workers}, таймаут: {TEST_TIMEOUT}s\n", flush=True)

    working: list[dict] = []
    failed: list[dict] = []

    def task(item: dict) -> tuple[dict | None, dict | None]:
        port = port_pool.get()
        try:
            return _check_one(item, port, total)
        finally:
            port_pool.put(port)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(task, item) for item in items]
        for fut in as_completed(futures):
            ok_row, fail_row = fut.result()
            if ok_row:
                working.append(ok_row)
            if fail_row:
                failed.append(fail_row)

    working.sort(key=lambda x: x["latency_ms"])
    return working, failed
