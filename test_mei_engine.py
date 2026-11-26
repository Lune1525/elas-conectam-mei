from decimal import Decimal as D

import pytest

from copiloto_mei import (
    Lancamento, limite_do_ano, meses_ativos, preco_minimo, resumo_caixa,
    simular_excesso, status_teto,
)


# --- limite proporcional
def test_ano_completo():
    assert limite_do_ano() == D("81000")


def test_abertura_em_setembro_conta_4_meses():
    assert meses_ativos(9) == 4
    assert limite_do_ano(9) == D("27000")


def test_abertura_em_dezembro_conta_1_mes():
    assert limite_do_ano(12) == D("6750")


def test_mes_abertura_invalido():
    with pytest.raises(ValueError):
        meses_ativos(13)


# --- status do teto
def test_status_ok():
    s = status_teto(20000, mes_atual=6)
    assert s.faixa_atual == "ok"
    assert s.percentual_usado == D("24.69")
    assert s.margem_restante == D("61000.00")
    assert s.meses_restantes == 6
    assert s.media_mensal_possivel == D("10166.67")


def test_projecao_passa_do_teto_mesmo_estando_dentro():
    s = status_teto(50000, mes_atual=6)  # 50k em 6 meses => 100k no ano
    assert s.faixa_atual == "ok"
    assert s.projecao_anual == D("100000.00")
    assert s.faixa_projetada == "excesso_acima_20"


def test_faixas_atuais():
    assert status_teto(60000, 12).faixa_atual == "atencao"
    assert status_teto(75000, 12).faixa_atual == "proximo"
    assert status_teto(90000, 12).faixa_atual == "excesso_ate_20"
    assert status_teto(97200, 12).faixa_atual == "excesso_ate_20"
    assert status_teto(97201, 12).faixa_atual == "excesso_acima_20"


def test_status_mei_aberto_no_ano():
    s = status_teto(10000, mes_atual=10, mes_abertura=9)  # set e out
    assert s.limite == D("27000.00")
    assert s.projecao_anual == D("20000.00")


def test_status_valida_entradas():
    with pytest.raises(ValueError):
        status_teto(-1, 5)
    with pytest.raises(ValueError):
        status_teto(1000, 13)
    with pytest.raises(ValueError):
        status_teto(1000, 3, mes_abertura=6)


# --- simulação de excesso
def test_dentro_do_teto():
    r = simular_excesso(70000)
    assert r.faixa == "dentro_do_teto" and r.imposto_estimado == D("0.00")


def test_excesso_ate_20():
    r = simular_excesso(90000)  # excedente 9.000 x 6% = 540
    assert r.faixa == "excesso_ate_20"
    assert r.excesso == D("9000.00")
    assert r.imposto_estimado == D("540.00")


def test_excesso_acima_20_retroativo():
    r = simular_excesso(100000)  # 100k x 6% = 6000 - (86,05 x 12 = 1032,60)
    assert r.faixa == "excesso_acima_20"
    assert r.imposto_estimado == D("4967.40")


def test_excesso_nunca_negativo():
    r = simular_excesso(97500, aliquota_estimada=D("0.01"))
    assert r.imposto_estimado == D("0.00")


# --- caixa PF/PJ
def test_resumo_caixa_e_mistura():
    ls = [
        Lancamento("entrada", "PF", "negocio", 800, "cliente X na conta pessoal"),
        Lancamento("entrada", "PJ", "negocio", 1200),
        Lancamento("saida", "PJ", "negocio", 300, "licença de software"),
        Lancamento("saida", "PJ", "pessoal", 150, "mercado pago da conta PJ"),
        Lancamento("saida", "PF", "pessoal", 400),
    ]
    r = resumo_caixa(ls)
    assert r["entradas_negocio"] == D("2000.00")
    assert r["saldo_negocio"] == D("1700.00")
    assert r["valor_misturado"] == D("950.00")  # 800 + 150
    assert r["qtd_misturados"] == 2
    assert r["faturamento_para_teto"] == D("2000.00")


def test_lancamento_invalido():
    with pytest.raises(ValueError):
        Lancamento("entrada", "PF", "negocio", 0)
    with pytest.raises(ValueError):
        Lancamento("troca", "PF", "negocio", 10)


# --- preço mínimo
def test_preco_minimo_basico():
    # (500 fixos + 3000 pró-labore + 86,05 DAS) / 10 = 358,605 + 20 variável
    r = preco_minimo(500, 3000, 10, custo_variavel_por_servico=20)
    assert r.preco_minimo == D("378.61")
    assert r.preco_sugerido == D("416.47")
    assert r.composicao["das_por_servico"] == D("8.61")


def test_preco_minimo_sem_servicos():
    with pytest.raises(ValueError):
        preco_minimo(500, 3000, 0)


def test_preco_minimo_valores_negativos():
    with pytest.raises(ValueError):
        preco_minimo(-1, 3000, 5)


# --- projeção com histórico mensal
from copiloto_mei import projetar_com_historico  # noqa: E402


def test_projecao_dentro_do_teto():
    r = projetar_com_historico({m: D(5000) for m in range(1, 7)}, 6)
    assert r.acumulado == D("30000.00")
    assert len(r.cenarios) == 2
    assert all(c.mes_estouro is None for c in r.cenarios)
    assert r.cenarios[0].projecao_anual == D("60000.00")


def test_projecao_estoura_em_setembro():
    r = projetar_com_historico({m: D(10000) for m in range(1, 7)}, 6)
    c = r.cenarios[0]
    assert c.mes_estouro == 9 and c.mes_estouro_20 == 10  # 90k > 81k; 100k > 97,2k
    assert c.faixa == "excesso_acima_20"


def test_cenario_recente_mais_pessimista():
    por_mes = {4: D(20000), 5: D(20000), 6: D(20000)}  # acumulado 60k, geral 10k/mês
    r = projetar_com_historico(por_mes, 6)
    geral, recente = r.cenarios
    assert geral.media_mensal == D("10000.00") and geral.mes_estouro == 9
    assert recente.media_mensal == D("20000.00") and recente.mes_estouro == 8


def test_projecao_ja_passou_do_teto():
    r = projetar_com_historico({1: D(50000), 2: D(40000)}, 2)
    assert r.cenarios[0].mes_estouro == 2 and r.faixa_atual == "excesso_ate_20"


def test_projecao_poucos_meses_tem_um_cenario_so():
    assert len(projetar_com_historico({1: D(1000), 2: D(1000)}, 2).cenarios) == 1


def test_projecao_mei_aberto_no_ano_tem_limite_proporcional():
    # Abriu em setembro: limite 27.000. 6k/mês => dez = 24k (dentro); 7k/mês => dez = 28k (passa).
    r = projetar_com_historico({9: D(6000), 10: D(6000)}, 10, mes_abertura=9)
    assert r.limite == D("27000.00")
    assert r.cenarios[0].projecao_anual == D("24000.00") and r.cenarios[0].mes_estouro is None
    r2 = projetar_com_historico({9: D(7000), 10: D(7000)}, 10, mes_abertura=9)
    assert r2.cenarios[0].projecao_anual == D("28000.00") and r2.cenarios[0].mes_estouro == 12
