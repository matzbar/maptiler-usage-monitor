"""Chamadas agendadas ao monitor: o que o GitHub Actions executa (.github/workflows/scheduler.yml).

Uso (com MONITOR_URL e MONITOR_CHECK_TOKEN definidos):
    python -m scripts.scheduler check           # verifica o consumo (e, com isso, os alertas)
    python -m scripts.scheduler check report    # verifica e manda o relatório diário

Sem MONITOR_URL ou MONITOR_CHECK_TOKEN, avisa que o agendamento está desligado e sai sem erro: antes de o
monitor estar publicado, o GitHub não manda e-mails de falha.

Saída 0 = tudo certo; 1 = alguma rotina falhou (o job fica vermelho e o GitHub avisa por e-mail).
Só depende do httpx: o job não instala o resto do projeto.
"""
import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

import httpx

TASKS = ("check", "report")
TIMEOUT_S = 60  # uma verificação faz 4 consultas ao MapTiler, com novas tentativas
WAKE_ATTEMPTS = 7  # hospedagem gratuita (Render) desliga o serviço parado; religar leva perto de 1 minuto
WAKE_DELAY_S = 15
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class Outcome:
    task: str
    ok: bool
    text: str
    warning: bool = False  # deu certo, mas merece atenção (ex.: e-mail não configurado)


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _pct(metric: Optional[dict]) -> str:
    if not metric:
        return "—"
    return f"{metric['percent']:.1f}%".replace(".", ",")


def check_url(url: str) -> Optional[str]:
    """Erro de configuração do endereço, ou None. Só https (fora da máquina local): o token vai no cabeçalho."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return None
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return None
    return "MONITOR_URL precisa começar com https:// (http só para localhost), sem caminho extra"


def wake(client: httpx.Client, sleep: Callable[[float], None]) -> Optional[str]:
    """Chama GET /health até o monitor responder. Devolve None quando ele está no ar, ou o motivo da falha.

    Pode repetir à vontade: /health não muda nada. As rotinas, ao contrário, são chamadas uma única vez:
    uma verificação que falhou também conta para o intervalo mínimo, e repeti-la só daria "muito cedo".
    """
    last = ""
    for attempt in range(1, WAKE_ATTEMPTS + 1):
        try:
            response = client.get("/health")
            if response.status_code == 200 and _json(response).get("status") == "ok":
                return None
            last = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        if attempt < WAKE_ATTEMPTS:
            sleep(WAKE_DELAY_S)
    return f"o monitor não respondeu em /health depois de {WAKE_ATTEMPTS} tentativas ({last})"


def run_check(client: httpx.Client) -> Outcome:
    response = client.post("/v1/checks")
    body = _json(response)
    status = response.status_code
    if status == 200:
        return Outcome("check", True, f"verificação feita: requests em {_pct(body.get('requests'))}, "
                                      f"sessions em {_pct(body.get('sessions'))} (faixa {body.get('level', '—')})")
    if status == 429 and "last_check" in body:
        return Outcome("check", True, "a última verificação é recente; nada a fazer agora")
    if status == 409:
        return Outcome("check", True, "já havia uma verificação em andamento")
    if status == 502:
        error = body.get("error") or {}
        return Outcome("check", False, f"a consulta ao MapTiler falhou ({error.get('kind', '?')}): "
                                       f"{error.get('message', 'sem detalhe')}")
    return Outcome("check", False, _http_problem(status, body))


def run_report(client: httpx.Client) -> Outcome:
    response = client.post("/v1/reports/daily")
    body = _json(response)
    if response.status_code != 200:
        return Outcome("report", False, _http_problem(response.status_code, body))
    status, day = body.get("status"), body.get("date", "?")
    if status in ("sent", "sending"):
        return Outcome("report", True, f"relatório de {day} enviado")
    if status == "already_sent":
        return Outcome("report", True, f"o relatório de {day} já tinha sido enviado")
    if status == "not_configured":
        return Outcome("report", True, "relatório não enviado: e-mail não configurado no monitor", warning=True)
    return Outcome("report", False, f"o envio do relatório de {day} falhou: {body.get('error') or status}")


def _http_problem(status: int, body: dict) -> str:
    if status in (401, 403):
        return f"o monitor recusou o token (HTTP {status}): confira o segredo MONITOR_CHECK_TOKEN"
    detail = body.get("detail")
    return f"resposta inesperada do monitor (HTTP {status})" + (f": {detail}" if isinstance(detail, str) else "")


def _report(outcomes: list[Outcome], env: Mapping[str, str]) -> None:
    """Uma linha por rotina no log; no GitHub Actions, também anotações e o resumo da execução."""
    in_actions = env.get("GITHUB_ACTIONS") == "true"
    lines = []
    for o in outcomes:
        mark = "ok" if o.ok and not o.warning else ("atenção" if o.ok else "FALHOU")
        line = f"{o.task}: {mark} — {o.text}"
        lines.append(line)
        print(line)
        if in_actions and (o.warning or not o.ok):
            print(f"::{'warning' if o.ok else 'error'} title=Monitor MapTiler ({o.task})::{o.text}")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Monitor MapTiler\n\n" + "".join(f"- {line}\n" for line in lines))


def main(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None,
         transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.scheduler", description=__doc__.split("\n")[0])
    parser.add_argument("tasks", nargs="+", choices=TASKS, help="rotinas a rodar, nesta ordem")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    url = env.get("MONITOR_URL", "").strip().rstrip("/")
    token = env.get("MONITOR_CHECK_TOKEN", "").strip()
    if not url or not token:
        message = "agendamento desligado: defina os segredos MONITOR_URL e MONITOR_CHECK_TOKEN"
        print(f"::notice title=Monitor MapTiler::{message}" if env.get("GITHUB_ACTIONS") == "true" else message)
        return 0
    problem = check_url(url)
    if problem:
        _report([Outcome("config", False, problem)], env)
        return 1

    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S,
                      follow_redirects=False, transport=transport) as client:
        down = wake(client, sleep)
        if down:
            _report([Outcome("health", False, down)], env)
            return 1
        runners = {"check": run_check, "report": run_report}
        outcomes = []
        for task in dict.fromkeys(args.tasks):  # sem repetir, na ordem pedida
            try:
                outcomes.append(runners[task](client))
            except httpx.HTTPError as exc:
                outcomes.append(Outcome(task, False, f"sem resposta do monitor ({type(exc).__name__})"))
    _report(outcomes, env)
    return 0 if all(o.ok for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
