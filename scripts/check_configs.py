"""Проверка конфигов через xray-core, сохранение только рабочих."""

from __future__ import annotations

import json
import platform
import socket
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from fetch_configs import fetch_all_configs

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JSON = ROOT / "output" / "working.json"
OUTPUT_TXT = ROOT / "output" / "working.txt"
XRAY_DIR = ROOT / ".xray"
TEST_URL = "https://www.google.com/generate_204"
TEST_TIMEOUT = 12
SOCKS_PORT = 10808


def get_xray_binary() -> Path:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        name = "xray"
        asset_key = "linux-64" if machine in ("x86_64", "amd64") else "linux-arm64-v8a"
    elif system == "windows":
        name = "xray.exe"
        asset_key = "win-64"
    elif system == "darwin":
        name = "xray"
        asset_key = "macos-64" if machine == "x86_64" else "macos-arm64-v8a"
    else:
        raise RuntimeError(f"Неподдерживаемая ОС: {system}")

    binary = XRAY_DIR / name
    if binary.exists():
        return binary

    XRAY_DIR.mkdir(parents=True, exist_ok=True)
    release = requests.get(
        "https://api.github.com/repos/XTLS/Xray-core/releases/latest",
        timeout=30,
    ).json()
    asset_map = {
        "linux-64": "Xray-linux-64.zip",
        "linux-arm64-v8a": "Xray-linux-arm64-v8a.zip",
        "win-64": "Xray-win-64.zip",
        "macos-64": "Xray-macos-64.zip",
        "macos-arm64-v8a": "Xray-macos-arm64-v8a.zip",
    }
    filename = asset_map[asset_key]
    url = next(a["browser_download_url"] for a in release["assets"] if a["name"] == filename)

    zip_path = XRAY_DIR / filename
    print(f"Скачиваю Xray...")
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        zip_path.write_bytes(resp.content)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(XRAY_DIR)

    if system != "windows":
        binary.chmod(0o755)
    return binary


def parse_config_uri(uri: str) -> dict | None:
    scheme = uri.split("://", 1)[0].lower()
    parsers = {
        "vless": _parse_vless,
        "vmess": _parse_vmess,
        "trojan": _parse_trojan,
        "ss": _parse_ss,
    }
    parser = parsers.get(scheme)
    return parser(uri) if parser else None


def _parse_vless(uri: str) -> dict:
    parsed = urllib.parse.urlparse(uri)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    return {
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": parsed.hostname,
                "port": parsed.port or 443,
                "users": [{
                    "id": parsed.username,
                    "encryption": params.get("encryption", "none"),
                    "flow": params.get("flow", ""),
                }],
            }],
        },
        "streamSettings": _build_stream(params),
    }


def _parse_vmess(uri: str) -> dict:
    import base64 as b64

    payload = uri.split("://", 1)[1]
    padding = (-len(payload)) % 4
    data = json.loads(b64.b64decode(payload + "=" * padding))
    network = data.get("net", "tcp")
    stream: dict = {"network": network}
    if network == "ws":
        stream["wsSettings"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}}
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": data.get("path", "")}
    if data.get("tls"):
        stream["security"] = "tls"
        stream["tlsSettings"] = {"serverName": data.get("sni") or data.get("host") or data.get("add")}
    return {
        "protocol": "vmess",
        "settings": {
            "vnext": [{
                "address": data["add"],
                "port": int(data["port"]),
                "users": [{
                    "id": data["id"],
                    "alterId": int(data.get("aid", 0)),
                    "security": data.get("scy", "auto"),
                }],
            }],
        },
        "streamSettings": stream,
    }


def _parse_trojan(uri: str) -> dict:
    parsed = urllib.parse.urlparse(uri)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    stream = _build_stream({**params, "security": "tls"})
    stream["security"] = "tls"
    stream["tlsSettings"] = {"serverName": params.get("sni") or parsed.hostname}
    return {
        "protocol": "trojan",
        "settings": {"servers": [{
            "address": parsed.hostname,
            "port": parsed.port or 443,
            "password": urllib.parse.unquote(parsed.username or ""),
        }]},
        "streamSettings": stream,
    }


