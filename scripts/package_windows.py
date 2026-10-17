"""复用 one-dir 产物，打包程序/声明；不会读取用户持仓目录。Apache-2.0。
Modified 2026-10-17: package local-risk MVP upgrade instructions.
"""
from importlib.metadata import distribution, PackageNotFoundError
from pathlib import Path
import argparse
import shutil
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = ROOT / "dist" / "StockWidget"
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / "a-share-ai-butler"
        stage.mkdir()
        shutil.copy2(source / "StockWidget.exe", stage)
        shutil.copytree(source / "_internal", stage / "_internal")
        for name in ("README.md", "LICENSE", "NOTICE", "THIRD_PARTY.md", "PROGRESS.md", "WINDOWS_STARTUP.md", "UPGRADE_MULTI.md", "UPGRADE_3A.md"):
            shutil.copy2(ROOT / name, stage)
        (stage / "STARTUP_FIX.txt").write_text(
            "第三阶段3A本地风险分析与提醒版 stage3a-risk1\n"
            "保留 winfix1 的 ICU 修复；请完整解压到新目录，不与旧包混合。\n"
            "升级与提醒使用见 UPGRADE_3A.md；启动诊断见 WINDOWS_STARTUP.md。不执行真实交易。\n", encoding="utf-8-sig")
        shutil.copytree(ROOT / "third-party-licenses", stage / "third-party-licenses")
        licenses = stage / "third-party-licenses"
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
        if python_license.is_file():
            shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
        for package in ("PySide6_Essentials", "shiboken6", "requests", "urllib3", "idna",
                        "charset-normalizer", "certifi", "pyinstaller", "pywin32-ctypes"):
            try:
                dist = distribution(package)
            except PackageNotFoundError:
                raise SystemExit("缺少打包依赖的许可元数据：" + package)
            destination = licenses / package
            destination.mkdir()
            (destination / "METADATA.txt").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
            for file in dist.files or []:
                if ".dist-info" in str(file) and any(term in str(file).lower() for term in ("license", "copying", "notice")):
                    original = Path(dist.locate_file(file))
                    if original.is_file():
                        relative = Path(*file.parts[1:])
                        copied = destination / relative
                        copied.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, copied)
        (stage / "使用说明.txt").write_text(
            "完整解压后双击 StockWidget.exe。\n这是第三阶段3A个人验收包，尚待用户 Windows 实机验收。\n"
            "本地示例持仓不等于券商账户持仓；软件不执行真实交易。\n"
            "关闭面板后通过托盘“我的持仓”恢复；托盘“退出”结束程序。\n"
            "持仓文件：%APPDATA%/StockWidget/portfolio/positions.sqlite3。请勿上传此文件。\n",
            encoding="utf-8-sig")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in stage.rglob("*"):
                if not file.is_file():
                    continue
                if file.suffix.lower() in {".db", ".sqlite3", ".csv"} or file.name.endswith(("-wal", "-shm")):
                    raise SystemExit("拒绝打包可能的个人数据文件。")
                archive.write(file, file.relative_to(stage.parent))
    print("Windows 验收包已生成（构建成功不等于实机运行通过）。")


if __name__ == "__main__":
    main()
