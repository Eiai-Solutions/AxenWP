"""Singleton do rate limiter, importável sem dependência circular do main.py."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Aplicado por IP do cliente. Em produção atrás de proxy reverso (EasyPanel),
# slowapi tenta ler X-Forwarded-For automaticamente via get_remote_address.
limiter = Limiter(key_func=get_remote_address)
