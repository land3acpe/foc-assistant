"""FOC-Assistant CLI 启动入口"""
import sys
import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8")

from agent import main

if __name__ == "__main__":
    main()
