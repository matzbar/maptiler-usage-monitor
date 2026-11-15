"""Quem está chamando a API. Cada papel tem o seu token, sempre em `Authorization: Bearer <token>`:

  - admin (ADMIN_TOKEN): tudo;
  - scheduler (CHECK_TOKEN): só dispara verificações;
  - client (chave do projeto, criada por scripts/clients.py): só lê o consumo, e só vê as próprias chaves
    do MapTiler. O banco guarda apenas o hash SHA-256 da chave.
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal, Optional

from app.config import Settings
from app.storage import Storage

CLIENT_KEY_PREFIX = "mum_"  # maptiler-usage-monitor: deixa claro de onde a chave é, se ela vazar
Role = Literal["admin", "scheduler", "client"]


@dataclass(frozen=True)
class Principal:
    role: Role
    name: str
    maptiler_keys: Optional[frozenset[str]] = None  # None = vê todas as chaves (administrador)


def new_client_key() -> str:
    return CLIENT_KEY_PREFIX + secrets.token_urlsafe(32)  # 256 bits aleatórios


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())  # tempo constante: não revela quantos caracteres batem


async def identify(token: str, settings: Settings, storage: Storage) -> Optional[Principal]:
    if not token or len(token) > 200:
        return None
    if settings.admin_token and _same(token, settings.admin_token):
        return Principal("admin", "admin")
    if settings.check_token and _same(token, settings.check_token):
        return Principal("scheduler", "agendador")
    if token.startswith(CLIENT_KEY_PREFIX):
        client = await storage.client_by_hash(hash_key(token))
        if client and client.get("active", True):
            return Principal("client", client["name"], frozenset(client.get("maptiler_keys") or ()))
    return None
