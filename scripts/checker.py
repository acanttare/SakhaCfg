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

_raw_test_urls = os.environ.get(
    "TEST_URLS", os.environ.get("TEST_URL", "https://www.google.com/generate_204")
)
TEST_URLS = [u.strip() for u in _raw_test_urls.split(",") if u.strip()]
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "8"))
TCP_TIMEOUT = float(os.environ.get("TCP_TIMEOUT", "3"))
XRAY_START_WAIT = float(os.environ.get("XRAY_START_WAIT", "2.0"))
WORKERS = int(os.environ.get("PARALLEL_WORKERS", "30"))
BASE_PORT = int(os.environ.get("BASE_PORT", "10808"))
RETRY_PER_URL = int(os.environ.get("RETRY_PER_URL", "1"))
VALIDATE_CONFIG = os.environ.get("XRAY_VALIDATE_CONFIG", "0") == "1"
XRAY_BIN = Path(os.environ["XRAY_BIN"])

_print_lock = threading.Lock()
_done = 0


def _compact_err(text: str, limit: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _port_ready(host: str, port: int, proc: subprocess.Popen[str] | None = None) -> bool:
    deadline = time.time() + XRAY_START_WAIT
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
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
    except (OSError, UnicodeError, ValueError):
        # Invalid hostnames (e.g. broken IDNA labels) should be treated
        # as unreachable endpoints, not as fatal checker errors.
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

        if VALIDATE_CONFIG:
            test = subprocess.run(
                [str(XRAY_BIN), "run", "-test", "-c", str(cfg)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            if test.returncode != 0:
                err = _compact_err(test.stderr)
                if err:
                    return False, f"xray config invalid: {err}", 0
                return False, f"xray config invalid (code {test.returncode})", 0

        proc = subprocess.Popen(
            [str(XRAY_BIN), "run", "-c", str(cfg)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        t0 = time.time()
        try:
            if not _port_ready("127.0.0.1", socks_port, proc):
                if proc.poll() is not None:
                    err = _compact_err(proc.stderr.read() if proc.stderr else "")
                    if err:
                        return False, f"xray exited early: {err}", 0
                    return False, "xray exited early", 0
                return False, "xray start timeout", 0

            proxy = f"socks5h://127.0.0.1:{socks_port}"
            last_err = "all test urls failed"
            for test_url in TEST_URLS:
                for _ in range(RETRY_PER_URL + 1):
                    try:
                        resp = requests.get(
                            test_url,
                            proxies={"http": proxy, "https": proxy},
                            timeout=TEST_TIMEOUT,
                        )
                        ms = int((time.time() - t0) * 1000)
                        if resp.status_code in (200, 204):
                            return True, "", ms
                        last_err = f"{test_url} -> HTTP {resp.status_code}"
                    except requests.RequestException as exc:
                        last_err = f"{test_url} -> {_compact_err(str(exc), 120)}"
            return False, last_err, int((time.time() - t0) * 1000)
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
