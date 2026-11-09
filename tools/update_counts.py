#!/usr/bin/env python3
"""Refresh the catalog counts shown on the Browse screen. Run before a release.

The counts ship with the app so nobody's copy has to ask the Archive for them. This makes
one request at a time, with a pause between each.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openshelf import catalog  # noqa: E402

TARGET = Path(catalog.__file__).parent / "assets" / "catalog_counts.json"


def main() -> None:
    counts = {}
    for query in catalog.browse_count_queries():
        request = urllib.request.Request(catalog.search_url(query, rows=0), headers={"User-Agent": "OpenShelf"})
        with urllib.request.urlopen(request, timeout=60) as reply:
            counts[catalog.count_key(query)] = json.load(reply)["response"]["numFound"]
        time.sleep(1)
    TARGET.write_text(json.dumps(counts, indent=0, sort_keys=True) + "\n")
    print(f"Wrote {len(counts)} counts to {TARGET}")


if __name__ == "__main__":
    main()
