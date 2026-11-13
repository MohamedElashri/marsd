"""Self-contained escaped HTML and versioned JSON report exports."""

from html import escape
from typing import Any

from marsd.domain import dumps


def render_html(result: dict[str, Any]) -> str:
    title = f"Marsd {result.get('kind', 'investigation')} report"
    inputs = ", ".join(escape(str(s.get("name", "source"))) for s in result.get("inputs", []))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>{escape(title)}</title><style>body{{font:16px/1.6 system-ui,sans-serif;color:#142b40;background:#f4f7fa;max-width:1000px;margin:48px auto;padding:0 24px}}h1{{font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:24px;border:1px solid #c4d1da}}.note{{border-left:4px solid #d18c38;padding:12px}}@media print{{body{{margin:0}}}}</style></head><body><h1>{escape(title)}</h1><p>{inputs}</p><p><strong>{escape(str(result.get("outcome", "Recorded result")))}</strong></p><p class="note">Inspect coverage, source consistency and identity strength before using these findings. This report contains selected source values in bounded examples. Its timestamp does not guarantee that a live source is unchanged.</p><pre>{escape(dumps(result))}</pre></body></html>"""
