import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def pytest_collection_modifyitems(items):
    """Testes de navegador (tests/e2e) por último.

    O Playwright síncrono mantém um laço asyncio aberto na thread principal enquanto o navegador da sessão
    existe; qualquer teste com asyncio.run depois dele falharia. Rodando por último, nada vem depois.
    """
    items.sort(key=lambda item: "e2e" in item.path.parts)  # sort estável: o resto mantém a ordem
