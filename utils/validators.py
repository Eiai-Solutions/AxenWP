"""
Validadores de input para hardening dos endpoints públicos.

São validações leves (só formato), não substituem regras de negócio. O
objetivo é falhar cedo em payloads claramente maliciosos antes deles
chegarem em queries ou no AI engine.
"""

import re

# location_id pode vir como:
#  - Z-API/whatsapp_only: "wp_" + 12 hex chars  → ex: wp_9fe4c6ef7915
#  - GHL OAuth: 18-22 chars alfanuméricos       → ex: 2gtnAmCgynIFjU8gLkLg
#  - PIT: 18-22 chars alfanuméricos             → ex: BFErlZQ1lCnSF3xDAHFS
# Pra cobrir todos com folga: 3-50 chars do conjunto seguro.
_LOCATION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,50}$")

# form_token: gerado por uuid.uuid4().hex (32 chars hex)
_FORM_TOKEN_RE = re.compile(r"^[a-f0-9]{20,64}$")

# phone aceitável após normalização (só dígitos, opcional + e @lid sufixo)
_PHONE_RE = re.compile(r"^\+?[0-9]{8,15}(@lid)?$")


def is_valid_location_id(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_LOCATION_ID_RE.match(value))


def is_valid_form_token(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_FORM_TOKEN_RE.match(value))


def is_valid_phone(value: str) -> bool:
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    return bool(_PHONE_RE.match(cleaned))
