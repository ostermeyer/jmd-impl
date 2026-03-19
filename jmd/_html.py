"""JMD HTML Renderer (v0.2)."""

from __future__ import annotations

from typing import Any, cast

from ._parser import JMDParser
from ._tokenizer import tokenize
from ._scalars import serialize_scalar


_HTML_STYLE = """\
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif;
         background: #f0f4f8; padding: 2rem; color: #1a202c; margin: 0; }
  .jmd-doc  { max-width: 860px; margin: 0 auto; }
  .jmd-root-label { font-size: 1.5rem; font-weight: 700; color: #2d3748;
    border-bottom: 3px solid #4299e1; padding-bottom: .5rem;
    margin-bottom: 1.2rem; }
  .jmd-object { background: #fff; border: 1px solid #e2e8f0;
    border-radius: 8px; padding: .8rem 1.2rem; }
  .jmd-field { display: flex; align-items: flex-start; gap: .6rem;
    padding: .35rem 0; border-bottom: 1px solid #f7fafc; }
  .jmd-field:last-child { border-bottom: none; }
  .jmd-key  { font-weight: 600; color: #3182ce; min-width: 150px;
    font-size: .875rem; padding-top: .1rem; flex-shrink: 0; }
  .jmd-value { color: #2d3748; font-size: .875rem; flex: 1; }
  .jmd-string  { color: #276749; }
  .jmd-number  { color: #c05621; }
  .jmd-boolean { color: #6b46c1; font-style: italic; }
  .jmd-null    { color: #a0aec0; font-style: italic; }
  .jmd-nested { border-left: 3px solid #bee3f8; padding-left: .8rem;
    margin-top: .2rem; }
  .jmd-array  { display: flex; flex-direction: column; gap: .15rem;
    margin-top: .1rem; }
  .jmd-array-scalar::before { content: "— "; color: #cbd5e0; }
  .jmd-array-scalar { font-size: .875rem; }
  .jmd-array-obj { border-left: 3px solid #c6f6d5; padding-left: .8rem;
    margin: .2rem 0; }
  .jmd-array-nested { border-left: 3px solid #fefcbf; padding-left: .8rem;
    margin: .2rem 0; }
</style>"""


def _esc(s: str) -> str:
    """Escape a string for safe HTML embedding."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _scalar_html(raw: str) -> str:
    """Render a scalar value as an HTML span with type-specific styling."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return f'<span class="jmd-string">{_esc(raw)}</span>'
    if raw == "null":
        return '<span class="jmd-null">null</span>'
    if raw in ("true", "false"):
        return f'<span class="jmd-boolean">{raw}</span>'
    try:
        float(raw)
        return f'<span class="jmd-number">{_esc(raw)}</span>'
    except ValueError:
        pass
    return f'<span class="jmd-string">{_esc(raw)}</span>'


class JMDHTMLRenderer:
    """Renders JMD v0.2 documents as styled HTML."""

    def render(self, source: str) -> str:
        """Render a JMD document as a complete HTML page."""
        lines = tokenize(source)
        if not lines:
            return ""

        first = lines[0]
        if first.heading_depth == 1 and first.content == "[]":
            label = "Array"
            is_array = True
        elif first.heading_depth == 1:
            label = first.content
            is_array = False
        else:
            label = "Document"
            is_array = False

        data = JMDParser().parse(source)
        if is_array:
            body = self._render_array(data)
        else:
            body = self._render_object(data)

        return (
            f"<!DOCTYPE html>\n<html lang=\"en\">\n<head>"
            f"<meta charset=\"UTF-8\">"
            f"<title>JMD – {_esc(label)}</title>\n"
            f"{_HTML_STYLE}\n</head>\n<body>\n<div class=\"jmd-doc\">\n"
            f"<div class=\"jmd-root-label\">{_esc(label)}</div>\n"
            f"<div class=\"jmd-object\">\n{body}\n</div>\n"
            f"</div>\n</body>\n</html>"
        )

    def _render_value(self, value: Any) -> str:
        if isinstance(value, dict):
            return f'<div class="jmd-nested">{self._render_object(cast(dict[str, Any], value))}</div>'
        if isinstance(value, list):
            return f'<div class="jmd-array">{self._render_array(cast(list[Any], value))}</div>'
        return _scalar_html(serialize_scalar(value))

    def _render_object(self, obj: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, value in obj.items():
            val_html = self._render_value(value)
            parts.append(
                f'<div class="jmd-field">'
                f'<span class="jmd-key">{_esc(key)}</span>'
                f'<span class="jmd-value">{val_html}</span></div>'
            )
        return "\n".join(parts)

    def _render_array(self, lst: list[Any]) -> str:
        parts: list[str] = []
        for item in lst:
            if isinstance(item, dict):
                inner = self._render_object(cast(dict[str, Any], item))
                parts.append(f'<div class="jmd-array-obj">{inner}</div>')
            elif isinstance(item, list):
                inner = self._render_array(cast(list[Any], item))
                parts.append(
                    f'<div class="jmd-array-nested">{inner}</div>'
                )
            else:
                parts.append(
                    f'<div class="jmd-array-scalar">'
                    f'{_scalar_html(serialize_scalar(item))}</div>'
                )
        return "\n".join(parts)
