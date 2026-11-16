"""Fluxos do painel num navegador de verdade: entrada, saída, ações, erros, acessibilidade e layout."""
import re

from playwright.sync_api import expect

from tests.e2e.conftest import sign_in, slow_network
from tests.helpers import ADMIN, CHECK


def test_admin_sees_the_panel_without_errors(page, monitor_url):
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    expect(page.locator("#status")).to_contain_text("Aviso (70%)")
    expect(page.locator("#meters .meter")).to_have_count(2)
    expect(page.locator("#meters")).to_contain_text("78.600")
    expect(page.locator("#chart svg")).to_be_visible()
    expect(page.locator("#keys table")).to_have_attribute("aria-labelledby", "title-keys")
    # colunas de contagem (requests, sessions) nas tabelas de chaves, serviços e dia a dia
    expect(page.locator("#keys tbody tr").first).to_contain_text("70.740")
    expect(page.locator("#keys tbody tr").first).to_contain_text("420")
    expect(page.locator("#services tbody tr").first).to_contain_text("78.600")
    expect(page.locator("#daily tbody tr")).to_have_count(12)
    expect(page.locator("#daily tbody tr").last).to_contain_text("420")
    expect(page.locator("#status")).not_to_contain_text("null")
    # fontes do próprio monitor carregadas (a CSP não deixa vir de fora)
    loaded = page.evaluate("document.fonts.ready.then(() => [...document.fonts].filter(f => f.status === 'loaded')"
                           ".map(f => f.family + ' ' + f.weight))")
    assert {"Ubuntu 500", "Open Sans 400"} <= set(loaded)
    assert page.problems == []  # nenhum erro de JS, de CSP/Trusted Types ou do servidor


def test_choosing_a_project(page, monitor_url):
    sign_in(page, monitor_url)
    picker = page.locator("#project-select")
    expect(picker).to_have_value("")
    expect(picker.locator("option")).to_have_text(["Conta inteira", "Antigo (desativado)", "HydrantRouteSP"])
    expect(page.locator("#project-share")).to_be_hidden()
    expect(page.locator("[data-account-scope]:visible")).to_have_count(0)
    expect(page.locator("#keys tbody tr")).to_have_count(2)

    picker.select_option("HydrantRouteSP")
    share = page.locator("#project-share")
    expect(share).to_be_visible()
    expect(share).to_contain_text("HydrantRouteSP")
    expect(share).to_contain_text("70.740")  # requests da chave A
    expect(share).to_contain_text("70,7% da cota da conta")
    expect(page.locator("#keys tbody tr")).to_have_count(1)
    expect(page.locator("#meters")).to_contain_text("78.600")  # os medidores continuam sendo da conta
    expect(page.locator("[data-account-scope]:visible")).to_have_count(6)

    # a escolha fica lembrada no navegador
    page.reload()
    expect(page.locator("#project-share")).to_be_visible()
    expect(picker).to_have_value("HydrantRouteSP")

    picker.select_option("")
    expect(page.locator("#project-share")).to_be_hidden()
    expect(page.locator("#keys tbody tr")).to_have_count(2)
    assert page.problems == []


def test_remembered_project_that_no_longer_exists_falls_back_to_the_account(page, monitor_url):
    page.goto(f"{monitor_url}/panel")
    page.evaluate("localStorage.setItem('maptiler-monitor-project', 'Apagado')")
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    expect(page.locator("#project-select")).to_have_value("")
    expect(page.locator("#project-share")).to_be_hidden()
    assert page.evaluate("localStorage.getItem('maptiler-monitor-project')") is None
    assert page.problems == []


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
    slow_network(page, latency_ms=1200)
    page.click("#refresh-button")
    page.wait_for_timeout(150)
    page.click("#logout-button")
    page.wait_for_timeout(2500)  # as respostas atrasadas já chegaram
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#app")).to_be_hidden()


def test_signing_in_again_during_an_old_load_opens_the_panel(page, monitor_url):
    """Segunda auditoria R1/R2: entrar de novo enquanto a carga da sessão anterior ainda está em andamento
    abre o painel (antes não acontecia nada) e não traz o aviso da sessão anterior."""
    sign_in(page, monitor_url)
    expect(page.locator("#app")).to_be_visible()
    page.click("#check-button")  # deixa um aviso na tela
    expect(page.locator("#notice")).not_to_be_empty()
    slow_network(page, latency_ms=1200)
    page.click("#refresh-button")
    page.wait_for_timeout(150)
    page.click("#logout-button")
    page.fill("#token-input", ADMIN)
    page.click("#login-submit")
    expect(page.locator("#app")).to_be_visible(timeout=10_000)
    expect(page.locator("#meters .meter")).to_have_count(2)
    expect(page.locator("#notice")).to_be_empty()


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
    # um <main> visível por vez; o primeiro título do painel é o h1 (o menu não usa títulos)
    assert page.locator("main:visible").count() == 1
    first_heading = page.evaluate("document.querySelector('#app h1, #app h2, #app h3').tagName")
    assert first_heading == "H1"
    expect(page.locator("#section-daily summary h2")).to_have_accessible_name("Consumo dia a dia")


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
