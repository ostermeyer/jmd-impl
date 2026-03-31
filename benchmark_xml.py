"""XML ↔ JMD token-efficiency benchmark.

Compares token counts for the same OOXML data represented as:
  - Raw XML (minified)
  - Pretty-printed XML (standard human-readable form)
  - JSON via Parker-convention mapping (xmltodict default)
  - JMD over XML (this library)

Tokenizer: cl100k_base (GPT-4 / Claude family approximation).
Energy estimate: ~0.6 Wh per 1M tokens (H100 inference, conservative).
"""

from __future__ import annotations

import json

import tiktoken
import xmltodict
from lxml import etree

from jmd.xml import xml_to_jmd

# ---------------------------------------------------------------------------
# Benchmark corpus
# ---------------------------------------------------------------------------

# Four documents of increasing complexity / real-world representativeness.

SAMPLES: list[tuple[str, str]] = [
    (
        "Minimal paragraph",
        (
            '<w:document'
            ' xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p>"
            "<w:r><w:t>Hello World</w:t></w:r>"
            "</w:p>"
            "</w:body>"
            "</w:document>"
        ),
    ),
    (
        "Companion spec example",
        (
            '<w:document'
            ' xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships">'
            "<w:body>"
            '<w:p w:rsidR="00B84FB8">'
            "<w:pPr>"
            '<w:pStyle w:val="Normal"/>'
            '<w:jc w:val="center"/>'
            "</w:pPr>"
            "<w:r>"
            "<w:rPr>"
            "<w:b/>"
            '<w:color w:val="FF0000"/>'
            "</w:rPr>"
            "<w:t>Hello</w:t>"
            "</w:r>"
            "<w:r>"
            '<w:t xml:space="preserve"> World</w:t>'
            "</w:r>"
            "</w:p>"
            "<w:tbl/>"
            '<w:p w:rsidR="00B84FB9"/>'
            "</w:body>"
            "</w:document>"
        ),
    ),
    (
        "Table with rows",
        (
            '<w:document'
            ' xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            "<w:body>"
            "<w:tbl>"
            '<w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>'
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>Alpha</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>Beta</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>99</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "</w:tbl>"
            "</w:body>"
            "</w:document>"
        ),
    ),
    (
        "DrawingML chart series",
        (
            '<c:chartSpace'
            ' xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<c:chart>"
            "<c:plotArea>"
            "<c:barChart>"
            '<c:barDir val="col"/>'
            '<c:grouping val="clustered"/>'
            "<c:ser>"
            "<c:idx val='0'/>"
            "<c:order val='0'/>"
            "<c:tx><c:strRef><c:f>Sheet1!$B$1</c:f></c:strRef></c:tx>"
            "<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$4</c:f></c:strRef></c:cat>"
            "<c:val>"
            "<c:numRef>"
            "<c:f>Sheet1!$B$2:$B$4</c:f>"
            "<c:numCache>"
            '<c:formatCode>General</c:formatCode>'
            '<c:ptCount val="3"/>'
            '<c:pt idx="0"><c:v>10</c:v></c:pt>'
            '<c:pt idx="1"><c:v>20</c:v></c:pt>'
            '<c:pt idx="2"><c:v>30</c:v></c:pt>'
            "</c:numCache>"
            "</c:numRef>"
            "</c:val>"
            "</c:ser>"
            "</c:barChart>"
            "</c:plotArea>"
            "</c:chart>"
            "</c:chartSpace>"
        ),
    ),
]

# ---------------------------------------------------------------------------
# Representation generators
# ---------------------------------------------------------------------------


def to_xml_minified(xml: str) -> str:
    """Return minified XML (lxml canonical form, no whitespace)."""
    root = etree.fromstring(xml.encode())
    return etree.tostring(root, encoding="unicode")


def to_xml_pretty(xml: str) -> str:
    """Return pretty-printed XML with 2-space indent."""
    root = etree.fromstring(xml.encode())
    etree.indent(root, space="  ")
    return etree.tostring(root, encoding="unicode")


def to_json_parker(xml: str) -> str:
    """Return JSON using xmltodict (de-facto Parker convention)."""
    data = xmltodict.parse(xml)
    return json.dumps(data, indent=2, ensure_ascii=False)


