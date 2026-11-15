"""scripts/check_mongo.py: avaliação da permissão do usuário (a parte que não depende de um MongoDB)."""
from scripts.check_mongo import broader_access, main

DB = "maptiler_monitor"


def test_only_the_monitor_database_is_restricted():
    privileges = [{"resource": {"db": DB, "collection": ""}, "actions": ["find", "insert"]},
                  {"resource": {"db": DB, "collection": "system.js"}, "actions": ["find"]}]
    assert broader_access(privileges, DB) == []


def test_other_databases_and_cluster_access_are_reported():
    privileges = [{"resource": {"db": DB, "collection": ""}},
                  {"resource": {"db": "hydrantroute", "collection": ""}},
                  {"resource": {"db": "", "collection": ""}},
                  {"resource": {"cluster": True}},
                  {"resource": {"anyResource": True}}]
    assert broader_access(privileges, DB) == ["o cluster inteiro", "o database 'hydrantroute'", "todos os databases"]


def test_rejects_something_that_is_not_a_mongodb_address(capsys):
    assert main("cluster0.abcde.mongodb.net") == 1
    assert "mongodb+srv://" in capsys.readouterr().out


def test_cleans_what_usually_comes_along_when_pasting():
    from scripts.check_mongo import clean

    url = "mongodb+srv://u:p@c.mongodb.net/?x=1"
    for pasted in (url, f"  {url}\n", f"'{url}'", f'"{url}"', f"MONGO_URL={url}", f"MONGO_URL='{url}'"):
        assert clean(pasted) == url


def test_says_when_it_uses_the_variable_from_the_terminal(monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URL", "texto colado errado")
    assert main() == 1
    out = capsys.readouterr().out
    assert "Usando o MONGO_URL guardado neste Terminal" in out and "unset MONGO_URL" in out
    assert "texto colado errado" not in out  # nunca mostra o que foi colado (pode ter a senha)
