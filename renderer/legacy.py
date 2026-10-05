"""
renderer/legacy.py — Renderer untuk render_mode == "legacy".

Tugasnya HANYA mempertahankan kompatibilitas: html_code template lama
dikembalikan apa adanya, tanpa placeholder replacement, tanpa modifikasi.

Data undangan (wedding) diabaikan oleh renderer ini — template legacy lama
menyimpan kontennya langsung di html_code (contenteditable / statis).
"""


def render_legacy(html_code, wedding_data=None):
    """Kembalikan HTML legacy tanpa perubahan agresif."""
    return html_code or ""
