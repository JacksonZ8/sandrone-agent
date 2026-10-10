"""macOS 窗口只读辅助：列出前台应用与窗口、读取空闲时长、截取单个窗口。

不安装 pyobjc：窗口信息用 osascript 的 JXA 调 CoreGraphics / AppKit 读取（约 0.2 秒），
截图用系统自带的 screencapture -l，只截指定窗口而不是整个屏幕。这里没有任何键鼠控制。
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

CAPTURE_MAX_SIDE = 1280
MIN_WINDOW_SIDE = 160  # 更小的多半是菜单栏小窗、浮层或工具条

_SNAPSHOT_JXA = r"""
ObjC.import('AppKit');
ObjC.import('CoreGraphics');
function run() {
  const front = $.NSWorkspace.sharedWorkspace.frontmostApplication;
  const opts = $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements;
  const list = ObjC.castRefToObject($.CGWindowListCopyWindowInfo(opts, $.kCGNullWindowID));
  const windows = [];
  for (let i = 0; i < list.count; i++) {
    const w = ObjC.deepUnwrap(list.objectAtIndex(i));
    if (w.kCGWindowLayer !== 0 || !w.kCGWindowBounds) continue;
    windows.push({id: w.kCGWindowNumber, app: w.kCGWindowOwnerName || '', pid: w.kCGWindowOwnerPID,
                  w: w.kCGWindowBounds.Width, h: w.kCGWindowBounds.Height});
  }
  return JSON.stringify({
    app: front.isNil() ? '' : ObjC.unwrap(front.localizedName),
    pid: front.isNil() ? 0 : front.processIdentifier,
    windows: windows,
  });
}
"""

_APPS_JXA = r"""
ObjC.import('AppKit');
function run() {
  const apps = $.NSWorkspace.sharedWorkspace.runningApplications;
  const names = [];
  for (let i = 0; i < apps.count; i++) {
    const app = apps.objectAtIndex(i);
    if (app.activationPolicy === $.NSApplicationActivationPolicyRegular) names.push(ObjC.unwrap(app.localizedName));
  }
  return JSON.stringify(names);
}
"""


@dataclass(frozen=True)
class WindowInfo:
    id: int
    app: str
    pid: int
    width: int
    height: int


@dataclass(frozen=True)
class DesktopSnapshot:
    app: str  # 前台应用名（与窗口所属应用名一致，如 VS Code 显示为 Code）
    pid: int
    windows: tuple[WindowInfo, ...]  # 从前到后

    def front_window(self, apps: set[str] | None = None) -> WindowInfo | None:
        """前台应用最前面的窗口；给出 apps 时改为名单内最前面的窗口。"""
        for window in self.windows:
            if window.width < MIN_WINDOW_SIDE or window.height < MIN_WINDOW_SIDE:
                continue
            if apps is None and window.pid == self.pid:
                return window
            if apps is not None and window.app in apps:
                return window
        return None


def _jxa(script: str, timeout: float = 10) -> str:
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"读取窗口信息失败：{(result.stderr or '').strip()[:200]}")
    return result.stdout.strip()


def parse_snapshot(raw: str) -> DesktopSnapshot:
    data = json.loads(raw)
    windows = tuple(
        WindowInfo(
            id=int(item["id"]),
            app=str(item.get("app") or ""),
            pid=int(item.get("pid") or 0),
            width=int(item.get("w") or 0),
            height=int(item.get("h") or 0),
        )
        for item in data.get("windows") or []
    )
    return DesktopSnapshot(app=str(data.get("app") or ""), pid=int(data.get("pid") or 0), windows=windows)


def parse_idle_seconds(ioreg_output: str) -> float | None:
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', ioreg_output)
    return int(match.group(1)) / 1e9 if match else None


def dhash(image: Image.Image) -> int:
    """64 位差值哈希：窗口内容基本没变时哈希距离很小，用来省掉重复的视觉调用。"""
    pixels = list(image.convert("L").resize((9, 8), Image.BILINEAR).get_flattened_data())
    value = 0
    for row in range(8):
        for column in range(8):
            if pixels[row * 9 + column] > pixels[row * 9 + column + 1]:
                value |= 1 << (row * 8 + column)
    return value


def hash_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


@dataclass(frozen=True)
class WindowImage:
    jpeg: bytes
    width: int
    height: int
    fingerprint: int


def encode_window_image(raw: bytes) -> WindowImage:
    with Image.open(io.BytesIO(raw)) as image:
        image = image.convert("RGB")
        image.thumbnail((CAPTURE_MAX_SIDE, CAPTURE_MAX_SIDE), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=80)
        return WindowImage(buffer.getvalue(), image.width, image.height, dhash(image))


class MacDesktop:
    """真实桌面。测试用同样接口的假对象替换。"""

    def snapshot(self) -> DesktopSnapshot:
        return parse_snapshot(_jxa(_SNAPSHOT_JXA))

    def running_apps(self) -> list[str]:
        names = json.loads(_jxa(_APPS_JXA))
        return sorted({str(name) for name in names if name})

    def idle_seconds(self) -> float | None:
        try:
            result = subprocess.run(
                ["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            return None
        return parse_idle_seconds(result.stdout)

    def capture(self, window_id: int) -> WindowImage:
        """只截这一个窗口（含被遮挡部分，不含阴影）；临时 PNG 读入内存后立即删除。"""
        with tempfile.TemporaryDirectory(prefix="sandrone_window_") as tmp:
            raw = Path(tmp) / "window.png"
            result = subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-o", f"-l{int(window_id)}", str(raw)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0 or not raw.is_file():
                tail = (result.stderr or "").strip()
                raise RuntimeError(f"窗口截图失败：{tail or '未生成截图'}。请确认已授予「屏幕录制」权限。")
            return encode_window_image(raw.read_bytes())

    def notify(self, title: str, text: str) -> None:
        """系统通知；文字作为参数传入，不拼进脚本。"""
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run",
                title,
                text,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )


MAC_DESKTOP = MacDesktop()
