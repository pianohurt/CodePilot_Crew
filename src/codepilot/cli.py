"""命令行入口。通信命令在 S0 的下一个实现节点接入。"""

import argparse

from codepilot import get_version


def main() -> int:
    parser = argparse.ArgumentParser(prog="codepilot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="显示本地安装版本")
    parser.parse_args()
    print(f"codepilot {get_version()}")
    return 0
