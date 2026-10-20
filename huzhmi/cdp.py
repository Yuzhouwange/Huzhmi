"""极简 CDP 客户端：起一个 headless 浏览器，在页面上下文里执行 JS。

不依赖 selenium/playwright/websocket-client，全部标准库实现：
  - WebSocket 握手 + 帧编解码（客户端掩码）
  - /json/version 拿 browser 级 WS 端点
  - Target.createTarget / attachToTarget(flatten) / Runtime.evaluate
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

_WIN_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def parse_cookie_string(cookie: str) -> list[tuple[str, str]]:
    """`a=1; b=2` → [(a, 1), (b, 2)]，丢掉空名/空值。"""
    pairs: list[tuple[str, str]] = []
    for item in (cookie or "").split(";"):
        name, _, value = item.strip().partition("=")
        name, value = name.strip(), value.strip()
        if name and value:
            pairs.append((name, value))
    return pairs


class CDPError(RuntimeError):
    pass


def find_browser() -> str | None:
    for path in _WIN_CANDIDATES:
        if Path(path).is_file():
            return path
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------
# WebSocket（最小实现，够 CDP 用）
# --------------------------------------------------------------------------


class WebSocket:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        # ws://host:port/path
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        response = self._read_until(b"\r\n\r\n")
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise CDPError(f"WebSocket 握手失败：{response[:120]!r}")
        self._buffer = b""
        self._id = 0

    def _read_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("WebSocket 连接被关闭")
            data += chunk
        head, _, tail = data.partition(marker)
        self._buffer = tail
        return head + marker

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("WebSocket 连接被关闭")
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def send(self, payload: str) -> None:
        data = payload.encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        frame = self._read_frame()
        return frame.decode("utf-8", "replace")

    def _read_frame(self) -> bytes:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            masked = second & 0x80
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                raise CDPError("WebSocket 被对端关闭")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode in (0x1, 0x2):
                return payload
            # 其余（pong 等）忽略

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# 浏览器会话
# --------------------------------------------------------------------------


class Browser:
    """一次启动，复用到进程退出。"""

    def __init__(self, headless: bool = True, keep_profile: bool = False) -> None:
        exe = find_browser()
        if not exe:
            raise CDPError("没找到 Edge / Chrome，无法启动页面桥。")
        self.exe = exe
        self.port = _free_port()
        self.profile = tempfile.mkdtemp(prefix="huzhmi-edge-")
        self.keep_profile = keep_profile
        args = [
            exe,
            "--headless" if headless else "--start-maximized",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--mute-audio",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--window-size=1280,900",
            "about:blank",
        ]
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.ws = WebSocket(self._wait_endpoint(), timeout=60)
        self._msg_id = 0
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._target_id: str | None = None

    # ---------- 基础设施 ----------

    def _wait_endpoint(self, timeout: float = 25.0) -> str:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/version", timeout=2
                ) as resp:
                    data = json.loads(resp.read().decode())
                    return data["webSocketDebuggerUrl"]
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
                time.sleep(0.35)
        raise CDPError(f"浏览器调试端口没起来：{last}")

    def _send(self, method: str, params: dict | None = None,
              session_id: str | None = None) -> int:
        with self._lock:
            self._msg_id += 1
            msg_id = self._msg_id
            payload = {"id": msg_id, "method": method, "params": params or {}}
            if session_id:
                payload["sessionId"] = session_id
            self.ws.send(json.dumps(payload))
        return msg_id

    def call(self, method: str, params: dict | None = None,
             session_id: str | None = None, timeout: float = 60.0) -> dict:
        msg_id = self._send(method, params, session_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = json.loads(self.ws.recv())
            if message.get("id") == msg_id:
                if "error" in message:
                    raise CDPError(f"{method} 失败：{message['error']}")
                return message.get("result", {})
            # 其余事件（Network/Page 等）这里不消费，直接丢弃
        raise CDPError(f"{method} 超时")

    # ---------- 页面 ----------

    def open_page(self, url: str = "about:blank") -> str:
        target = self.call("Target.createTarget", {"url": url})
        self._target_id = target["targetId"]
        attached = self.call(
            "Target.attachToTarget", {"targetId": self._target_id, "flatten": True}
        )
        self._session_id = attached["sessionId"]
        self.call("Runtime.enable", session_id=self._session_id)
        self.call("Page.enable", session_id=self._session_id)
        return self._session_id

    def goto(self, url: str, wait_pages: float = 0.0, state_target: tuple = ("interactive", "complete")) -> None:
        self.call("Page.navigate", {"url": url}, session_id=self._session_id)
        deadline = time.time() + 30
        while time.time() < deadline:
            state = self.eval("document.readyState")
            if state in state_target:
                break
            time.sleep(0.25)
        if wait_pages:
            time.sleep(wait_pages)

    def eval(self, expression: str, await_promise: bool = True,
             timeout: float = 60.0):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
            session_id=self._session_id,
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or str(detail)
            raise CDPError(f"页面脚本报错：{text[:300]}")
        return (result.get("result") or {}).get("value")

    def cookies(self) -> list[dict]:
        # Network.getAllCookies 在部分版本上不存在，Storage.getCookies 是浏览器级的稳定接口
        return self.call("Storage.getCookies").get("cookies", [])

    def set_cookies(self, cookie: str, domain: str = ".douyin.com") -> int:
        """把 `a=1; b=2` 形式保存的 Cookie 写回浏览器，复用已登录状态。

        用浏览器级的 `Storage.setCookies`：`Network.setCookie` 在没 enable
        Network 域时会静默失败，写不进去也不报错。返回写入条数。
        """
        pairs = parse_cookie_string(cookie)
        if not pairs:
            return 0
        cookies = [
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "secure": True,
            }
            for name, value in pairs
        ]
        self.call("Storage.setCookies", {"cookies": cookies})
        return len(cookies)

    def screenshot(self, path: str, full_page: bool = False) -> None:
        params: dict = {"format": "png"}
        if full_page:
            metrics = self.call("Page.getLayoutMetrics", session_id=self._session_id)
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            width = int(size.get("width") or 1280)
            height = int(size.get("height") or 900)
            params.update(
                {
                    "captureBeyondViewport": True,
                    "clip": {
                        "x": 0,
                        "y": 0,
                        "width": width,
                        "height": min(height, 4000),
                        "scale": 1,
                    },
                }
            )
        data = self.call(
            "Page.captureScreenshot", params, session_id=self._session_id, timeout=60
        )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(data["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        if not self.keep_profile:
            shutil.rmtree(self.profile, ignore_errors=True)
