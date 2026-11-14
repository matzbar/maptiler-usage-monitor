"""Cabeçalhos de segurança aplicados a todas as respostas."""

# A API só devolve JSON: nada pode ser carregado nem embutido.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# /docs (Swagger UI, gerado pelo FastAPI) carrega o próprio JavaScript e CSS do jsDelivr e tem um script inline.
DOCS_CSP = "; ".join([
    "default-src 'none'",
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net",
    "img-src 'self' data: https://fastapi.tiangolo.com",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
])

# Painel do administrador: script, estilo e fontes só do próprio servidor, sem nada inline nem de outros sites;
# os dados vêm da própria API (connect-src 'self'). Trusted Types: o navegador recusa qualquer escrita de HTML
# como texto (innerHTML e afins); o painel só cria elementos e texto, então nada nele depende disso.
PANEL_CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "require-trusted-types-for 'script'",
    "trusted-types 'none'",
])

COMMON = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Nenhuma página daqui usa câmera, microfone, localização, pagamento ou USB.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
HSTS = "max-age=31536000"


def headers_for(path: str, forwarded_proto: str) -> dict[str, str]:
    headers = dict(COMMON)
    if path == "/docs":
        headers["Content-Security-Policy"] = DOCS_CSP
    elif path == "/panel" or path.startswith("/panel/"):
        headers["Content-Security-Policy"] = PANEL_CSP
        # O navegador pode guardar, mas confere com o servidor antes de usar (ETag): depois de uma atualização do
        # monitor, nunca mistura módulos JavaScript antigos e novos.
        headers["Cache-Control"] = "no-cache"
    else:
        headers["Content-Security-Policy"] = API_CSP
    if path.startswith("/v1/"):
        headers["Cache-Control"] = "no-store"  # consumo da conta: nenhum proxy ou navegador guarda cópia
    if forwarded_proto.split(",")[0].strip().lower() == "https":
        headers["Strict-Transport-Security"] = HSTS
    return headers
