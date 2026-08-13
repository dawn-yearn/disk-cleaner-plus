#!/usr/bin/env python3
"""
生成静态 HTML 报告（无清理按钮）。

Usage:
    python build_report.py <analysis.json> [output.html]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "report_template.html")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/Desktop/disk-cleaner-report.html")

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()

    blob = json.dumps(data, ensure_ascii=False)
    html = tpl.replace("__REPORT_DATA__", blob).replace("__DELETE_CONFIG__", "null")

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"静态报告已生成: {out}")


if __name__ == "__main__":
    main()
