# -*- coding: utf-8 -*-

import os

from odoo.tests import TransactionCase, tagged

from odoo.addons.dolares_arg.models.bna_parser import (
    extract_bna_usd_sell,
    is_usd_row_name,
    parse_ar_float,
)

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

BILLETES_HTML = """
<div id="billetes">
    <table class="table cotizacion">
        <thead><tr><th>28/9/2026</th><th>Compra</th><th>Venta</th></tr></thead>
        <tbody>
            <tr><td class="tit">Dolar U.S.A</td><td>1500,00</td><td>1550,00</td></tr>
            <tr><td class="tit">Euro</td><td>1685,00</td><td>1785,00</td></tr>
        </tbody>
    </table>
</div>
<div id="divisas">
    <table class="table cotizacion">
        <thead><tr><th>25/9/2026</th><th>Compra</th><th>Venta</th></tr></thead>
        <tbody>
            <tr><td class="tit">Dolar U.S.A</td><td>1516.5000</td><td>1525.5000</td></tr>
        </tbody>
    </table>
</div>
"""

NO_ID_THOUSANDS_HTML = """
<table class="table cotizacion">
    <thead><tr><th>Fecha</th><th>Compra</th><th>Venta</th></tr></thead>
    <tbody>
        <tr><td>Dólar U.S.A</td><td>1.500,00</td><td>1.550,00</td></tr>
        <tr><td>Dolares Canadienses *</td><td>1.100,00</td><td>1.200,00</td></tr>
    </tbody>
</table>
"""

REGEX_ONLY_HTML = """
<table>
<tr>
<td>Dolar U.S.A</td>
<td>1495,00</td>
<td>1545,00</td>
</tr>
</table>
"""


@tagged("post_install", "-at_install")
class TestBnaParser(TransactionCase):
    def test_parse_ar_float_formats(self):
        self.assertEqual(parse_ar_float("1550,00"), 1550.0)
        self.assertEqual(parse_ar_float("1.550,00"), 1550.0)
        self.assertEqual(parse_ar_float("1516.5000"), 1516.5)
        self.assertEqual(parse_ar_float("  $ 1.550,50 "), 1550.5)

    def test_usd_row_name_filters(self):
        self.assertTrue(is_usd_row_name("Dolar U.S.A"))
        self.assertTrue(is_usd_row_name("Dólar USA"))
        self.assertFalse(is_usd_row_name("Dolares Canadienses *"))
        self.assertFalse(is_usd_row_name("Euro"))

    def test_prefers_billetes_over_divisas(self):
        self.assertEqual(extract_bna_usd_sell(BILLETES_HTML), 1550.0)
        with open(
            os.path.join(_FIXTURE_DIR, "bna_personas.html"), encoding="utf-8"
        ) as fixture:
            self.assertEqual(extract_bna_usd_sell(fixture.read()), 1550.0)

    def test_thousands_separator_without_billetes_id(self):
        self.assertEqual(extract_bna_usd_sell(NO_ID_THOUSANDS_HTML), 1550.0)

    def test_regex_fallback(self):
        self.assertEqual(extract_bna_usd_sell(REGEX_ONLY_HTML), 1545.0)
