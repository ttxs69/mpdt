"""python3 -m mpdt <子命令> 入口（等价于 python3 -c 'from mpdt.cli import main; ...'）。"""
import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
