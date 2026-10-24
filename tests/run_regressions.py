"""在仓库隔离目录运行回归，禁止测试连接外部网络。"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    log = (root / args.log).resolve()
    if root / "output" not in log.parents:
        parser.error("日志必须位于工作区 output 目录")
    log.parent.mkdir(parents=True, exist_ok=True)
    scratch = log.parent / "tmp"
    scratch.mkdir(exist_ok=True)
    tempfile.tempdir = str(scratch)
    os.environ["TEMP"] = os.environ["TMP"] = str(scratch)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def local_only(original):
        def connect(sock, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
                raise RuntimeError("回归测试禁止连接外部网络")
            return original(sock, address)
        return connect

    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
    with log.open("w", encoding="utf-8") as stream, patch.object(socket.socket, "connect", local_only(original_connect)), patch.object(socket.socket, "connect_ex", local_only(original_connect_ex)):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    print(log.read_text(encoding="utf-8")[-16000:])
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    sys.exit(main())
