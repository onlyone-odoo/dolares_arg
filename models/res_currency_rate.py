from odoo import models
import requests
from datetime import date
from bs4 import BeautifulSoup
from odoo.exceptions import UserError


class CurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    def fetch_arg_dollars(self):
        """Fetch and update Argentine dollar rates based on configuration."""
        today = date.today()
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
                self._log_error("API Fetch Error", str(e))
                return  # Stop if API fails, but continue for BNA if enabled

        # Process each enabled API rate
        for casa, code in casa_to_currency.items():
            if config.get_param(f"dolares_arg.enable_{casa}", "False") == "True":
                data = rates_data.get(casa)
                if not data:
                    self._log_error(
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
                    self._update_rate(code, today, 1.0 / rate_value)
                except Exception as e:
                    self._log_error(f"{casa.capitalize()} Processing Error", str(e))

        # Process BNA if enabled
        if config.get_param("dolares_arg.enable_bna", "False") == "True":
            try:
                bna_url = "https://www.bna.com.ar/Personas"
                page = requests.get(bna_url, timeout=10)
                soup = BeautifulSoup(page.content, "html.parser")
                results = soup.find(id="billetes")
                if not results:
                    raise UserError("BNA table not found")
                found = False
                for tr in results.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 3 and tds[0].text.strip() == "Dolar U.S.A":
                        value_str = tds[2].text.strip()  # Venta
                        value = float(value_str.replace(",", "."))
                        if value == 0:
                            raise ValueError("Invalid BNA value")
                        self._update_rate("USBN", today, 1.0 / value)
                        found = True
                        break
                if not found:
                    raise UserError("Dolar U.S.A row not found in BNA table")
            except Exception as e:
                self._log_error("BNA Scraping Error", str(e))

    def _update_rate(self, currency_code, date, rate):
        """Update or create currency rate for the given code and date."""
        currency = self.env["res.currency"].search(
            [("name", "=", currency_code)], limit=1
        )
        if not currency:
            currency = self.env["res.currency"].create(
                {
                    "name": currency_code,
                    "symbol": "$",
                    "active": True,
                }
            )
        existing_rate = self.search(
            [("currency_id", "=", currency.id), ("name", "=", date)], limit=1
        )
        if existing_rate:
            existing_rate.write({"rate": rate})
        else:
            self.create(
                {
                    "currency_id": currency.id,
                    "name": date,
                    "rate": rate,
                }
            )

    def _log_error(self, name, message):
        """Log errors to ir.logging."""
        self.env["ir.logging"].create(
            {
                "name": name,
                "type": "server",
                "level": "ERROR",
                "message": message,
                "path": __file__,
                "func": "fetch_arg_dollars",
                "line": 0,
            }
        )
