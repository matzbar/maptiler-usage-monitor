"""Gerencia os projetos clientes: quem pode ler o consumo pela API.

Uso (com MONGO_URL e DB_NAME definidos):
    python -m scripts.projetos criar HydrantRouteSP --chave-maptiler <chave pública do mapa>
    python -m scripts.projetos listar
    python -m scripts.projetos desativar HydrantRouteSP
    python -m scripts.projetos reativar HydrantRouteSP

A chave do projeto aparece uma única vez, na criação: o banco guarda só o hash dela.
--chave-maptiler (pode repetir) diz quais chaves do MapTiler o projeto vê no detalhamento por chave.
"""
import argparse
import asyncio
import datetime as dt
import re
import sys
from typing import Optional

from app.auth import hash_key, new_client_key
from app.config import load_settings
from app.storage import DuplicateClient, MongoStorage, Storage

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}")


async def create_client(storage: Storage, name: str, maptiler_keys: list[str], now: dt.datetime) -> str:
    """Cria o projeto e devolve a chave dele (a única vez em que ela existe fora de quem a recebe)."""
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("nome inválido: use letras, números, espaço, _ . - (até 64 caracteres)")
    key = new_client_key()
    await storage.add_client({
        "name": name,
        "key_hash": hash_key(key),
        "key_prefix": key[:8],  # para reconhecer a chave numa lista, sem revelá-la
        "maptiler_keys": sorted({k.strip() for k in maptiler_keys if k.strip()}),
        "active": True,
        "created_at": now,
    })
    return key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.projetos", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("criar", help="cria um projeto e mostra a chave dele uma única vez")
    create.add_argument("nome")
    create.add_argument("--chave-maptiler", action="append", default=[], dest="maptiler_keys",
                        help="chave do MapTiler usada pelo projeto (pode repetir)")
    sub.add_parser("listar", help="lista os projetos (sem as chaves)")
    for command in ("desativar", "reativar"):
        sub.add_parser(command, help=f"{command} um projeto").add_argument("nome")
    return parser


async def main(argv: Optional[list[str]] = None, storage: Optional[Storage] = None) -> int:
    args = _parser().parse_args(argv)
    own_storage = storage is None
    if own_storage:
        settings = load_settings()
        if not settings.mongo_url:
            print("Defina MONGO_URL (e DB_NAME, se não for maptiler_monitor).", file=sys.stderr)
            return 2
        storage = MongoStorage(settings.mongo_url, settings.db_name, settings.retention_days)
        await storage.connect()
    try:
        if args.command == "criar":
            try:
                key = await create_client(storage, args.nome, args.maptiler_keys, dt.datetime.now(dt.timezone.utc))
            except (ValueError, DuplicateClient) as exc:
                message = f"já existe um projeto chamado {args.nome!r}" if isinstance(exc, DuplicateClient) else exc
                print(f"Erro: {message}", file=sys.stderr)
                return 1
            print(f"Projeto {args.nome!r} criado.")
            print("Chave do projeto (guarde agora num lugar seguro; ela não aparece de novo):")
            print(key)
        elif args.command == "listar":
            clients = await storage.list_clients()
            if not clients:
                print("Nenhum projeto.")
            for c in clients:
                state = "ativo" if c.get("active", True) else "desativado"
                keys = ", ".join(k[:4] + "…" for k in c.get("maptiler_keys", [])) or "nenhuma"
                print(f"- {c['name']} ({state}) · chave {c['key_prefix']}… · chaves do MapTiler: {keys}")
        else:
            found = await storage.set_client_active(args.nome, args.command == "reativar")
            if not found:
                print(f"Erro: projeto {args.nome!r} não encontrado", file=sys.stderr)
                return 1
            print(f"Projeto {args.nome!r} {'reativado' if args.command == 'reativar' else 'desativado'}.")
        return 0
    finally:
        if own_storage:
            await storage.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
