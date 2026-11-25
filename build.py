# -*- coding: utf-8 -*-
"""
喵伴启 - 打包脚本
用法： python build.py
产物： dist/MiaoBanQi.exe + 自动回填 version.json 的 sha256 / size
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date

from core import APP_EN, APP_NAME, APP_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
EXE = os.path.join(DIST, APP_EN + ".exe")
ICON = os.path.join(HERE, "icon.ico")
VERSION_JSON = os.path.join(HERE, "version.json")


def make_icon():
    try:
        from PIL import Image
        sys.path.insert(0, HERE)
        from ui import make_icon_image
        img = make_icon_image()
        if img:
            img.save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
            print("图标已生成:", ICON)
    except Exception as exc:
        print("图标生成失败（不影响打包）:", exc)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build():
    make_icon()
    os.makedirs(DIST, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", APP_EN,
        "--distpath", DIST,
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", HERE,
    ]
    if os.path.isfile(ICON):
        cmd += ["--icon", ICON]
    cmd.append(os.path.join(HERE, "main.py"))
    print("执行打包:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=HERE)

    if not os.path.isfile(EXE):
        raise RuntimeError("打包结束但未找到 exe")

    size = os.path.getsize(EXE)
    if size >= 100 * 1024 * 1024:
        print(f"警告：exe 大小 {size / 1024 / 1024:.1f}MB 超过 GitHub 100MB 单文件限制！")

    data = {
        "version": APP_VERSION,
        "download_url": f"https://raw.githubusercontent.com/H-ZHOWEI/miaobanqi/main/dist/{APP_EN}.exe",
        "notes": "新增程序路径从运行中选择（自动取完整路径）；全部下拉框中文化。",
        "sha256": sha256(EXE),
        "size": size,
        "pub_date": date.today().isoformat(),
        "mandatory": False,
    }
    with open(VERSION_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"完成：{EXE}  {size / 1024 / 1024:.2f} MB")
    print("version.json 已回填 sha256 / size")
    print("提示：如 GitHub 仓库名不是 miaobanqi，请修改 version.json 的 download_url")


if __name__ == "__main__":
    build()
