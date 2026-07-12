"""Inline a review pack into the renderer template -> a self-contained HTML file.

Spike-stage seed of the eventual ``brnrd review`` render/serve step. Reads
``template.html`` (generic over any pack) and substitutes the pack JSON
into its embedded ``<script>`` tag, so the output opens in any browser
with no server and no dependencies.

    python -m brr.diffense.render <pack.json> [out.html]

With no ``out.html`` the HTML is written to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PLACEHOLDER = "__DIFFENSE_PACK__"
_TEMPLATE = Path(__file__).with_name("template.html")


def render(pack: dict, template: str | None = None) -> str:
    """Return self-contained HTML for *pack* using the renderer template."""
    tmpl = template if template is not None else _TEMPLATE.read_text(encoding="utf-8")
    if _PLACEHOLDER not in tmpl:
        raise ValueError(f"template is missing the {_PLACEHOLDER} placeholder")
    # ``</`` -> ``<\/`` keeps the HTML parser from ending the <script> early;
    # JSON.parse reads ``\/`` back as ``/``, so the embedded pack stays valid.
    payload = json.dumps(pack, ensure_ascii=False).replace("</", "<\\/")
    return tmpl.replace(_PLACEHOLDER, payload)


def render_shell(template: str | None = None) -> str:
    """Return the renderer shell; the browser loads ``?pack=<url>`` itself."""
    tmpl = template if template is not None else _TEMPLATE.read_text(encoding="utf-8")
    if _PLACEHOLDER not in tmpl:
        raise ValueError(f"template is missing the {_PLACEHOLDER} placeholder")
    return tmpl.replace(_PLACEHOLDER, "")


def render_file(pack_path: Path, out_path: Path | None) -> str:
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    html = render(pack)
    if out_path is not None:
        Path(out_path).write_text(html, encoding="utf-8")
    return html


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    pack_path = Path(argv[0])
    out_path = Path(argv[1]) if len(argv) > 1 else None
    html = render_file(pack_path, out_path)
    if out_path is None:
        sys.stdout.write(html)
    else:
        print(f"wrote {out_path} ({len(html):,} bytes) from {pack_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
