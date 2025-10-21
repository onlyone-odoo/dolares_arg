from odoo import models
import requests
import logging
from datetime import date
from bs4 import BeautifulSoup
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    def fetch_arg_dollars(self):
        """Fetch and update Argentine dollar rates based on configuration."""
        today = date.today()
        _logger.info("Fetch Start", f"Starting fetch_arg_dollars for {today}")
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
                _logger.info("API Fetch Error", str(e))
                return  # Stop if API fails, but continue for BNA if enabled

        # Process each enabled API rate
        for casa, code in casa_to_currency.items():
            if config.get_param(f"dolares_arg.enable_{casa}", "False") == "True":
                data = rates_data.get(casa)
                if not data:
                    self._logger.warning(
                        f"{casa.capitalize()} Data Missing",
                        "No data found in API response",
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
                    _logger.info(
                        f"{casa.capitalize()} Rate",
                        f"Calculated rate_value: {rate_value}",
                    )
                    self._update_rate(code, today, 1.0 / rate_value)
                except Exception as e:
                    _logger.info(f"{casa.capitalize()} Processing Error", str(e))

        # Process BNA if enabled
        if config.get_param("dolares_arg.enable_bna", "False") == "True":
            _logger.info("BNA Process Start", f"Starting BNA fetch for {today}")
            try:
                bna_url = "https://www.bna.com.ar/Personas"
                _logger.info("BNA Request", f"Sending request to {bna_url}")
                page = requests.get(bna_url, timeout=10)
                _logger.info(
                    "BNA Response", f"Received response with status {page.status_code}"
                )
                soup = BeautifulSoup(page.content, "html.parser")
                _logger.info(
                    "BNA Parsing", "Soup created, searching for billetes table"
                )
                results = soup.find(id="billetes")
                if not results:
                    _logger.info("BNA Error", "BNA table not found")
                    raise UserError("BNA table not found")
                found = False
                for tr in results.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 3:
                        self._logger.debug(
                            "BNA Row Check",
                            f"Found row with {len(tds)} columns: {tds[0].text.strip()}",
                        )
                        if tds[0].text.strip() == "Dolar U.S.A":
                            _logger.info("BNA Match", "Found Dolar U.S.A row")
                            value_str = tds[2].text.strip()  # Venta
                            _logger.info("BNA Value", f"Raw value string: {value_str}")
                            value = float(value_str.replace(",", "."))
                            if value == 0:
                                _logger.info("BNA Error", "Invalid BNA value (zero)")
                                raise ValueError("Invalid BNA value")
                            _logger.info("BNA Rate", f"Calculated rate: {1.0 / value}")
                            self._update_rate("USBN", today, 1.0 / value)
                            found = True
                            _logger.info(
                                "BNA Success", f"Updated USBN rate with value {value}"
                            )
                            break
                if not found:
                    _logger.info("BNA Error", "Dolar U.S.A row not found in BNA table")
                    raise UserError("Dolar U.S.A row not found in BNA table")
            except Exception as e:
                _logger.info("BNA Exception", str(e))

    def _update_rate(self, currency_code, date, rate):
        """Update or create currency rate for the given code and date."""
        _logger.info(
            "Update Rate Start", f"Processing {currency_code} with rate {rate}"
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
            "USBN": "Dolar Banco Nación (BNA)",
        }
        if not currency:
            currency = self.env["res.currency"].create(
                {
                    "name": currency_code,
                    "symbol": currency_code,
                    "full_name": currency_full_names.get(currency_code, currency_code),
                    "active": True,
                }
            )
            _logger.info(
                "Currency Created",
                f"Created {currency_code} with full_name {currency_full_names.get(currency_code)}",
            )
        existing_rate = self.search(
            [("currency_id", "=", currency.id), ("name", "=", date)], limit=1
        )
        if existing_rate:
            existing_rate.write({"rate": rate})
            _logger.info(
                "Rate Updated", f"Updated {currency_code} rate to {rate} for {date}"
            )
        else:
            self.create(
                {
                    "currency_id": currency.id,
                    "name": date,
                    "rate": rate,
                }
            )
            _logger.info(
                "Rate Created",
                f"Created new rate for {currency_code} with value {rate} on {date}",
            )
