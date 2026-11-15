"""Confere o MONGO_URL antes da publicação: conexão, senha, gravação no database do monitor e permissão restrita.

Uso:
    python -m scripts.check_mongo

Pede o MONGO_URL sem mostrar na tela (ou lê da variável de ambiente MONGO_URL, se existir). Não deixa nada no
banco: grava um documento de teste e apaga a coleção na hora. O endereço e a senha nunca são impressos.
"""
import getpass
import os
import sys
from typing import Optional

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

DEFAULT_DB = "maptiler_monitor"
TEST_COLLECTION = "connection_check"


def broader_access(privileges: list[dict], db_name: str) -> list[str]:
    """Recursos fora do database do monitor a que o usuário tem acesso (vazio = permissão restrita, como deve ser)."""
    extra = set()
    for privilege in privileges:
        resource = privilege.get("resource", {})
        if resource.get("anyResource") or resource.get("cluster"):
            extra.add("o cluster inteiro")
        elif resource.get("db", db_name) != db_name:
            extra.add(f"o database '{resource['db']}'" if resource["db"] else "todos os databases")
    return sorted(extra)


def clean(value: str) -> str:
    """Tira o que costuma vir junto ao colar: espaços, aspas e um 'MONGO_URL=' na frente."""
    value = value.strip()
    if value.upper().startswith("MONGO_URL="):
        value = value.split("=", 1)[1].strip()
    return value.strip("'\"").strip()


def main(mongo_url: Optional[str] = None, db_name: Optional[str] = None) -> int:
    if mongo_url is None and os.environ.get("MONGO_URL"):
        mongo_url = os.environ["MONGO_URL"]
        print("Usando o MONGO_URL guardado neste Terminal (para colar outro: unset MONGO_URL e rode de novo).")
    url = clean(mongo_url if mongo_url is not None else getpass.getpass("Cole o MONGO_URL e tecle Enter: "))
    db_name = (db_name or os.environ.get("DB_NAME") or DEFAULT_DB).strip()
    if not url.startswith(("mongodb://", "mongodb+srv://")):
        print(f"O endereço precisa começar com mongodb+srv:// (ou mongodb://), mas o texto recebido tem {len(url)} "
              "caracteres e começa de outro jeito. Copie de novo o endereço inteiro e confira o que foi colado.")
        return 1

    client = MongoClient(url, serverSelectionTimeoutMS=10_000)
    try:
        client.admin.command("ping")
        print("1. Conexão e senha: ok")

        collection = client[db_name][TEST_COLLECTION]
        collection.insert_one({"ok": True})
        collection.drop()
        print(f"2. Gravar em {db_name}: ok")

        status = client.admin.command("connectionStatus", showPrivileges=True)
        privileges = status.get("authInfo", {}).get("authenticatedUserPrivileges", [])
        extra = broader_access(privileges, db_name)
        if extra:
            print(f"3. ATENÇÃO: o usuário também tem acesso a: {', '.join(extra)}. "
                  f"Deixe só readWrite em {db_name} (Atlas > Database Access > Edit).")
            return 1
        print(f"3. Permissão restrita a {db_name}: ok")
        return 0
    except OperationFailure as exc:
        if exc.code == 18:  # AuthenticationFailed
            print("Falhou: usuário ou senha errados no MONGO_URL.")
        elif exc.code == 13:  # Unauthorized
            print(f"Falhou: o usuário não tem permissão para gravar em {db_name} (falta readWrite nesse database).")
        else:
            print(f"Falhou: o MongoDB recusou a operação (código {exc.code}).")
        return 1
    except PyMongoError as exc:
        print(f"Falhou: não foi possível conectar ({type(exc).__name__}). Confira o endereço do cluster "
              "(a parte depois do @) e o Network Access no Atlas.")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
