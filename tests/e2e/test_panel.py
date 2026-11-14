"""Fluxos do painel num navegador de verdade: entrada, saída, ações, erros, acessibilidade e layout."""
import re

from playwright.sync_api import expect

from tests.e2e.conftest import sign_in
from tests.helpers import ADMIN, CHECK


def test_admin_sees_the_panel_without_errors(page, monitor_url):
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    expect(page.locator("#status")).to_contain_text("Aviso (70%)")
    expect(page.locator("#meters .meter")).to_have_count(2)
    expect(page.locator("#meters")).to_contain_text("78.600")
    expect(page.locator("#chart svg")).to_be_visible()
    expect(page.locator("#keys table")).to_have_attribute("aria-labelledby", "title-keys")
    expect(page.locator("#status")).not_to_contain_text("null")
    # fontes do próprio monitor carregadas (a CSP não deixa vir de fora)
    loaded = page.evaluate("document.fonts.ready.then(() => [...document.fonts].filter(f => f.status === 'loaded')"
                           ".map(f => f.family + ' ' + f.weight))")
    assert {"Ubuntu 500", "Open Sans 400"} <= set(loaded)
    assert page.problems == []  # nenhum erro de JS, de CSP/Trusted Types ou do servidor


def test_wrong_and_non_admin_tokens_stay_on_sign_in(page, monitor_url):
    sign_in(page, monitor_url, "x" * 40)
    expect(page.locator("#login-error")).to_have_text("O token não foi aceito. Entre de novo.")
    expect(page.locator("#app")).to_be_hidden()

    page.fill("#token-input", CHECK)
    page.click("#login-submit")
    expect(page.locator("#login-error")).to_have_text("Esse token não é o de administrador.")

    page.fill("#token-input", "token com espaço" + "a" * 20)
    page.click("#login-submit")
    expect(page.locator("#login-error")).to_contain_text("caracteres inválidos")


def test_session_survives_reload_and_ends_on_sign_out(page, monitor_url):
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    page.reload()
    expect(page.locator("#app")).to_be_visible()
    page.click("#logout-button")
    expect(page.locator("#login")).to_be_visible()
    page.reload()
    expect(page.locator("#login")).to_be_visible()
    assert page.evaluate("sessionStorage.length") == 0


def test_signing_out_during_a_load_does_not_reopen_the_panel(page, monitor_url):
    """Auditoria A1: a resposta que chega depois de sair não pode reabrir o painel com os dados."""
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions",
             {"offline": False, "latency": 1200, "downloadThroughput": -1, "uploadThroughput": -1})
    page.click("#refresh-button")
    page.wait_for_timeout(150)
    page.click("#logout-button")
    page.wait_for_timeout(2500)  # as respostas atrasadas já chegaram
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#app")).to_be_hidden()


def test_unexpected_response_shows_a_message(page, monitor_url):
    """Auditoria M1: dado fora do formato vira aviso na tela, não falha silenciosa."""
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    page.route("**/v1/alerts*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                    body='{"unexpected": true}'))
    page.click("#refresh-button")
    expect(page.locator("#notice")).to_contain_text("erro inesperado")
    page.problems.clear()  # o console.error é esperado aqui


def test_actions_report_their_result(page, monitor_url):
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    page.click("#check-button")  # a fixture verificou há pouco: o monitor pede para esperar
    expect(page.locator("#notice")).to_contain_text("Dá para verificar de novo em")
    page.once("dialog", lambda d: d.accept())
    page.click("#test-button")
    expect(page.locator("#notice")).to_contain_text("e-mail: enviado; WhatsApp: não configurado")
    page.once("dialog", lambda d: d.dismiss())  # cancelar o teste não manda nada
    page.click("#test-button")
    expect(page.locator("#notice")).to_contain_text("e-mail: enviado")  # aviso anterior continua


def test_accessibility_basics(page, monitor_url):
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    # região de aviso sempre presente, para leitores de tela anunciarem as mudanças
    notice = page.locator("#notice")
    expect(notice).to_have_attribute("role", "status")
    assert notice.evaluate("el => getComputedStyle(el).display") != "none"
    # o link de pulo é o primeiro foco e leva ao conteúdo
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#content")).to_be_focused()
    # medidores com texto para leitor de tela; a caixa de erro não usa role=alert
    expect(page.locator(".meter__bar").first).to_have_attribute("aria-valuetext", re.compile(r"78,6% da cota"))
    assert page.locator("#status [role=alert]").count() == 0
    # um <main> visível por vez; estrutura de títulos sem pular nível no conteúdo
    assert page.locator("main:visible").count() == 1
    expect(page.locator("#section-daily summary h2")).to_have_text("Consumo dia a dia")


def test_phone_layout_has_no_horizontal_scroll(browser, monitor_url):
    context = browser.new_context(viewport={"width": 390, "height": 800}, color_scheme="dark")
    pg = context.new_page()
    try:
        sign_in(pg, monitor_url, ADMIN)
        expect(pg.locator("#meters .meter")).to_have_count(2)
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
        width = pg.locator("#chart svg").evaluate("el => el.getBoundingClientRect().width")
        assert width <= 390
    finally:
        context.close()
