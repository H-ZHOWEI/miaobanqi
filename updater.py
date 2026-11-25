# -*- coding: utf-8 -*-
"""
喵伴启 - 版本更新模块
规范：清单多镜像并发 + 时间戳破缓存 / 1字节 Range 探测 / 5 路分段下载
      / MZ + size + SHA256 三重校验 / 程序内替换 / PyInstaller 安全重启
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

from core import (APP_EN, APP_NAME, APP_VERSION, DEFAULT_MANIFEST_URLS,
                  parse_version, version_gt)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 " + APP_EN + "/" + APP_VERSION

LogFn = Callable[[str, str], None]
ProgressFn = Callable[[float, str], None]


def _request(url: str, timeout: int = 10, headers: Optional[dict] = None):
    h = {"User-Agent": UA, "Cache-Control": "no-cache", "Pragma": "no-cache"}
    if headers:
        h.update(headers)
    return urllib.request.Request(url, headers=h)


def _add_nocache(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_t={int(time.time() * 1000)}"


def _dedup(seq):
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# 清单检查
# ---------------------------------------------------------------------------
def _fetch_manifest(url: str, log: LogFn) -> Optional[Tuple[str, dict]]:
    try:
        with urllib.request.urlopen(_request(_add_nocache(url), 10), timeout=10) as r:
            raw = r.read(131072)
        if not raw.lstrip().startswith(b"{"):
            return None
        data = json.loads(raw.decode("utf-8-sig", "replace"))
        if not isinstance(data, dict) or not data.get("version") or not data.get("download_url"):
            return None
        return url, data
    except Exception as exc:
        log("调试", f"更新源不可用 {url}: {type(exc).__name__}")
        return None


def check_updates(urls: List[str], log: LogFn = lambda *_: None,
                  skipped: str = "") -> Optional[dict]:
    """并发拉取全部更新源，按版本号取最高；已是最新或已跳过返回 None"""
    urls = _dedup(list(urls or []) + list(DEFAULT_MANIFEST_URLS))
    found: List[Tuple[str, dict]] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as pool:
        futures = [pool.submit(_fetch_manifest, u, log) for u in urls]
        for f in as_completed(futures):
            try:
                res = f.result()
                if res:
                    found.append(res)
            except Exception:
                pass

    if not found:
        log("警告", "所有更新源均无法读取清单")
        return None

    # 版本最高者优先；同版本记录命中来源
    best_url, best = max(found, key=lambda x: parse_version(str(x[1].get("version", "0"))))
    best = dict(best)
    best["_source"] = best_url
    remote = str(best.get("version", "0"))
    if not version_gt(remote, APP_VERSION):
        log("信息", f"已是最新版本 v{APP_VERSION}（线上 v{remote}，来源：{best_url}）")
        return None
    if skipped and skipped.lstrip("vV") == remote.lstrip("vV"):
        log("信息", f"已跳过版本 v{remote}")
        return None
    log("信息", f"发现新版本 v{remote}，来源：{best_url}")
    return best


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
def build_mirrors(url: str) -> List[str]:
    url = url.strip()
    mirrors = [url]
    if "raw.githubusercontent.com" in url:
        tail = url.split("raw.githubusercontent.com/", 1)[1]
        gh = "https://github.com/" + tail.replace("/raw/", "/raw/")
        mirrors += [
            "https://ghproxy.net/https://raw.githubusercontent.com/" + tail,
            "https://gh-proxy.com/https://raw.githubusercontent.com/" + tail,
            gh,
        ]
    else:
        mirrors += ["https://ghproxy.net/" + url, "https://gh-proxy.com/" + url, url]
    return _dedup(mirrors)


class _MirrorRotator:
    def __init__(self, urls: List[str]):
        self.urls = urls or [""]
        self._i = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            u = self.urls[self._i % len(self.urls)]
            self._i += 1
            return u

    def reset(self) -> None:
        with self._lock:
            self._i = 0


def _probe_size(urls: List[str], log: LogFn) -> Optional[Tuple[str, int]]:
    for u in urls:
        for _ in range(2):
            try:
                req = _request(u, 12, {"Range": "bytes=0-0"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr:
                        return u, int(cr.rsplit("/", 1)[1])
                    cl = r.headers.get("Content-Length")
                    if cl and int(cl) > 1024:
                        return u, int(cl)
                    r.read(2)
            except Exception:
                pass
    log("错误", "无法探测更新文件大小（所有镜像均失败）")
    return None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _is_valid_exe(path: str) -> bool:
    try:
        if os.path.getsize(path) < 1024:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except Exception:
        return False


class UpdateDownloader(threading.Thread):
    """1 字节 Range 探测 → 5 路并发分段 → 独立重试/续传 → 镜像轮换 → 三重校验"""

    def __init__(self, manifest: dict, progress: ProgressFn,
                 log: LogFn, finished: Callable[[Optional[str], str], None],
                 workdir: Optional[str] = None):
        super().__init__(name="MiaoBanQi-Download", daemon=True)
        self.manifest = manifest
        self.progress = progress
        self.log = log
        self.finished = finished
        self.workdir = workdir or os.path.join(tempfile.gettempdir(), APP_EN + "-update")
        self._stop = threading.Event()
        self._parts_progress: Dict[int, int] = {}
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:
        target = None
        err = "未知错误"
        try:
            os.makedirs(self.workdir, exist_ok=True)
            # 清理上次残留（超过 1 天的分片）
            for fn in os.listdir(self.workdir):
                p = os.path.join(self.workdir, fn)
                try:
                    if os.path.isfile(p) and time.time() - os.path.getmtime(p) > 86400:
                        os.remove(p)
                except Exception:
                    pass

            mirrors = build_mirrors(str(self.manifest.get("download_url", "")))
            rot = _MirrorRotator(mirrors)
            probed = _probe_size(mirrors, self.log)
            if not probed:
                self.finished(None, "无法连接更新服务器")
                return
            base_url, total = probed
            expected_size = int(self.manifest.get("size") or total)
            if expected_size > 0 and abs(total - expected_size) > 1024:
                self.log("警告", f"清单大小 {expected_size} 与服务器 {total} 不一致，以服务器为准")
            total = max(total, expected_size)
            self.log("信息", f"更新包大小 {total / 1024 / 1024:.1f} MB，开始分段下载")

            n = 5 if total > 2 * 1024 * 1024 else 1
            chunk = (total + n - 1) // n
            ranges = [(i * chunk, min(total - 1, (i + 1) * chunk - 1)) for i in range(n)]
            ranges = [(s, e) for s, e in ranges if s <= e]
            self._parts_progress = {i: 0 for i in range(len(ranges))}

            # 若已有完整同版本文件则直接复用
            final_name = f"{APP_EN}-v{str(self.manifest.get('version')).lstrip('vV')}.exe"
            final_path = os.path.join(self.workdir, final_name)
            sha_expected = str(self.manifest.get("sha256") or "").strip().lower()
            if _is_valid_exe(final_path) and os.path.getsize(final_path) == total:
                if not sha_expected or _sha256(final_path) == sha_expected:
                    self.progress(1.0, "复用已下载文件")
                    self.finished(final_path, "")
                    return

            part_paths = [os.path.join(self.workdir, f"{final_name}.part{i}") for i in range(len(ranges))]

            def worker(i: int, start: int, end: int) -> bool:
                need = end - start + 1
                part = part_paths[i]
                done = 0
                try:
                    if os.path.isfile(part):
                        done = min(os.path.getsize(part), need)
                except Exception:
                    done = 0
                if done == need:
                    self._parts_progress[i] = need
                    return True

                for attempt in range(8):
                    if self._stop.is_set():
                        return False
                    url = rot.next()
                    try:
                        resume = start + done
                        headers = {"Range": f"bytes={resume}-{end}"}
                        req = _request(_add_nocache(url), 30, headers)
                        with urllib.request.urlopen(req, timeout=30) as r:
                            if r.status != 206:
                                raise RuntimeError("镜像不支持分段续传")
                            mode = "ab" if done else "wb"
                            with open(part, mode) as f:
                                while not self._stop.is_set():
                                    buf = r.read(65536)
                                    if not buf:
                                        break
                                    f.write(buf)
                                    done += len(buf)
                                    self._parts_progress[i] = done
                                    self._report(total)
                            if done >= need:
                                self._parts_progress[i] = need
                                self._report(total)
                                return True
                            raise RuntimeError(f"分片未完成 {done}/{need}")
                    except Exception as exc:
                        self.log("调试", f"分片{i} 第{attempt + 1}次失败: {type(exc).__name__}")
                        try:
                            if os.path.isfile(part):
                                cur = os.path.getsize(part)
                                if cur > need:
                                    os.remove(part)
                                    done = 0
                                else:
                                    done = cur
                        except Exception:
                            done = 0
                        self._parts_progress[i] = done
                return False

            ok = {}
            with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
                futs = {pool.submit(worker, i, s, e): i for i, (s, e) in enumerate(ranges)}
                for f in as_completed(futs):
                    ok[futs[f]] = bool(f.result())

            if not all(ok.values()):
                failed = [i for i, v in ok.items() if not v]
                self.finished(None, f"下载失败：分片 {', '.join(str(x + 1) for x in failed)} 未成功，请重试")
                return

            # 合并
            self.progress(0.99, "正在合并校验")
            with open(final_path, "wb") as out:
                for part in part_paths:
                    with open(part, "rb") as f:
                        while True:
                            b = f.read(1024 * 1024)
                            if not b:
                                break
                            out.write(b)

            # 三重校验
            if not _is_valid_exe(final_path):
                raise RuntimeError("文件不是有效的 Windows 程序（MZ 头校验失败）")
            actual_size = os.path.getsize(final_path)
            if actual_size != total:
                raise RuntimeError(f"文件大小不符：期望 {total}，实际 {actual_size}")
            if sha_expected and _sha256(final_path) != sha_expected:
                raise RuntimeError("SHA256 校验失败，文件可能已损坏或被篡改")

            for part in part_paths:
                try:
                    os.remove(part)
                except Exception:
                    pass
            self.progress(1.0, "下载完成")
            target = final_path
        except Exception as exc:
            err = str(exc) or type(exc).__name__
        self.finished(target, "" if target else err)

    def _report(self, total: int) -> None:
        done = sum(self._parts_progress.values())
        ratio = min(0.98, done / max(1, total))
        mb = done / 1024 / 1024
        self.progress(ratio, f"已下载 {mb:.1f} MB")


# ---------------------------------------------------------------------------
# 安装 / 重启
# ---------------------------------------------------------------------------
def validate_new_exe(path: str, manifest: dict) -> Tuple[bool, str]:
    if not os.path.isfile(path):
        return False, "找不到下载文件"
    if not _is_valid_exe(path):
        return False, "MZ 头校验失败，不是有效的 exe"
    size = int(manifest.get("size") or 0)
    if size > 0 and os.path.getsize(path) != size:
        return False, f"大小校验失败：期望 {size}，实际 {os.path.getsize(path)}"
    sha = str(manifest.get("sha256") or "").strip().lower()
    if sha and _sha256(path) != sha:
        return False, "SHA256 校验失败"
    return True, ""


def install_update(new_exe: str, log: LogFn = lambda *_: None) -> Tuple[bool, str]:
    """程序内替换：旧 exe 改名 .bak → 写入新文件 → 校验；失败自动还原，绝不退出"""
    if not getattr(sys, "frozen", False):
        log("提示", "当前为开发环境，已跳过程序替换（仅下载不覆盖）")
        return True, "开发模式"
    try:
        old = os.path.abspath(sys.executable)
        bak = old + ".bak"
        if os.path.exists(bak):
            try:
                os.remove(bak)
            except Exception:
                bak = old + ".bak." + str(int(time.time()))
        os.rename(old, bak)
        log("信息", "旧版本已重命名为 .bak")
        try:
            shutil.copy2(new_exe, old)
            if not _is_valid_exe(old):
                raise RuntimeError("写入后 MZ 校验失败")
            expected = os.path.getsize(new_exe)
            if os.path.getsize(old) != expected:
                raise RuntimeError("写入后大小校验失败")
            # 隔壁留一份新版副本，方便用户手动双击
            companion = os.path.join(os.path.dirname(old), f"{APP_EN}_新版.exe")
            try:
                shutil.copy2(new_exe, companion)
            except Exception:
                pass
            log("信息", "新版本已安装，软件不会自动关闭")
            return True, companion
        except Exception as exc:
            if os.path.exists(bak):
                if os.path.exists(old):
                    try:
                        os.remove(old)
                    except Exception:
                        pass
                os.rename(bak, old)
            raise RuntimeError(f"替换失败已还原：{exc}")
    except Exception as exc:
        log("错误", str(exc))
        return False, str(exc)


def restart_app() -> None:
    """PyInstaller 安全重启：剔除 _MEI 环境变量，避免复用临时目录导致互删"""
    if not getattr(sys, "frozen", False):
        return
    env = os.environ.copy()
    for key in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
                "_PYI_PARENT_PROCESS_LEVEL", "_PYI_APPLICATION_HOME_DIR"):
        env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        subprocess.Popen([sys.executable] + sys.argv[1:], env=env,
                         cwd=os.path.dirname(sys.executable), close_fds=True)
    except Exception:
        pass


def cleanup_stale_mei(log: LogFn = lambda *_: None) -> None:
    """启动时清理历史残留 _MEI 目录（可能堆积到 GB 级）"""
    if not getattr(sys, "frozen", False):
        return
    current = os.path.abspath(getattr(sys, "_MEIPASS", "") or "")
    try:
        names = os.listdir(tempfile.gettempdir())
    except Exception:
        return
    removed = 0
    for name in names:
        if not name.startswith("_MEI"):
            continue
        p = os.path.join(tempfile.gettempdir(), name)
        if os.path.abspath(p) == current:
            continue
        try:
            if time.time() - os.path.getmtime(p) > 7200:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except Exception:
            pass
    if removed:
        log("信息", f"已清理 {removed} 个历史更新残留目录")
