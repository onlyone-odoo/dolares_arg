# -*- coding: utf-8 -*-
# models/currency_rate.py

from odoo import models
from odoo.exceptions import UserError

import requests
import logging
from datetime import date
from bs4 import BeautifulSoup
import unicodedata

_logger = logging.getLogger(__name__)


class CurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    def fetch_arg_dollars(self, company_id=None):
        """
        Fetch and update Argentine dollar rates (Oficial, Blue, Bolsa, CCL, etc.)
        and BNA rate (which will override the standard USD currency).
        """
        today = date.today()
        _logger.info(
            f"Fetch Start: Starting fetch_arg_dollars for {today} (company_id: {company_id or 'Global'})"
        )

        config = self.env["ir.config_parameter"].sudo()

        # Mapping casa → currency code (except BNA which uses USN as trigger)
        casa_to_currency = {
            "oficial": "USO",
            "blue": "USB",
            "bolsa": "USL",
            "contadoconliqui": "USC",
            "mayorista": "USM",
            "cripto": "USCR",
            "tarjeta": "UST",
        }

        # === 1. API: dolarapi.com (fast and reliable) ===
        api_enabled = any(
            config.get_param(f"dolares_arg.enable_{casa}", "False") == "True"
            for casa in casa_to_currency
        )
        rates_data = {}

        if api_enabled:
            url = "https://dolarapi.com/v1/dolares"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                rates_data = {item["casa"]: item for item in response.json()}
                _logger.info("API Success: Retrieved rates from dolarapi.com")
            except Exception as e:
                _logger.error(f"API Fetch Error: {str(e)}")
                # Continue with BNA if enabled, don't stop completely

        # Process each enabled API rate
        for casa, code in casa_to_currency.items():
            if config.get_param(f"dolares_arg.enable_{casa}", "False") != "True":
                continue

            data = rates_data.get(casa)
            if not data:
                _logger.warning(
                    f"{casa.capitalize()} Data Missing: No data in API response"
                )
                continue

            try:
                buy = float(data.get("compra", 0))
                sell = float(data.get("venta", 0))

                if sell == 0:
                    raise ValueError("Sell price is zero")

                if casa == "blue":
                    if buy == 0:
                        raise ValueError("Blue dollar must have a valid buy price")
                    rate_value = (buy + sell) / 2
                else:
                    rate_value = sell

                _logger.info(f"{casa.capitalize()} Rate: Using rate {rate_value:.4f}")
                self._update_rate(code, today, 1.0 / rate_value, company_id=company_id)

            except Exception as e:
                _logger.error(f"{casa.capitalize()} Processing Error: {str(e)}")

        # === 2. BNA fallback / override (updates standard USD) ===
        if config.get_param("dolares_arg.enable_bna", "False") == "True":
            _logger.info("BNA Process Start: Fetching from Banco Nación")
            try:
                bna_url = "https://www.bna.com.ar/Personas"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36"
                }
                response = requests.get(bna_url, timeout=15, headers=headers)
                response.raise_for_status()

                soup = BeautifulSoup(response.content, "html.parser")
                table = soup.find(id="billetes")
                if not table:
                    raise UserError("BNA table not found (id='billetes')")

                found = False
                for tr in table.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) < 3:
                        continue

                    row_name = (
                        unicodedata.normalize("NFKD", tds[0].get_text(strip=True))
                        .encode("ascii", "ignore")
                        .decode("utf-8")
                        .lower()
                    )

                    if "dolar u.s.a" in row_name or "dólar u.s.a" in row_name:
                        value_str = tds[2].get_text(strip=True)  # Venta
                        value = float(value_str.replace(",", "."))
                        if value <= 0:
                            raise ValueError("Invalid BNA sell value")

                        _logger.info(
                            f"BNA Success: Found USD rate {value} → updating standard USD currency"
                        )
                        # USN is just a trigger → will be redirected to USD
                        self._update_rate(
                            "USN", today, 1.0 / value, company_id=company_id
                        )
                        found = True
                        break

                if not found:
                    raise UserError("Dolar U.S.A row not found in BNA table")

            except Exception as e:
                _logger.error(f"BNA Exception: {str(e)}")
                if isinstance(e, UserError):
                    raise

    def _update_rate(self, currency_code, date_rate, rate, company_id=None):
        """
        Update or create currency rate.
        Special case: currency_code == 'USN' → updates the standard USD currency instead of creating USN.
        """
        _logger.info(f"Update Rate Start: {currency_code} → {rate:.8f} on {date_rate}")

        # ------------------------------------------------------------------
        # Special handling: BNA → override standard USD
        # ------------------------------------------------------------------
        if currency_code == "USN":
            currency = self.env["res.currency"].search([("name", "=", "USD")], limit=1)
            if not currency:
                _logger.error("Standard currency USD not found in database")
                return
            _logger.info("USN trigger detected → updating official USD currency")
        else:
            # Normal flow: custom Argentine dollars
            currency = self.env["res.currency"].search(
                [("name", "=", currency_code)], limit=1
            )

            if not currency:
                currency_names = {
                    "USO": "Dolar Oficial",
                    "USB": "United States Dollar Blue",
                    "USL": "Dolar Bolsa (MEP)",
                    "USC": "Dolar Contado con Liqui (CCL)",
                    "USM": "Dolar Mayorista",
                    "USCR": "Dolar Cripto",
                    "UST": "Dolar Tarjeta",
                }
                currency = self.env["res.currency"].create(
                    {
                        "name": currency_code,
                        "symbol": currency_code,
                        "full_name": currency_names.get(currency_code, currency_code),
                        "active": True,
                    }
                )
                _logger.info(f"Currency Created: {currency_code}")

        # ------------------------------------------------------------------
        # Search or create rate
        # ------------------------------------------------------------------
        domain = [
            ("currency_id", "=", currency.id),
            ("name", "=", date_rate),
        ]
        if company_id:
            domain.append(("company_id", "=", company_id))
        else:
            domain.append(("company_id", "=", False))

        existing = self.search(domain, limit=1)

        vals = {
            "currency_id": currency.id,
            "name": date_rate,
            "rate": rate,
            "company_id": company_id or False,
        }

        if existing:
            existing.write(vals)
            _logger.info(f"Rate Updated: {currency.name} → {rate:.8f} on {date_rate}")
        else:
            self.create(vals)
            _logger.info(f"Rate Created: {currency.name} → {rate:.8f} on {date_rate}")
