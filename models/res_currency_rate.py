# -*- coding: utf-8 -*-
import logging
import requests
import unicodedata
from bs4 import BeautifulSoup
from odoo import models, fields, _
from odoo.exceptions import UserError

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
}

class ResCurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    def fetch_arg_dollars(self, company_id=None):
        """
        Orquesta la actualización de tipos de cambio:
        1. API DolarApi (Varios tipos)
        2. BNA Scraping (Override del USD oficial)
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

    def _fetch_bna_scraping(self, date_rate, company_id):
        """Maneja el scraping de BNA y actualiza la moneda USD oficial"""
        _logger.info("BNA: Iniciando Scraping...")
        url = "https://www.bna.com.ar/Personas"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, "html.parser")
            table = soup.find(id="billetes")
            
            if not table:
                raise UserError("Estructura BNA cambió: No se encontró tabla 'billetes'")

            val_venta = None
            
            # Búsqueda optimizada en la tabla
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 3:
                    continue
                
                # Normalización agresiva de texto
                curr_name = unicodedata.normalize("NFKD", tds[0].get_text(strip=True)).lower()
                
                if "dolar u.s.a" in curr_name:
                    val_str = tds[2].get_text(strip=True).replace(",", ".")
                    val_venta = float(val_str)
                    break
            
            if val_venta and val_venta > 0:
                _logger.info(f"BNA Success: Dolar Oficial detectado: {val_venta}")
                # "USN" es nuestro trigger interno para decir "Actualiza el USD del sistema"
                self._update_currency_rate("USN", date_rate, val_venta, company_id)
            else:
                raise UserError("No se pudo extraer el valor del Dolar U.S.A de la tabla BNA")

        except Exception as e:
            _logger.error(f"BNA Scraping Error: {str(e)}")

    def _update_currency_rate(self, currency_code, date_rate, rate_value, company_id):
        """
        Crea o actualiza la tasa.
        rate_value: Cantidad de ARS por 1 unidad de Moneda Extranjera.
        Odoo almacena la tasa inversa (1 / rate_value) si la base es ARS.
        """
        Currency = self.env["res.currency"]
        
        # Lógica especial para BNA -> USD Oficial
        if currency_code == "USN":
            currency = Currency.search([("name", "=", "USD")], limit=1)
            if not currency:
                _logger.error("Error Crítico: Moneda USD no existe en el sistema.")
                return
        else:
            # Buscar o crear monedas personalizadas
            currency = Currency.search([("name", "=", currency_code)], limit=1)
            if not currency:
                currency = Currency.create({
                    "name": currency_code,
                    "symbol": currency_code,
                    "full_name": CURRENCY_NAMES.get(currency_code, currency_code),
                    "active": True,
                    "position": "before", # Estético
                })
                _logger.info(f"Moneda creada: {currency_code}")

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