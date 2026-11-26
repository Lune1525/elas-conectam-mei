from decimal import Decimal as D

import copiloto_mei as apoio
from copiloto_mei import FERRAMENTAS, Agente, Sessao, extrair_valores, responder_offline


def test_extrair_valores():
    assert extrair_valores("faturei R$ 45.000") == [D("45000")]
    assert extrair_valores("uns 52 mil") == [D("52000")]
    assert extrair_valores("recebi 800,50") == [D("800.50")]


def test_ferramentas_tem_schema_valido():
    nomes = {f["name"] for f in FERRAMENTAS}
    assert nomes == {"verificar_teto", "simular_migracao_me", "registrar_lancamento",
                     "resumo_caixa", "calcular_preco_minimo", "sugerir_apoio", "analisar_notas"}
    for f in FERRAMENTAS:
        assert f["input_schema"]["type"] == "object"


def test_dispatcher_teto_devolve_numeros_do_motor():
    r = Sessao().executar("verificar_teto", {"faturado": 20000, "mes_atual": 6})
    assert r["percentual_usado"] == "24.69"
    assert r["faixa_atual"] == "ok"


def test_dispatcher_erro_vira_mensagem():
    s = Sessao()
    assert "erro" in s.executar("verificar_teto", {"faturado": -5, "mes_atual": 6})
    assert "erro" in s.executar("verificar_teto", {"faturado": 100})  # falta mes_atual
    assert "erro" in s.executar("ferramenta_inventada", {})


def test_simulacao_usa_limite_proporcional():
    # Abriu em setembro: limite 27.000. 30.000 passa (11,11%), com teto cheio nem passaria.
    s = Sessao()
    r = s.executar("simular_migracao_me", {"faturamento_total": 30000, "mes_abertura": 9})
    assert r["faixa"] == "excesso_ate_20"
    assert r["excesso"] == "3000.00"
    r2 = s.executar("simular_migracao_me", {"faturamento_total": 30000})
    assert r2["faixa"] == "dentro_do_teto"


def test_simulacao_traz_aviso_de_aproximacao():
    r = Sessao().executar("simular_migracao_me", {"faturamento_total": 100000})
    assert any("contador" in p for p in r["premissas"])


def test_caixa_pela_sessao():
    s = Sessao()
    assert "erro" in s.executar("resumo_caixa", {})
    r = s.executar("registrar_lancamento", {"tipo": "entrada", "conta": "PF", "natureza": "negocio", "valor": 800})
    assert r["misturado"] is True
    assert s.executar("resumo_caixa", {})["valor_misturado"] == "800.00"


def test_apoio_acolhe_e_sugere_uma_coisa():
    r = apoio.acolher_e_sugerir("isolamento")
    assert r["acolhimento"] and r["sugestao"]["nome"]
    assert isinstance(r["sugestao"], dict)  # uma sugestão, não lista


def test_offline_desabafo_nao_vira_tarefa():
    resp = responder_offline("Estou me sentindo sozinha nisso", Sessao())
    assert "R$" not in resp  # nada de números ou cobrança
    assert "Sem pressa" in resp


def test_offline_teto_pede_dado_que_falta():
    assert "quanto você faturou" in responder_offline("Será que vou estourar o teto?", Sessao())


def test_offline_teto_com_numeros():
    resp = responder_offline("Faturei 20 mil até junho, estou perto do teto?", Sessao())
    assert "24,69%" in resp or "24.69%" in resp
    assert "R$ 81.000,00" in resp


def test_offline_preco():
    resp = responder_offline("Quanto cobrar? custos 500, retirar 3000, 10 serviços", Sessao())
    assert "R$ 358,61" in resp  # (500+3000+86,05)/10, sem custo variável


# --- loop com LLM, usando um cliente falso (sem rede)
class _Bloco:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ClienteFalso:
    def __init__(self):
        self.chamadas = 0
        self.messages = self

    def create(self, **kw):
        self.chamadas += 1
        if self.chamadas == 1:
            return _Bloco(stop_reason="tool_use", content=[_Bloco(
                type="tool_use", id="t1", name="verificar_teto",
                input={"faturado": 20000, "mes_atual": 6})])
        ultima = kw["messages"][-1]["content"][0]
        assert ultima["tool_use_id"] == "t1" and "24.69" in ultima["content"]
        return _Bloco(stop_reason="end_turn", content=[_Bloco(type="text", text="Você usou 24,69% do teto.")])


def test_loop_llm_executa_ferramenta_e_responde():
    ag = Agente(cliente=_ClienteFalso())
    assert ag.conversar("Faturei 20 mil até junho") == "Você usou 24,69% do teto."


# --- notas fiscais
from datetime import date  # noqa: E402

from copiloto_mei import Nota  # noqa: E402


def _sessao_com_notas():
    s = Sessao()
    s.notas = [Nota(date(2026, m, 10), D(10000), chave=f"n{m}") for m in range(1, 7)]
    return s


def test_analisar_notas_sem_notas():
    assert "erro" in Sessao().executar("analisar_notas", {})


def test_analisar_notas_estouro():
    r = _sessao_com_notas().executar("analisar_notas", {})
    assert r["acumulado"] == "60000.00" and r["mes_atual"] == 6
    assert r["cenarios"][0]["mes_estouro"] == 9 and r["qtd_notas"] == 6


def test_analisar_notas_respeita_mes_escolhido():
    r = _sessao_com_notas().executar("analisar_notas", {"mes_atual": 3})
    assert r["acumulado"] == "30000.00" and r["mes_atual"] == 3


def test_analisar_notas_ignora_outro_ano_com_aviso():
    s = _sessao_com_notas()
    s.notas.append(Nota(date(2025, 12, 1), D(500), chave="velha"))
    assert "fora de 2026" in s.executar("analisar_notas", {})["avisos"][0]


def test_offline_usa_notas_sem_precisar_de_numeros():
    resp = responder_offline("Vou bater o teto se eu continuar assim?", _sessao_com_notas())
    assert "R$ 60.000,00" in resp and "setembro" in resp
    assert "só as notas enviadas" in resp
