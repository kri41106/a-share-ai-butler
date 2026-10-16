"""在移除开发环境 DLL/Python 路径后运行实际 EXE，不等同干净虚拟机。"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--qt-platform", choices=("windows", "offscreen"), default="windows")
    args = parser.parse_args()
    exe, report = Path(args.exe).resolve(), Path(args.report).resolve()
    if os.name != "nt":
        raise SystemExit("此检查仅适用于 Windows。")
    if list((exe.parent / "_internal").rglob("icuuc.dll")):
        raise SystemExit("拒绝验收：包内含可能覆盖系统 ICU 的 icuuc.dll。")
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        env.pop(name, None)
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = str(Path(system_root) / "System32") + os.pathsep + system_root
    env["BUTLER_STARTUP_QPA"] = args.qt_platform
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="butler-cwd-", dir=report.parent) as cwd:
        completed = subprocess.run([str(exe), "--startup-check", str(report)], cwd=cwd, env=env,
                                   timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode or not report.is_file():
        raise SystemExit("实际 EXE 启动失败，请查看启动报告或 Windows 错误信息。")
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["ok"] and result["frozen"] and result["qt_platform"] == args.qt_platform, result
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