def to_jmd(xml: str) -> str:
    """Return JMD over XML."""
    return xml_to_jmd(xml)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def count_tokens(text: str, enc: tiktoken.Encoding) -> int:
    """Count tokens in text using the given encoder."""
    return len(enc.encode(text))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

ENERGY_PER_M_TOKENS_WH = 0.6  # Wh per 1M tokens, H100 conservative estimate


def _bar(ratio: float, width: int = 30) -> str:
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def run_benchmark() -> None:
    """Run the benchmark and print a report."""
    enc = tiktoken.get_encoding("cl100k_base")

    representations = [
        ("XML minified", to_xml_minified),
        ("XML pretty", to_xml_pretty),
        ("JSON (Parker)", to_json_parker),
        ("JMD over XML", to_jmd),
    ]

    print("=" * 72)
    print("JMD over XML — Token Efficiency Benchmark")
    print(f"Tokenizer: cl100k_base  |  Energy: {ENERGY_PER_M_TOKENS_WH} Wh/M tokens")
    print("=" * 72)

    totals: dict[str, int] = {name: 0 for name, _ in representations}

    for sample_name, xml in SAMPLES:
        print(f"\n{'─' * 72}")
        print(f"  {sample_name}")
        print(f"{'─' * 72}")

        counts: dict[str, int] = {}
        for name, fn in representations:
            text = fn(xml)
            counts[name] = count_tokens(text, enc)
            totals[name] += counts[name]

        baseline = counts["XML pretty"]
        for name, n in counts.items():
            pct = n / baseline * 100
            bar = _bar(n / baseline)
            marker = " ◀ baseline" if name == "XML pretty" else f"  {pct:5.1f}%"
            print(f"  {name:<18} {n:5d} tokens  {bar}{marker}")

    print(f"\n{'═' * 72}")
    print("  TOTALS (all samples combined)")
    print(f"{'─' * 72}")
    baseline_total = totals["XML pretty"]
    for name, n in totals.items():
        pct = n / baseline_total * 100
        saved = baseline_total - n
        energy_saved_uh = saved / 1_000_000 * ENERGY_PER_M_TOKENS_WH * 1_000_000
        bar = _bar(n / baseline_total)
        marker = " ◀ baseline" if name == "XML pretty" else f"  {pct:5.1f}%"
        print(f"  {name:<18} {n:5d} tokens  {bar}{marker}")

    print()
    jmd_total = totals["JMD over XML"]
    xml_pretty_total = totals["XML pretty"]
    xml_mini_total = totals["XML minified"]
    json_total = totals["JSON (Parker)"]

    savings_vs_pretty = (1 - jmd_total / xml_pretty_total) * 100
    savings_vs_mini = (1 - jmd_total / xml_mini_total) * 100
    savings_vs_json = (1 - jmd_total / json_total) * 100

    print(f"  JMD vs XML pretty   : -{savings_vs_pretty:5.1f}% tokens")
    print(f"  JMD vs XML minified : {'-' if savings_vs_mini >= 0 else '+'}"
          f"{abs(savings_vs_mini):4.1f}% tokens")
    print(f"  JMD vs JSON Parker  : {'-' if savings_vs_json >= 0 else '+'}"
          f"{abs(savings_vs_json):4.1f}% tokens")

    # Energy implication at 1B requests/day
    requests_per_day = 1_000_000_000
    saved_per_req = xml_pretty_total / len(SAMPLES) - jmd_total / len(SAMPLES)
    saved_per_day_tokens = saved_per_req * requests_per_day
    saved_kwh_per_day = saved_per_day_tokens / 1_000_000 * ENERGY_PER_M_TOKENS_WH / 1000
    saved_mwh_per_year = saved_kwh_per_day * 365 / 1000

    print()
    print(f"  Energy implication (1B requests/day, JMD vs XML pretty):")
    print(f"    Saved per request : ~{saved_per_req:,.0f} tokens")
    print(f"    Saved per day     : ~{saved_kwh_per_day:,.0f} kWh")
    print(f"    Saved per year    : ~{saved_mwh_per_year:,.0f} MWh")
    print("=" * 72)


if __name__ == "__main__":
    run_benchmark()
