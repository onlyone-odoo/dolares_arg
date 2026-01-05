# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Agrupamos los campos para mantener el código ordenado
    enable_oficial = fields.Boolean(
        "Enable Oficial Dollar", config_parameter="dolares_arg.enable_oficial"
    )
    enable_blue = fields.Boolean(
        "Enable Blue Dollar", config_parameter="dolares_arg.enable_blue"
    )
    enable_bolsa = fields.Boolean(
        "Enable Bolsa Dollar", config_parameter="dolares_arg.enable_bolsa"
    )
    enable_contadoconliqui = fields.Boolean(
        "Enable CCL Dollar", config_parameter="dolares_arg.enable_contadoconliqui"
    )
    enable_mayorista = fields.Boolean(
        "Enable Mayorista Dollar", config_parameter="dolares_arg.enable_mayorista"
    )
    enable_cripto = fields.Boolean(
        "Enable Cripto Dollar", config_parameter="dolares_arg.enable_cripto"
    )
    enable_tarjeta = fields.Boolean(
        "Enable Tarjeta Dollar", config_parameter="dolares_arg.enable_tarjeta"
    )

    enable_bna = fields.Boolean(
        string="Enable BNA Dollar",
        config_parameter="dolares_arg.enable_bna",
        help="Realiza scraping a la web del Banco Nación para actualizar la moneda base USD.",
    )