def _parse_ss(uri: str) -> dict:
    import base64 as b64

    parsed = urllib.parse.urlparse(uri)
    if "@" in parsed.netloc:
        userinfo, hostport = parsed.netloc.rsplit("@", 1)
        method, password = b64.b64decode(userinfo + "==").decode().split(":", 1)
        host, port = hostport.split(":")
    else:
        decoded = b64.b64decode(parsed.netloc + "==").decode()
        method, rest = decoded.split(":", 1)
        password, host = rest.rsplit("@", 1)
        port = str(parsed.port or 443)
    return {
        "protocol": "shadowsocks",
        "settings": {"servers": [{
            "address": host,
            "port": int(port),
            "method": method,
            "password": password,
        }]},
        "streamSettings": {"network": "tcp"},
    }


def _build_stream(params: dict) -> dict:
    network = params.get("type", "tcp")
    stream: dict = {"network": network}
    security = params.get("security", "none")
    if security and security != "none":
        stream["security"] = security
        tls: dict = {"serverName": params.get("sni") or params.get("host", "")}
        if security == "reality":
            tls["fingerprint"] = params.get("fp", "chrome")
            tls["realitySettings"] = {
                "publicKey": params.get("pbk", ""),
                "shortId": params.get("sid", ""),
                "spiderX": params.get("spx", ""),
            }
        stream["tlsSettings"] = tls
    if network == "ws":
        stream["wsSettings"] = {
            "path": params.get("path", "/"),
            "headers": {"Host": params.get("host", "")},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": params.get("serviceName", "")}
    elif network in ("xhttp", "splithttp"):
        stream["network"] = "xhttp"
        stream["xhttpSettings"] = {"path": params.get("path", "/")}
    return stream


def build_xray_config(outbound: dict, socks_port: int) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "port": socks_port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"udp": True},
        }],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
    }


def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def test_config(uri: str, xray_bin: Path, socks_port: int) -> tuple[bool, str, float]:
    outbound = parse_config_uri(uri)
    if not outbound:
        return False, "unsupported protocol", 0.0

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(build_xray_config(outbound, socks_port)), encoding="utf-8")

        proc = subprocess.Popen(
            [str(xray_bin), "run", "-c", str(config_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        start = time.time()
        try:
            for _ in range(30):
                if is_port_open("127.0.0.1", socks_port):
                    break
                time.sleep(0.2)
            else:
                return False, "xray did not start", time.time() - start

            proxies = {
                "http": f"socks5h://127.0.0.1:{socks_port}",
                "https": f"socks5h://127.0.0.1:{socks_port}",
            }
            resp = requests.get(TEST_URL, proxies=proxies, timeout=TEST_TIMEOUT)
            ok = resp.status_code in (200, 204)
            return ok, "" if ok else f"HTTP {resp.status_code}", time.time() - start
        except Exception as exc:
            return False, str(exc), time.time() - start
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def get_config_name(uri: str) -> str:
    if "#" in uri:
        return urllib.parse.unquote(uri.rsplit("#", 1)[1])
    return urllib.parse.urlparse(uri).hostname or "unknown"


def run_check() -> None:
    xray_bin = get_xray_binary()
    all_configs = fetch_all_configs()
    print(f"Конфигов для проверки: {len(all_configs)}\n")

    working: list[dict] = []

    for i, item in enumerate(all_configs, 1):
        uri = item["config"]
        name = get_config_name(uri)
        proto = uri.split("://", 1)[0]
        print(f"[{i}/{len(all_configs)}] {proto} {name}...", end=" ", flush=True)

        ok, error, latency = test_config(uri, xray_bin, SOCKS_PORT)
        if ok:
            ms = int(latency * 1000)
            print(f"OK ({ms} ms)")
            working.append({
                "config": uri,
                "protocol": proto,
                "name": name,
                "source": item["source"],
                "latency_ms": ms,
            })
        else:
            print(f"FAIL ({error})")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_checked": len(all_configs),
        "total_working": len(working),
        "working": working,
    }
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_TXT.write_text("\n".join(w["config"] for w in working) + ("\n" if working else ""), encoding="utf-8")

    print(f"\nГотово: {len(working)}/{len(all_configs)} рабочих")
    print(f"  → {OUTPUT_TXT}")
    print(f"  → {OUTPUT_JSON}")


if __name__ == "__main__":
    run_check()
