from datetime import date
from decimal import Decimal as D

import pytest

import copiloto_mei as n

NFE = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe123">
<ide><nNF>7</nNF><dhEmi>2026-03-15T10:00:00-03:00</dhEmi></ide>
<dest><xNome>Cliente Teste</xNome></dest>
<total><ICMSTot><vNF>1500.50</vNF></ICMSTot></total></infNFe></NFe></nfeProc>"""

ABRASF = """<ListaNfse>
<CompNfse><Nfse><InfNfse><Numero>1</Numero><DataEmissao>2026-04-02T09:00:00</DataEmissao>
<Servico><Valores><ValorServicos>1000.00</ValorServicos><ValorLiquidoNfse>950.00</ValorLiquidoNfse></Valores></Servico>
<TomadorServico><RazaoSocial>Tomador A</RazaoSocial></TomadorServico></InfNfse></Nfse></CompNfse>
<CompNfse><Nfse><InfNfse><Numero>2</Numero><DataEmissao>2026-04-20T09:00:00</DataEmissao>
<Servico><Valores><ValorServicos>2000.00</ValorServicos></Valores></Servico></InfNfse></Nfse></CompNfse>
</ListaNfse>"""


def test_parse_valor_formatos():
    assert n.parse_valor("1500.00") == D("1500.00")
    assert n.parse_valor("1.500,00") == D("1500.00")
    assert n.parse_valor("R$ 1.500,50") == D("1500.50")
    assert n.parse_valor("1.500") == D("1500")
    assert n.parse_valor("800") == D("800")


@pytest.mark.parametrize("ruim", ["", "abc", "-5", "NaN", "Infinity"])
def test_parse_valor_rejeita(ruim):
    with pytest.raises(ValueError):
        n.parse_valor(ruim)


def test_parse_data_formatos():
    assert n.parse_data("2026-03-15") == date(2026, 3, 15)
    assert n.parse_data("2026-03-15T10:00:00-03:00") == date(2026, 3, 15)
    assert n.parse_data("15/03/2026") == date(2026, 3, 15)
    with pytest.raises(ValueError):
        n.parse_data("32/13/2026")
    with pytest.raises(ValueError):
        n.parse_data("ontem")


def test_nfe_xml():
    ns, av = n.parse_xml(NFE, "nfe.xml")
    assert av == [] and len(ns) == 1
    assert ns[0].data == date(2026, 3, 15) and ns[0].valor == D("1500.50")
    assert ns[0].descricao == "Cliente Teste" and ns[0].chave == "NFe123"


def test_nfse_abrasf_com_varias_notas_usa_valor_bruto():
    ns, av = n.parse_xml(ABRASF, "lote.xml")
    assert [x.valor for x in ns] == [D("1000.00"), D("2000.00")]  # não o líquido (950)
    assert ns[0].descricao == "Tomador A"
    assert len({x.id for x in ns}) == 2


def test_xml_com_doctype_e_recusado():
    ns, av = n.parse_xml('<!DOCTYPE x [<!ENTITY a "b">]><x/>', "ruim.xml")
    assert ns == [] and "segurança" in av[0]


def test_xml_quebrado_e_sem_valor_viram_avisos():
    assert n.parse_xml("<a><b></a>", "q.xml")[1]
    ns, av = n.parse_xml("<infNFe><ide><dhEmi>2026-01-01</dhEmi></ide></infNFe>", "s.xml")
    assert ns == [] and "sem data ou valor" in av[0]


def test_csv_br_com_cabecalho():
    ns, av = n.parse_csv("data;valor;descricao\n10/01/2026;1.800,00;Logo\n22/01/2026;2.200,00;Cartaz\n", "a.csv")
    assert av == [] and [x.valor for x in ns] == [D("1800.00"), D("2200.00")]
    assert ns[0].descricao == "Logo"


def test_csv_ingles_sem_cabecalho():
    ns, _ = n.parse_csv("2026-02-01,500.00,Teste\n2026-02-02,300,Outro\n", "b.csv")
    assert [x.valor for x in ns] == [D("500.00"), D("300")]


def test_csv_linha_ruim_vira_aviso_e_nao_derruba():
    ns, av = n.parse_csv("data;valor\n10/01/2026;100\nxx;yy\n", "c.csv")
    assert len(ns) == 1 and "linha 3" in av[0]


def test_csv_cabecalho_sem_colunas_certas():
    ns, av = n.parse_csv("foo;data\n1;2\n", "d.csv")
    assert ns == [] and "coluna de data e uma de valor" in av[0]


def test_pdf_nao_suportado():
    ns, av = n.parse_arquivo("nota.pdf", "%PDF-1.4")
    assert ns == [] and "PDF" in av[0]


def test_mesclar_remove_repetidas_mas_mantem_linhas_iguais_do_mesmo_csv():
    ns, _ = n.parse_csv("data;valor\n10/01/2026;100\n10/01/2026;100\n", "e.csv")
    assert len(ns) == 2  # duas vendas iguais no mesmo dia são legítimas
    lista, av = n.mesclar(ns, ns)  # reenviar o mesmo arquivo não duplica
    assert len(lista) == 2 and "repetida" in av[0]


def test_agregar_por_mes_ignora_outro_ano():
    ns = [n.Nota(date(2026, 1, 5), D(100)), n.Nota(date(2026, 1, 9), D(50)),
          n.Nota(date(2025, 12, 31), D(999))]
    por_mes, av = n.agregar_por_mes(ns, 2026)
    assert por_mes == {1: D(150)} and "fora de 2026" in av[0]
