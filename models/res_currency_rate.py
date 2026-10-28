# -*- coding: utf-8 -*-
import logging

import requests

from odoo import fields, models

from .bna_parser import (
    BNA_HOME_URL,
    BNA_PERSONAS_URL,
    BNA_REQUEST_HEADERS,
    extract_bna_usd_sell,
)

_logger = logging.getLogger(__name__)

# Mapping: Config Field Suffix -> Currency Code
CASA_TO_CURRENCY = {
    "oficial": "USO",
    "blue": "USB",
    "bolsa": "USL",
    "contadoconliqui": "USC",
    "mayorista": "USM",
    "cripto": "USCR",
    "tarjeta": "UST",
}

CURRENCY_NAMES = {
    "USO": "Dolar Oficial",
    "USB": "United States Dollar Blue",
    "USL": "Dolar Bolsa (MEP)",
    "USC": "Dolar Contado con Liqui (CCL)",
    "USM": "Dolar Mayorista",
    "USCR": "Dolar Cripto",
    "UST": "Dolar Tarjeta",
    "USN": "Dolar Banco Nación (BNA)",
}

class ResCurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    def fetch_arg_dollars(self, company_id=None):
        """
        Orquesta la actualización de tipos de cambio:
        1. API DolarApi (Varios tipos)
        2. BNA scraping (USN, or USD when the override setting is on)
        """
        # Usar context_today para respetar timezone de la compañía/usuario
        today = fields.Date.context_today(self)
        _logger.info(f"Fetch Start: Iniciando actualización para {today} (Company: {company_id or 'Global'})")

        config = self.env["ir.config_parameter"].sudo()
        
        # 1. Preparar lista de monedas a buscar en API para evitar iteraciones innecesarias
        api_casas_enabled = [
            casa for casa in CASA_TO_CURRENCY
            if config.get_param(f"dolares_arg.enable_{casa}") == "True"
        ]

        # === Ejecución API ===
        if api_casas_enabled:
            self._fetch_dolarapi_data(api_casas_enabled, today, company_id)

        # === Ejecución BNA ===
        if config.get_param("dolares_arg.enable_bna") == "True":
            self._fetch_bna_scraping(today, company_id)

    def _fetch_dolarapi_data(self, casas_enabled, date_rate, company_id):
        """Maneja la lógica específica de DolarAPI"""
        url = "https://dolarapi.com/v1/dolares"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            rates_data = {item["casa"]: item for item in response.json()}
            
            for casa in casas_enabled:
                data = rates_data.get(casa)
                if not data:
                    _logger.warning(f"API Warning: No hay datos para '{casa}'")
                    continue

                try:
                    buy = float(data.get("compra", 0))
                    sell = float(data.get("venta", 0))

                    if casa == "blue":
                        # Promedio para el Blue
                        if buy > 0 and sell > 0:
                            rate_value = (buy + sell) / 2
                        else:
                            raise ValueError("Compra/Venta inválida para Blue")
                    else:
                        # Venta para el resto
                        if sell <= 0:
                            raise ValueError("Precio de venta es cero o negativo")
                        rate_value = sell

                    code = CASA_TO_CURRENCY[casa]
                    self._update_currency_rate(code, date_rate, rate_value, company_id)

                except Exception as e:
                    _logger.error(f"Error procesando {casa}: {str(e)}")

        except Exception as e:
            _logger.error(f"API Fetch Error (DolarApi): {str(e)}")

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

    def _fetch_bna_scraping(self, date_rate, company_id):
        """Scrape BNA and store the sell rate on USN, or on USD if configured."""
        _logger.info("BNA: Iniciando Scraping...")
        try:
            value = self._fetch_bna_usd_sell()
            target = "USD" if self._bna_overrides_usd() else "USN"
            _logger.info("BNA Success: venta %s → %s", value, target)
            self._update_currency_rate(target, date_rate, value, company_id)
        except Exception:
            _logger.exception("BNA Scraping Error")

    def _update_currency_rate(self, currency_code, date_rate, rate_value, company_id):
        """
        Crea o actualiza la tasa.
        rate_value: Cantidad de ARS por 1 unidad de Moneda Extranjera.
        Odoo almacena la tasa inversa (1 / rate_value) si la base es ARS.
        """
        Currency = self.env["res.currency"]

        currency = Currency.search([("name", "=", currency_code)], limit=1)
        if currency_code == "USD":
            if not currency:
                _logger.error("Error Crítico: Moneda USD no existe en el sistema.")
                return
        elif not currency:
            currency = Currency.create({
                "name": currency_code,
                "symbol": currency_code,
                "full_name": CURRENCY_NAMES.get(currency_code, currency_code),
                "active": True,
                "position": "before",
            })
            _logger.info("Moneda creada: %s", currency_code)

        # Cálculo de la tasa inversa para Odoo
        # Si 1 USD = 1000 ARS -> Odoo Rate = 0.001
        inverse_rate = 1.0 / rate_value if rate_value else 0.0

        # Buscar tasa existente
        domain = [
            ("currency_id", "=", currency.id),
            ("name", "=", date_rate),
            ("company_id", "=", company_id or False),
        ]
        existing_rate = self.search(domain, limit=1)

        vals = {
            "currency_id": currency.id,
            "name": date_rate,
            "rate": inverse_rate,
            "company_id": company_id or False,
        }

        if existing_rate:
            existing_rate.write(vals)
            _logger.info(f"Update: {currency.name} a {inverse_rate:.6f} ({rate_value})")
        else:
            self.create(vals)
            _logger.info(f"Create: {currency.name} a {inverse_rate:.6f} ({rate_value})")