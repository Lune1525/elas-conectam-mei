"""Testes das rotas novas do sistema: caixa, preço, simulador, listagem e chat."""
import json
import threading
import urllib.request

import pytest

import copiloto_mei as c


@pytest.fixture(scope="module")
def url():
    srv = c.criar_servidor(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, caminho, corpo):
    req = urllib.request.Request(url + caminho, json.dumps(corpo).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_caixa_registrar_resumo_e_mistura(url):
    sid = "caixa-sessao-1"
    assert post(url, "/api/caixa", {"sessao": sid})[1]["lancamentos"] == []
    s, r = post(url, "/api/caixa/registrar", {"sessao": sid, "tipo": "entrada", "conta": "PF", "natureza": "negocio",
                                              "valor": "800,00", "descricao": "Cliente Bia"})
    assert s == 200 and r["lancamentos"][0]["misturado"] is True
    s, r = post(url, "/api/caixa/registrar", {"sessao": sid, "tipo": "saida", "conta": "PJ", "natureza": "negocio", "valor": "120"})
    assert r["resumo"]["entradas_negocio"] == "800.00" and r["resumo"]["saldo_negocio"] == "680.00"
    assert r["resumo"]["valor_misturado"] == "800.00" and r["resumo"]["qtd_misturados"] == 1


def test_caixa_remover_exemplo_e_limpar(url):
    sid = "caixa-sessao-2"
    s, r = post(url, "/api/caixa/exemplo", {"sessao": sid})
    assert len(r["lancamentos"]) == 6 and r["resumo"]["valor_misturado"] == "950.00"
    assert len(post(url, "/api/caixa/exemplo", {"sessao": sid})[1]["lancamentos"]) == 6  # não duplica
    s, r = post(url, "/api/caixa/remover", {"sessao": sid, "indice": 0})
    assert len(r["lancamentos"]) == 5
    assert post(url, "/api/caixa/remover", {"sessao": sid, "indice": 99})[0] == 400
    assert post(url, "/api/caixa/remover", {"sessao": sid, "indice": True})[0] == 400
    assert post(url, "/api/caixa/limpar", {"sessao": sid})[1]["lancamentos"] == []


def test_caixa_valida_entrada(url):
    base = {"sessao": "caixa-sessao-3", "tipo": "entrada", "conta": "PF", "natureza": "negocio", "valor": "10"}
    assert post(url, "/api/caixa/registrar", {**base, "valor": "0"})[0] == 400
    assert post(url, "/api/caixa/registrar", {**base, "valor": "abc"})[0] == 400
    assert post(url, "/api/caixa/registrar", {**base, "conta": "XX"})[0] == 400
    assert post(url, "/api/caixa/registrar", {**base, "tipo": "troca"})[0] == 400


def test_preco_bate_com_o_motor(url):
    s, r = post(url, "/api/preco", {"sessao": "preco-sessao-1", "custos_fixos": "500,00", "pro_labore": "3.000,00",
                                    "servicos": "10", "custo_variavel": "", "margem": "10"})
    assert s == 200 and r["preco_minimo"] == "358.61" and r["preco_sugerido"] == "394.47"
    assert r["composicao"]["das_por_servico"] == "8.61"


def test_preco_margem_e_atividade(url):
    base = {"sessao": "preco-sessao-2", "custos_fixos": "500", "pro_labore": "3000", "servicos": "10"}
    s, r = post(url, "/api/preco", {**base, "margem": "0", "tipo_atividade": "comercio"})
    assert r["preco_minimo"] == r["preco_sugerido"] == "358.21"  # DAS comércio 82,05
    assert post(url, "/api/preco", {**base, "tipo_atividade": "outro"})[0] == 400


def test_preco_entradas_invalidas(url):
    base = {"sessao": "preco-sessao-3", "custos_fixos": "500", "pro_labore": "3000", "servicos": "10"}
    assert post(url, "/api/preco", {**base, "servicos": "0"})[0] == 400
    assert post(url, "/api/preco", {**base, "servicos": "dez"})[0] == 400
    assert "serviços" in post(url, "/api/preco", {**base, "servicos": "dez"})[1]["erro"]
    assert post(url, "/api/preco", {**base, "custos_fixos": ""})[0] == 400


def test_simulador(url):
    sid = "sim-sessao-1"
    s, r = post(url, "/api/simular", {"sessao": sid, "faturamento_total": "90.000,00", "aliquota": "6"})
    assert s == 200 and r["faixa"] == "excesso_ate_20" and r["excesso"] == "9000.00" and r["imposto_estimado"] == "540.00"
    s, r = post(url, "/api/simular", {"sessao": sid, "faturamento_total": "100000"})
    assert r["faixa"] == "excesso_acima_20" and r["imposto_estimado"] == "4967.40"
    s, r = post(url, "/api/simular", {"sessao": sid, "faturamento_total": "30000", "mes_abertura": "9"})
    assert r["faixa"] == "excesso_ate_20"  # limite proporcional de R$ 27.000
    assert post(url, "/api/simular", {"sessao": sid, "faturamento_total": "1", "aliquota": "500"})[0] == 400
    assert post(url, "/api/simular", {"sessao": sid, "faturamento_total": ""})[0] == 400
    assert post(url, "/api/simular", {"sessao": sid, "faturamento_total": "1", "mes_abertura": "13"})[0] == 400


def test_listar_notas_e_chat_limpar(url):
    sid = "notas-sessao-1"
    assert post(url, "/api/notas/listar", {"sessao": sid})[1]["notas"] == []
    post(url, "/api/exemplo", {"sessao": sid})
    assert len(post(url, "/api/notas/listar", {"sessao": sid})[1]["notas"]) == 17
    assert post(url, "/api/chat/limpar", {"sessao": sid})[1] == {"ok": True}


def test_arquivo_estatico_ausente_nao_derruba(url, monkeypatch, tmp_path):
    monkeypatch.setattr(c, "BASE", tmp_path)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/app.js")
    assert e.value.code == 404
