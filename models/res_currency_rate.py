from odoo import models
import requests
import logging
from datetime import date

from .bna_parser import (
    BNA_HOME_URL,
    BNA_PERSONAS_URL,
    BNA_REQUEST_HEADERS,
    extract_bna_usd_sell,
)


class CurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    _logger = logging.getLogger(__name__)

    def fetch_arg_dollars(self, company_id=None):
        """Fetch and update Argentine dollar rates based on configuration."""
        today = date.today()
        self._logger.info(
            f"Fetch Start: Starting fetch_arg_dollars for {today} (company_id: {company_id or 'Global'})"
        )
        config = self.env["ir.config_parameter"].sudo()

        # Mapping of 'casa' to currency codes
        casa_to_currency = {
            "oficial": "USO",
            "blue": "USB",
            "bolsa": "USL",
            "contadoconliqui": "USC",
            "mayorista": "USM",
            "cripto": "USCR",
            "tarjeta": "UST",
        }

        # Fetch all rates from API if any API-based is enabled
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
            except Exception as e:
                self._logger.error(f"API Fetch Error: {str(e)}")
                return  # Stop if API fails, but continue for BNA if enabled

        # Process each enabled API rate
        for casa, code in casa_to_currency.items():
            if config.get_param(f"dolares_arg.enable_{casa}", "False") == "True":
                data = rates_data.get(casa)
                if not data:
                    self._logger.warning(
                        f"{casa.capitalize()} Data Missing: No data found in API response"
                    )
                    continue
                try:
                    buy = float(data.get("compra", 0))
                    sell = float(data.get("venta", 0))
                    if sell == 0:
                        raise ValueError("Invalid sell value")
                    if casa == "blue":
                        if buy == 0:
                            raise ValueError("Invalid buy value for blue")
                        rate_value = (buy + sell) / 2
                    else:
                        rate_value = sell
                    self._logger.info(
                        f"{casa.capitalize()} Rate: Calculated rate_value: {rate_value}"
                    )
                    self._update_rate(
                        code, today, 1.0 / rate_value, company_id=company_id
                    )
                except Exception as e:
                    self._logger.error(
                        f"{casa.capitalize()} Processing Error: {str(e)}"
                    )

        # Process BNA if enabled
        if config.get_param("dolares_arg.enable_bna", "False") == "True":
            self._logger.info("BNA Process Start: Starting BNA fetch for %s", today)
            try:
                value = self._fetch_bna_usd_sell()
                target = "USD" if self._bna_overrides_usd() else "USN"
                self._logger.info(
                    "BNA Success: USD sell %s → updating %s", value, target
                )
                self._update_rate(
                    target, today, 1.0 / value, company_id=company_id
                )
            except Exception:
                # Do not raise: a failure here must not roll back dolarapi rates.
                self._logger.exception("BNA Exception: could not update the BNA rate")

    def _bna_overrides_usd(self):
        """Return whether the BNA sell rate should replace standard USD."""
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("dolares_arg.bna_override_usd", "False")
            == "True"
        )

    def _fetch_bna_usd_sell(self):
        """Download BNA HTML and return the USD bill sell rate.

        Tries /Personas first, then the homepage if parsing fails.
        """
        last_error = None
        for url in (BNA_PERSONAS_URL, BNA_HOME_URL):
            try:
                response = requests.get(url, timeout=30, headers=BNA_REQUEST_HEADERS)
                response.raise_for_status()
                html = response.text or ""
                self._logger.info(
                    "BNA Response: %s status %s (%s bytes)",
                    url,
                    response.status_code,
                    len(html),
                )
                return extract_bna_usd_sell(html)
            except Exception as err:
                last_error = err
                self._logger.warning("BNA fetch/parse failed for %s: %s", url, err)
        raise last_error or ValueError("BNA USD sell rate not available")

    def _update_rate(self, currency_code, date, rate, company_id=None):
        """Update or create currency rate for the given code and date."""
        self._logger.info(
            "Update Rate Start: Processing %s with rate %s", currency_code, rate
        )
        currency = self.env["res.currency"].search(
            [("name", "=", currency_code)], limit=1
        )
        # Mapping of currency codes to full names
        currency_full_names = {
            "USO": "Dolar Oficial",
            "USB": "United States Dollar Blue",
            "USL": "Dolar Bolsa (MEP)",
            "USC": "Dolar Contado con Liqui (CCL)",
            "USM": "Dolar Mayorista",
            "USCR": "Dolar Cripto",
            "UST": "Dolar Tarjeta",
            "USN": "Dolar Banco Nación (BNA)",
        }
        if not currency:
            if currency_code == "USD":
                self._logger.error("Standard currency USD not found in database")
                return
            try:
                currency = self.env["res.currency"].create(
                    {
                        "name": currency_code,
                        "symbol": currency_code,
                        "full_name": currency_full_names.get(
                            currency_code, currency_code
                        ),
                        "active": True,
                    }
                )
                self._logger.info(
                    f"Currency Created: Created {currency_code} with full_name {currency_full_names.get(currency_code)}"
                )
            except Exception as e:
                self._logger.error(
                    f"Currency Create Error: Failed to create {currency_code}: {str(e)}"
                )
                return  # Salir si falla el create, sin intentar rate
        domain = [("currency_id", "=", currency.id), ("name", "=", date)]
        if company_id:
            domain.append(("company_id", "=", company_id))
        else:
            domain.append(("company_id", "=", False))
        existing_rate = self.search(domain, limit=1)
        vals = {
            "currency_id": currency.id,
            "name": date,
            "rate": rate,
            "company_id": company_id or False,
        }
        if existing_rate:
            existing_rate.write(vals)
            self._logger.info(
                f"Rate Updated: Updated {currency_code} rate to {rate} for {date}"
            )
        else:
            self.create(vals)
            self._logger.info(
                f"Rate Created: Created new rate for {currency_code} with value {rate} on {date}"
            )
