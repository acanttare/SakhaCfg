"""Парсинг URI → outbound для Xray."""

from __future__ import annotations

import base64
import json
import urllib.parse


def parse_uri(uri: str) -> dict | None:
    scheme = uri.split("://", 1)[0].lower()
    parsers = {
        "vless": _vless,
        "vmess": _vmess,
        "trojan": _trojan,
        "ss": _shadowsocks,
    }
    fn = parsers.get(scheme)
    return fn(uri) if fn else None


def protocol(uri: str) -> str:
    return uri.split("://", 1)[0].lower()


def display_name(uri: str) -> str:
    if "#" in uri:
        return urllib.parse.unquote(uri.rsplit("#", 1)[1])
    return urllib.parse.urlparse(uri).hostname or "unknown"


def _vless(uri: str) -> dict:
    p = urllib.parse.urlparse(uri)
    q = dict(urllib.parse.parse_qsl(p.query))
    return {
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": p.hostname,
                "port": p.port or 443,
                "users": [{
                    "id": p.username,
                    "encryption": q.get("encryption", "none"),
                    "flow": q.get("flow", ""),
                }],
            }],
        },
        "streamSettings": _stream(q),
    }


def _vmess(uri: str) -> dict:
    body = uri.split("://", 1)[1]
    pad = (-len(body)) % 4
    data = json.loads(base64.b64decode(body + "=" * pad))
    net = data.get("net", "tcp")
    stream: dict = {"network": net}
    if net == "ws":
        stream["wsSettings"] = {
            "path": data.get("path", "/"),
            "headers": {"Host": data.get("host", "")},
        }
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": data.get("path", "")}
    if data.get("tls"):
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": data.get("sni") or data.get("host") or data.get("add"),
        }
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


def _trojan(uri: str) -> dict:
    p = urllib.parse.urlparse(uri)
    q = dict(urllib.parse.parse_qsl(p.query))
    stream = _stream({**q, "security": "tls"})
    stream["security"] = "tls"
    stream["tlsSettings"] = {"serverName": q.get("sni") or p.hostname}
    return {
        "protocol": "trojan",
        "settings": {
            "servers": [{
                "address": p.hostname,
                "port": p.port or 443,
                "password": urllib.parse.unquote(p.username or ""),
            }],
        },
        "streamSettings": stream,
    }


def _shadowsocks(uri: str) -> dict:
    p = urllib.parse.urlparse(uri)
    if "@" in p.netloc:
        user, hostport = p.netloc.rsplit("@", 1)
        method, password = base64.b64decode(user + "==").decode().split(":", 1)
        host, port_str = hostport.rsplit(":", 1)
    else:
        decoded = base64.b64decode(p.netloc + "==").decode()
        method, rest = decoded.split(":", 1)
        password, host = rest.rsplit("@", 1)
        port_str = str(p.port or 443)
    return {
        "protocol": "shadowsocks",
        "settings": {
            "servers": [{
                "address": host,
                "port": int(port_str),
                "method": method,
                "password": password,
            }],
        },
        "streamSettings": {"network": "tcp"},
    }


def _stream(q: dict) -> dict:
    net = q.get("type", "tcp")
    stream: dict = {"network": net}
    sec = q.get("security", "none")
    if sec and sec != "none":
        stream["security"] = sec
        tls: dict = {"serverName": q.get("sni") or q.get("host", "")}
        if sec == "reality":
            tls["fingerprint"] = q.get("fp", "chrome")
            # For Xray, reality settings must be sibling of tlsSettings.
            stream["realitySettings"] = {
                "publicKey": q.get("pbk", ""),
                "shortId": q.get("sid", ""),
                "spiderX": q.get("spx", ""),
            }
        stream["tlsSettings"] = tls
    if net == "ws":
        stream["wsSettings"] = {
            "path": q.get("path", "/"),
            "headers": {"Host": q.get("host", "")},
        }
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": q.get("serviceName", "")}
    elif net in ("xhttp", "splithttp"):
        stream["network"] = "xhttp"
        stream["xhttpSettings"] = {"path": q.get("path", "/")}
    return stream


def xray_config(outbound: dict, socks_port: int) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": {"udp": True},
        }],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
    }
