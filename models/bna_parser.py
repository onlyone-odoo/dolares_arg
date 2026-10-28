# -*- coding: utf-8 -*-

"""Parse Banco Nación USD bill sell rate from the public website HTML."""

import re
import unicodedata

from bs4 import BeautifulSoup

BNA_PERSONAS_URL = "https://www.bna.com.ar/Personas"
BNA_HOME_URL = "https://www.bna.com.ar/"

BNA_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

# Billetes table usually comes first; Divisas uses a different decimal format.
_USD_ROW_MARKERS = (
    "dolaru.s.a",
    "dolarusa",
    "dolareeuu",
    "dolarestadounidense",
)
_USD_ROW_EXACT = ("dolar", "dolares")
_USD_EXCLUDE = ("canadien", "austral", "hongkong", "neozeland")

# First group is compra, second is venta after the USD row label.
_USD_ROW_REGEX = re.compile(
    r"d[oó]lar\s*u\.?\s*s\.?\s*a\.?\s*</t[dh]>\s*"
    r"<t[dh][^>]*>\s*([^<]+)\s*</t[dh]>\s*"
    r"<t[dh][^>]*>\s*([^<]+)\s*</t[dh]>",
    re.IGNORECASE | re.DOTALL,
)


def normalize_ascii(text):
    """Return lowercase ASCII without diacritics."""
    if not text:
        return ""
    return (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "ignore")
        .decode("utf-8")
        .lower()
        .strip()
    )


def parse_ar_float(value_str):
    """Parse amounts as published by BNA (1550,00 / 1.550,00 / 1516.5000)."""
    if value_str is None:
        raise ValueError("Empty BNA amount")
    raw = (
        str(value_str)
        .strip()
        .replace("\xa0", "")
        .replace(" ", "")
        .replace("$", "")
    )
    raw = re.sub(r"[^0-9,.\-]", "", raw)
    if not raw or raw in {".", ",", "-", "-.", "-,"}:
        raise ValueError("Invalid BNA amount: %s" % value_str)
    if "," in raw:
        # Argentine grouping: 1.550,00 or 1550,00
        raw = raw.replace(".", "").replace(",", ".")
    value = float(raw)
    if value <= 0:
        raise ValueError("BNA amount must be positive: %s" % value_str)
    return value


def is_usd_row_name(name):
    """Return True if the table row labels US dollar (not CAD/AUD)."""
    compact = normalize_ascii(name).replace(" ", "")
    if not compact:
        return False
    if any(token in compact for token in _USD_EXCLUDE):
        return False
    if any(marker in compact for marker in _USD_ROW_MARKERS):
        return True
    return compact in _USD_ROW_EXACT


def _venta_cell_index(table):
    """Return the Venta column index from thead, defaulting to 2."""
    headers = table.find_all("th") if table else []
    for index, header in enumerate(headers):
        if "venta" in normalize_ascii(header.get_text(" ", strip=True)):
            return index
    return 2


def _extract_usd_sell_from_node(node):
    """Return USD sell from tables under *node*, or None."""
    tables = []
    if getattr(node, "name", None) == "table":
        tables.append(node)
    tables.extend(node.find_all("table"))
    seen = set()
    for table in tables:
        marker = id(table)
        if marker in seen:
            continue
        seen.add(marker)
        venta_idx = _venta_cell_index(table)
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            if not is_usd_row_name(cells[0].get_text(" ", strip=True)):
                continue
            if venta_idx >= len(cells):
                continue
            return parse_ar_float(cells[venta_idx].get_text(" ", strip=True))
    return None


def extract_bna_usd_sell(html):
    """Extract the BNA USD bill sell rate from Personas HTML.

    Prefers ``id="billetes"`` (banknotes). Falls back to any cotizacion table
    and finally to a regex over the raw markup.

    Args:
        html (str | bytes): Raw HTML of the BNA page.

    Returns:
        float: Sell rate in ARS per USD.

    Raises:
        ValueError: If no plausible USD sell rate is found.
    """
    if not html:
        raise ValueError("Empty BNA HTML")
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    billetes = soup.find(id="billetes")
    if billetes:
        candidates.append(billetes)
    candidates.extend(soup.find_all(class_=re.compile(r"\bcotizacion\b")))
    candidates.append(soup)

    seen = set()
    for node in candidates:
        marker = id(node)
        if marker in seen:
            continue
        seen.add(marker)
        value = _extract_usd_sell_from_node(node)
        if value is not None:
            _assert_plausible_usd_ars(value)
            return value

    match = _USD_ROW_REGEX.search(html)
    if match:
        value = parse_ar_float(match.group(2))
        _assert_plausible_usd_ars(value)
        return value

    raise ValueError("Could not parse BNA USD sell rate from HTML")


def _assert_plausible_usd_ars(value):
    """Reject values that cannot be a retail USD/ARS quote."""
    if value < 10 or value > 10000000:
        raise ValueError("Implausible BNA USD sell rate: %s" % value)
