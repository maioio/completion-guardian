import argparse


def main():
    p = argparse.ArgumentParser(prog="tool")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init")
    sub.add_parser("run")
    p.parse_args()
    return 0
