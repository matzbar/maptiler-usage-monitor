import pytest

from app.config import load_settings

TOKEN_A, TOKEN_B = "x" * 32, "y" * 32


def test_defaults():
    s = load_settings({})
    assert (s.quota_requests, s.quota_sessions, s.db_name) == (100_000, 5_000, "maptiler_monitor")
    assert (s.min_check_interval_s, s.refresh_after_s, s.stale_after_s) == (600, 6 * 3600, 12 * 3600)
    assert (s.client_ip_header, s.trusted_proxy_hops) == ("", 0)


def test_values_from_environment():
    s = load_settings({"QUOTA_REQUESTS": "200000", "CHECK_MIN_INTERVAL_MIN": "5", "REFRESH_AFTER_HOURS": "3",
                       "RENDER": "true", "RENDER_GIT_COMMIT": "abcdef1234"})
    assert s.quota_requests == 200_000 and s.min_check_interval_s == 300 and s.refresh_after_s == 3 * 3600
    assert (s.client_ip_header, s.trusted_proxy_hops, s.build_commit) == ("cf-connecting-ip", 2, "abcdef1")


@pytest.mark.parametrize("env,message", [
    ({"CHECK_TOKEN": "curto"}, "pelo menos 32"),
    ({"ADMIN_TOKEN": "curto"}, "pelo menos 32"),
    ({"CHECK_TOKEN": TOKEN_A, "ADMIN_TOKEN": TOKEN_A}, "diferentes"),
    ({"QUOTA_REQUESTS": "muito"}, "número inteiro"),
    ({"QUOTA_SESSIONS": "0"}, "no mínimo 1"),
    ({"DB_NAME": "nome com espaço"}, "DB_NAME"),
])
def test_invalid_configuration_stops_startup(env, message):
    with pytest.raises(ValueError, match=message):
        load_settings(env)


def test_secrets_are_hidden_from_repr():
    s = load_settings({"MAPTILER_SERVICE_TOKEN": "segredo-do-maptiler", "CHECK_TOKEN": TOKEN_A,
                       "ADMIN_TOKEN": TOKEN_B, "MONGO_URL": "mongodb+srv://usuario:senha@cluster"})
    text = repr(s)
    for secret in ("segredo-do-maptiler", TOKEN_A, TOKEN_B, "senha"):
        assert secret not in text
