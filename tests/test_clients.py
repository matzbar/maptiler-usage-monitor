import pytest

from app.auth import hash_key, identify
from app.storage import MemoryStorage
from scripts.clients import main
from tests.helpers import KEY_A, make_settings

pytestmark = pytest.mark.anyio


async def test_create_shows_key_once_and_stores_only_the_hash(capsys):
    storage = MemoryStorage()
    assert await main(["create", "HydrantRouteSP", "--maptiler-key", KEY_A], storage) == 0
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert key.startswith("mum_") and len(key) > 40
    saved = storage.clients[0]
    assert saved["key_hash"] == hash_key(key) and key not in str(saved)
    assert saved["maptiler_keys"] == [KEY_A] and saved["active"] is True
    who = await identify(key, make_settings(), storage)
    assert who.role == "client" and who.name == "HydrantRouteSP" and who.maptiler_keys == frozenset({KEY_A})


async def test_duplicate_and_invalid_names(capsys):
    storage = MemoryStorage()
    assert await main(["create", "HydrantRouteSP"], storage) == 0
    assert await main(["create", "HydrantRouteSP"], storage) == 1
    assert await main(["create", "../etc"], storage) == 1
    assert "já existe" in capsys.readouterr().err
    assert len(storage.clients) == 1


async def test_list_never_shows_keys(capsys):
    storage = MemoryStorage()
    await main(["create", "HydrantRouteSP", "--maptiler-key", KEY_A], storage)
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert await main(["list"], storage) == 0
    out = capsys.readouterr().out
    assert "HydrantRouteSP (ativo)" in out and key not in out and KEY_A not in out


async def test_deactivate_and_reactivate(capsys):
    storage = MemoryStorage()
    await main(["create", "HydrantRouteSP"], storage)
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert await main(["deactivate", "HydrantRouteSP"], storage) == 0
    assert await identify(key, make_settings(), storage) is None
    assert await main(["reactivate", "HydrantRouteSP"], storage) == 0
    assert (await identify(key, make_settings(), storage)).role == "client"
    assert await main(["deactivate", "Inexistente"], storage) == 1
