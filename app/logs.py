"""Logs estruturados: uma linha JSON por evento, com campos fáceis de filtrar (event, kind, duration_ms...)."""
import datetime as dt
import json
import logging
import sys

# Atributos que todo LogRecord já tem; o resto veio de extra={...} e entra no JSON.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                # Um campo extra com o nome de um campo-base (ts, level...) não pode apagar o original.
                data[f"extra_{key}" if key in data else key] = value
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # O httpx registra cada requisição em INFO. A credencial vai só no cabeçalho (que ele não registra), mas o
    # log fica mais limpo sem essas linhas: as consultas ao MapTiler já têm o próprio evento.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
