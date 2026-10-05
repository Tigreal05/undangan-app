"""
renderer/ — Template Engine Invinite V1

Prinsip: ONE TEMPLATE (master) + MANY WEDDINGS (data) -> RENDERER -> GENERATED INVITATION.

Modul:
  - legacy.py      : render_mode == "legacy"      -> html_code dikembalikan apa adanya (kompatibilitas penuh)
  - placeholder.py : render_mode == "placeholder" -> pengisian {{key}} dgn escaping HTML eksplisit
  - validator.py   : validasi template placeholder (placeholder dikenal / wajib / CSS-JS kurung kurawal)
  - renderer.py    : dispatch utama render_template(template, wedding_data)
"""
from .renderer import (
    render_template,
    load_template,
    PLACEHOLDER_KEYS,
    REQUIRED_PLACEHOLDERS,
    RenderError,
    UnknownPlaceholderError,
    InvalidTemplateError,
)
from .validator import validate_template, validate_photo_value

__all__ = [
    "render_template",
    "load_template",
    "validate_template",
    "validate_photo_value",
    "PLACEHOLDER_KEYS",
    "REQUIRED_PLACEHOLDERS",
    "RenderError",
    "UnknownPlaceholderError",
    "InvalidTemplateError",
]
