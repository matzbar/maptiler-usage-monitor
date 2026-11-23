FROM python:3.15-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Usuário sem privilégios: a API não roda como root. Os arquivos ficam com o root (só leitura para a API).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY app/ app/
COPY scripts/ scripts/
USER app
EXPOSE 8000
# O Docker marca o contêiner como "unhealthy" se a API parar de responder.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"
# 1 processo: o controle de "uma verificação por vez" e o limite de requisições ficam na memória dele.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT} --no-server-header"]
