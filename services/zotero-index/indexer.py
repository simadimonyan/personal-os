#!/usr/bin/env python3
"""
Zotero vector index — background refresher under the pos supervisor.

Every ZOTERO_REFRESH_SEC (default 6h) runs an *incremental* refresh of the
Zotero vector library: re-embeds only new/changed annotations, re-chunks only
when the PDF set changed, rebuilds the book graph if anything moved, and writes
tools/zotero/status.json (read by mission-control).

Each refresh runs as a subprocess so the ~1GB embedding model is released
between cycles — the daemon itself stays tiny.
"""
import os
import sys
import time
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VECTORIZE = REPO / "tools" / "zotero" / "vectorize.py"
INTERVAL = int(os.environ.get("ZOTERO_REFRESH_SEC", str(6 * 3600)))


def cycle():
    env = dict(os.environ)
    # homebrew tesseract on PATH for OCR of scanned PDFs
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    r = subprocess.run([sys.executable, str(VECTORIZE), "refresh"], env=env)
    return r.returncode


if __name__ == "__main__":
    print(f"[zotero-index] start · interval={INTERVAL}s · {VECTORIZE}", flush=True)
    while True:
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            code = cycle()
            print(f"[zotero-index] {t} refresh done (exit {code})", flush=True)
        except Exception as e:  # never let the loop die
            print(f"[zotero-index] {t} error: {e}", flush=True)
        time.sleep(INTERVAL)
