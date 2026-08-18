#!/usr/bin/env python3
"""Bygger et selvstændigt HTML-dashboard ved at støbe data/posts.json ind i skabelonen.

    python3 build_dashboard.py [posts.json] [output.html]

Output er én fil uden eksterne afhængigheder — kan åbnes lokalt, mailes eller
lægges op som artifact.
"""
import json
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "data/posts.json")
out = Path(sys.argv[2] if len(sys.argv) > 2 else "some-dashboard.html")
tpl = Path(__file__).with_name("dashboard_template.html")

doc = json.loads(src.read_text(encoding="utf-8"))
html = tpl.read_text(encoding="utf-8")

marker = "/*__DATA__*/"
if marker not in html:
    sys.exit("Skabelonen mangler /*__DATA__*/-markøren")

# JSON'en indsættes som JS-literal. </script> escapes, så den ikke lukker script-tagget.
payload = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
head, _, tail = html.partition(marker)
tail = tail[tail.index("{"): ]                     # fjern fallback-objektet
depth, i = 0, 0
for i, ch in enumerate(tail):                      # find slutningen af fallback-objektet
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            break
html = head + payload + tail[i + 1:]

out.write_text(html, encoding="utf-8")
n = len(doc.get("posts", []))
print(f"Skrev {out} — {n} opslag, {out.stat().st_size/1024:.0f} KB, kilde: {doc.get('source')}")
