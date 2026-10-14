"""One small launcher; every command uses the current Python environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
COMMANDS = {
    "index": "scripts.index",
    "query": "scripts.query",
    "check": "scripts.check_mcp",
    "eval": "scripts.eval",
    "test": "pytest",
    "agent": "scripts.agent",
    "demo": "scripts.demo_server",
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="在项目目录中使用当前 Python 运行命令；无参数仅显示帮助。",
        epilog="各命令后可透传原模块参数，例如 query --help、demo --check。demo 是固定数据协议示例。",
    )
    parser.add_argument("command", choices=COMMANDS, help="index 建索引 / query 检索 / check 正式MCP检查 / eval 评测 / test 测试 / agent 问答 / demo 固定样例")
    if not arguments or arguments[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    # Parse only the command: flags such as query --help belong to its module.
    command = parser.parse_args(arguments[:1]).command
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", COMMANDS[command], *arguments[1:]],
            cwd=ROOT,
            env=environment,
            check=False,
        )
    except KeyboardInterrupt:
        return 130
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
