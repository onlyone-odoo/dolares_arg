# -*- coding: utf-8 -*-

import logging
from datetime import date

import requests

from odoo import models

from .bna_parser import (
    BNA_HOME_URL,
    BNA_PERSONAS_URL,
    BNA_REQUEST_HEADERS,
    extract_bna_usd_sell,
)

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
                value = self._fetch_bna_usd_sell()
                _logger.info(
                    "BNA Success: Found USD sell rate %s → updating standard USD",
                    value,
                )
                # USN is just a trigger → will be redirected to USD
                self._update_rate("USN", today, 1.0 / value, company_id=company_id)
            except Exception:
                # Do not raise: a UserError here would rollback dolarapi updates
                # and mark the cron as failed.
                _logger.exception("BNA Exception: could not update USD from BNA")

    def _fetch_bna_usd_sell(self):
        """Download BNA HTML and return the USD bill sell rate.

        Tries /Personas first, then the homepage if parsing fails.
        """
        last_error = None
        for url in (BNA_PERSONAS_URL, BNA_HOME_URL):
            try:
                response = requests.get(
                    url, timeout=30, headers=BNA_REQUEST_HEADERS
                )
                response.raise_for_status()
                html = response.text or ""
                _logger.info(
                    "BNA Response: %s status %s (%s bytes)",
                    url,
                    response.status_code,
                    len(html),
                )
                return extract_bna_usd_sell(html)
            except Exception as err:
                last_error = err
                _logger.warning("BNA fetch/parse failed for %s: %s", url, err)
        raise last_error or ValueError("BNA USD sell rate not available")

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
