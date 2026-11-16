"""render.yaml (publicação no Render): coerente com .env.example e sem nenhum segredo escrito no arquivo."""
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
SECRET_HINTS = ("TOKEN", "KEY", "MONGO_URL", "PHONE", "EMAIL_TO")


def env_example() -> dict[str, list[str]]:
    """Variáveis de .env.example por seção ('Obrigatórias', 'Alertas', 'Opcionais ...')."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        header = re.match(r"# --- (.+?) ---", line)
        if header:
            current = header.group(1)
        elif re.match(r"[A-Z_]+=", line):
            sections.setdefault(current, []).append(line.split("=", 1)[0])
    return sections


@pytest.fixture(scope="module")
def service() -> dict:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    assert len(blueprint["services"]) == 1
    return blueprint["services"][0]


def test_free_docker_web_service_with_health_check(service):
    assert service["type"] == "web" and service["runtime"] == "docker"
    assert service["plan"] == "free"  # sem plano explícito, o Render cria num plano pago
    assert service["healthCheckPath"] == "/health"
    assert service["autoDeployTrigger"] == "checksPass"  # só publica depois do CI


def test_build_filter_covers_everything_the_image_copies(service):
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied = re.findall(r"^COPY (\S+)", dockerfile, flags=re.M)
    paths = service["buildFilter"]["paths"]
    for source in copied:
        name = source.rstrip("/")
        assert any(p == name or p.startswith(f"{name}/") for p in paths), f"{source} não republica o serviço"
    assert "Dockerfile" in paths


def test_env_vars_exist_in_env_example(service):
    documented = {key for keys in env_example().values() for key in keys}
    for var in service["envVars"]:
        assert var["key"] in documented, f"{var['key']} não está em .env.example"


def test_every_required_and_alert_variable_is_declared(service):
    declared = {var["key"] for var in service["envVars"]}
    sections = env_example()
    for section in ("Obrigatórias", "Alertas"):
        assert section in sections, f"seção '{section}' sumiu de .env.example"
    required = set(sections["Obrigatórias"]) | {"RESEND_API_KEY", "ALERT_EMAIL_TO", "ALERT_WHATSAPP_PHONE",
                                                 "CALLMEBOT_API_KEY"}
    assert required <= declared, f"faltam no render.yaml: {sorted(required - declared)}"


def test_no_secret_is_written_in_the_file(service):
    for var in service["envVars"]:
        if any(hint in var["key"] for hint in SECRET_HINTS):
            assert "value" not in var, f"{var['key']} não pode ter valor no render.yaml"
            assert var.get("sync") is False or var.get("generateValue") is True, var["key"]


def test_tokens_are_generated_by_render(service):
    generated = {var["key"] for var in service["envVars"] if var.get("generateValue")}
    assert {"CHECK_TOKEN", "ADMIN_TOKEN"} <= generated  # 44 caracteres: passa no mínimo de 32


# ----------------------------- Agendamento (scheduler.yml) -----------------------------
def scheduler_workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/scheduler.yml").read_text(encoding="utf-8"))


def test_every_routine_has_a_backup_and_the_report_too():
    workflow = scheduler_workflow()
    triggers = workflow.get("on") or workflow[True]  # o YAML 1.1 lê a chave "on" como True
    crons = [entry["cron"] for entry in triggers["schedule"]]
    primary = [c for c in crons if c.startswith("17 ")]
    backup = [c for c in crons if c.startswith("47 ")]
    hours = lambda cs: sorted(int(h) for c in cs for h in c.split()[1].split(","))  # noqa: E731
    assert hours(primary) == [0, 6, 12, 18]
    assert hours(backup) == [h + 1 for h in hours(primary)]  # 1h30 depois de cada uma

    steps = workflow["jobs"]["routines"]["steps"]
    tasks = next(s for s in steps if s.get("name") == "Chama o monitor")["env"]["TASKS"]
    assert "'17 12 * * *'" in tasks and "'47 13 * * *'" in tasks  # relatório na principal e na reserva
    assert "17 12 * * *" in crons and "47 13 * * *" in crons
    # a reserva só segue se nada deu certo; todos os passos seguintes respeitam essa decisão
    first = steps[0]
    assert first["id"] == "backup" and "startsWith(github.event.schedule, '47 ')" in first["if"]
    assert all(s.get("if") == "steps.backup.outputs.skip != 'true'" for s in steps[1:])
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
