"""Copiloto MEI — Aplicação consolidada (motor determinístico, notas, apoio, agente e servidor).

Modos de execução:
  python copiloto_mei.py                  # inicia servidor web local em http://127.0.0.1:8000
  python copiloto_mei.py --porta 8080     # inicia servidor em porta customizada
  python copiloto_mei.py --cli            # chat interativo no terminal (LLM ou offline)
  python copiloto_mei.py --cli --offline  # chat interativo no terminal (apenas regras)
  python copiloto_mei.py --roteiro        # executa jornada de demo da Marina e encerra
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import sys
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

D = Decimal

# ==============================================================================
# 1. REGRAS FISCAIS E UTILITÁRIOS DE FORMATAÇÃO
# ==============================================================================

REGRAS_2026 = {
    "ano": 2026,
    "teto_anual": D("81000"),
    "teto_mensal": D("6750"),
    "tolerancia_excesso": D("0.20"),
    "das_mensal": {
        "comercio": D("82.05"),
        "servicos": D("86.05"),
        "misto": D("87.05"),
    },
    "fonte": "Agência Brasil / Sebrae / Receita Federal (conferido em nov/2026)",
}

LIMITE_ATENCAO = D("0.70")
LIMITE_PROXIMO = D("0.90")


def _d(valor) -> Decimal:
    return valor if isinstance(valor, Decimal) else D(str(valor))


def _r2(valor: Decimal) -> Decimal:
    return valor.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def fmt_brl(valor) -> str:
    """Formata valor monetário como R$ 1.234,56."""
    s = f"{_r2(_d(valor)):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(valor) -> str:
    """Formata percentual como 6,00%."""
    return f"{_r2(_d(valor)):.2f}".replace(".", ",") + "%"


def _sem_acento(t: str) -> str:
    return unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().lower()


# ==============================================================================
# 2. MOTOR DETERMINÍSTICO (TETO, TRANSIÇÃO ME, CAIXA PF/PJ, PREÇO E PROJEÇÃO)
# ==============================================================================

def meses_ativos(mes_abertura: Optional[int] = None) -> int:
    """Meses de atividade no ano. Abertura em setembro => 4 (set, out, nov, dez)."""
    if mes_abertura is None:
        return 12
    if not 1 <= mes_abertura <= 12:
        raise ValueError("mes_abertura deve estar entre 1 e 12")
    return 12 - mes_abertura + 1


def limite_do_ano(mes_abertura: Optional[int] = None, regras=REGRAS_2026) -> Decimal:
    return regras["teto_mensal"] * meses_ativos(mes_abertura)


@dataclass
class StatusTeto:
    faturado: Decimal
    limite: Decimal
    limite_com_tolerancia: Decimal
    percentual_usado: Decimal
    margem_restante: Decimal
    meses_restantes: int
    media_mensal_possivel: Decimal
    projecao_anual: Decimal
    faixa_atual: str
    faixa_projetada: str
    premissas: list = field(default_factory=list)


def _faixa(valor: Decimal, limite: Decimal, tolerancia: Decimal) -> str:
    if valor > limite * (1 + tolerancia):
        return "excesso_acima_20"
    if valor > limite:
        return "excesso_ate_20"
    pct = valor / limite
    if pct >= LIMITE_PROXIMO:
        return "proximo"
    if pct >= LIMITE_ATENCAO:
        return "atencao"
    return "ok"


def status_teto(faturado, mes_atual: int, mes_abertura: Optional[int] = None,
                regras=REGRAS_2026) -> StatusTeto:
    """Panorama do teto. `mes_atual` é o último mês já fechado (1 a 12)."""
    faturado = _d(faturado)
    if faturado < 0:
        raise ValueError("faturado não pode ser negativo")
    if not 1 <= mes_atual <= 12:
        raise ValueError("mes_atual deve estar entre 1 e 12")
    inicio = mes_abertura or 1
    if mes_atual < inicio:
        raise ValueError("mes_atual não pode ser anterior ao mês de abertura")

    limite = limite_do_ano(mes_abertura, regras)
    tol = regras["tolerancia_excesso"]
    decorridos = mes_atual - inicio + 1
    restantes = 12 - mes_atual
    projecao = _r2(faturado / decorridos * meses_ativos(mes_abertura))
    margem = max(limite - faturado, D("0"))
    media_possivel = _r2(margem / restantes) if restantes else D("0")

    return StatusTeto(
        faturado=_r2(faturado),
        limite=_r2(limite),
        limite_com_tolerancia=_r2(limite * (1 + tol)),
        percentual_usado=_r2(faturado / limite * 100),
        margem_restante=_r2(margem),
        meses_restantes=restantes,
        media_mensal_possivel=media_possivel,
        projecao_anual=projecao,
        faixa_atual=_faixa(faturado, limite, tol),
        faixa_projetada=_faixa(projecao, limite, tol),
        premissas=[
            f"Projeção supõe o mesmo faturamento médio dos {decorridos} meses já fechados.",
            f"Limite considera {meses_ativos(mes_abertura)} meses de atividade em {regras['ano']}: {fmt_brl(limite)}.",
        ],
    )


@dataclass
class SimulacaoME:
    faturamento_total: Decimal
    faixa: str
    excesso: Decimal
    percentual_excesso: Decimal
    imposto_estimado: Decimal
    explicacao: str
    premissas: list


def simular_excesso(faturamento_total, aliquota_estimada=D("0.06"),
                    tipo_atividade: str = "servicos",
                    mes_abertura: Optional[int] = None,
                    regras=REGRAS_2026) -> SimulacaoME:
    """Estimativa didática do custo de passar do teto. NÃO substitui contador."""
    total = _d(faturamento_total)
    aliq = _d(aliquota_estimada)
    n_meses = meses_ativos(mes_abertura)
    limite = limite_do_ano(mes_abertura, regras)
    tol = regras["tolerancia_excesso"]
    das_ano = regras["das_mensal"][tipo_atividade] * n_meses
    excesso = max(total - limite, D("0"))
    pct = _r2(excesso / limite * 100)
    premissas = [
        f"Considera {n_meses} mês(es) de atividade em {regras['ano']}: limite de {fmt_brl(limite)}.",
        f"Alíquota do Simples estimada em {fmt_pct(aliq * 100)} (premissa, depende da atividade e do fator R).",
        "É uma aproximação para você se planejar. Não substitui um contador: "
        "confirme os valores com um profissional ou no Portal do Empreendedor.",
    ]
    if total <= limite:
        return SimulacaoME(_r2(total), "dentro_do_teto", D("0.00"), D("0.00"), D("0.00"),
                           "Você está dentro do teto. Nada muda por enquanto.", premissas)
    if total <= limite * (1 + tol):
        imposto = _r2(excesso * aliq)
        return SimulacaoME(
            _r2(total), "excesso_ate_20", _r2(excesso), pct, imposto,
            "Você passou do teto em até 20%. Continua MEI até dezembro, paga um imposto "
            "complementar sobre o que excedeu e vira Microempresa (ME) em janeiro.",
            premissas)
    imposto = _r2(max(total * aliq - das_ano, D("0")))
    return SimulacaoME(
        _r2(total), "excesso_acima_20", _r2(excesso), pct, imposto,
        "Você passou de 20% do teto. A mudança para ME vale desde 1º de janeiro deste ano, "
        "e é preciso comunicar até o último dia útil do mês seguinte ao do excesso.",
        premissas)


@dataclass
class Lancamento:
    tipo: str
    conta: str
    natureza: str
    valor: Decimal
    descricao: str = ""

    def __post_init__(self):
        if self.tipo not in ("entrada", "saida"):
            raise ValueError("tipo deve ser 'entrada' ou 'saida'")
        if self.conta not in ("PF", "PJ"):
            raise ValueError("conta deve ser 'PF' ou 'PJ'")
        if self.natureza not in ("negocio", "pessoal"):
            raise ValueError("natureza deve ser 'negocio' ou 'pessoal'")
        self.valor = _d(self.valor)
        if self.valor <= 0:
            raise ValueError("valor deve ser positivo")

    @property
    def misturado(self) -> bool:
        return (self.conta == "PF") != (self.natureza == "pessoal")


def resumo_caixa(lancamentos: list[Lancamento]) -> dict:
    """Totais do negócio, do pessoal e quanto do dinheiro passou na conta errada."""
    def soma(filtro):
        return _r2(sum((l.valor for l in lancamentos if filtro(l)), D("0")))

    entradas_neg = soma(lambda l: l.tipo == "entrada" and l.natureza == "negocio")
    saidas_neg = soma(lambda l: l.tipo == "saida" and l.natureza == "negocio")
    return {
        "entradas_negocio": entradas_neg,
        "saidas_negocio": saidas_neg,
        "saldo_negocio": _r2(entradas_neg - saidas_neg),
        "entradas_pessoais": soma(lambda l: l.tipo == "entrada" and l.natureza == "pessoal"),
        "saidas_pessoais": soma(lambda l: l.tipo == "saida" and l.natureza == "pessoal"),
        "valor_misturado": soma(lambda l: l.misturado),
        "qtd_misturados": sum(1 for l in lancamentos if l.misturado),
        "faturamento_para_teto": entradas_neg,
    }


@dataclass
class PrecoMinimo:
    preco_minimo: Decimal
    preco_sugerido: Decimal
    composicao: dict
    premissas: list


def preco_minimo(custos_fixos_mensais, pro_labore_desejado, servicos_por_mes: int,
                 custo_variavel_por_servico=D("0"), margem_seguranca=D("0.10"),
                 tipo_atividade: str = "servicos", regras=REGRAS_2026) -> PrecoMinimo:
    """Preço mínimo por serviço em 3 passos."""
    if servicos_por_mes <= 0:
        raise ValueError("servicos_por_mes deve ser maior que zero")
    fixos = _d(custos_fixos_mensais)
    prolabore = _d(pro_labore_desejado)
    variavel = _d(custo_variavel_por_servico)
    margem = _d(margem_seguranca)
    if min(fixos, prolabore, variavel, margem) < 0:
        raise ValueError("valores não podem ser negativos")

    das = regras["das_mensal"][tipo_atividade]
    n = D(servicos_por_mes)
    parte_fixos = fixos / n
    parte_prolabore = prolabore / n
    parte_das = das / n
    minimo = parte_fixos + parte_prolabore + parte_das + variavel
    sugerido = minimo * (1 + margem)
    return PrecoMinimo(
        preco_minimo=_r2(minimo),
        preco_sugerido=_r2(sugerido),
        composicao={
            "custos_fixos_por_servico": _r2(parte_fixos),
            "pro_labore_por_servico": _r2(parte_prolabore),
            "das_por_servico": _r2(parte_das),
            "custo_variavel": _r2(variavel),
        },
        premissas=[
            f"Considera {servicos_por_mes} serviço(s) por mês e DAS de {fmt_brl(das)}.",
            f"Preço sugerido inclui {fmt_pct(margem * 100)} de margem de segurança.",
        ],
    )


@dataclass
class CenarioProjecao:
    nome: str
    media_mensal: Decimal
    projecao_anual: Decimal
    faixa: str
    mes_estouro: Optional[int]
    mes_estouro_20: Optional[int]
    serie_acumulada: list


@dataclass
class ProjecaoNotas:
    mes_atual: int
    acumulado: Decimal
    limite: Decimal
    limite_com_tolerancia: Decimal
    percentual_usado: Decimal
    faixa_atual: str
    margem_restante: Decimal
    meses_restantes: int
    media_possivel_para_ficar_no_limite: Decimal
    por_mes: dict
    acumulado_por_mes: list
    cenarios: list
    premissas: list


def _primeiro_mes_acima(serie: list, limite: Decimal) -> Optional[int]:
    for i, v in enumerate(serie):
        if v > limite:
            return i + 1
    return None


def projetar_com_historico(por_mes: dict, mes_atual: int, mes_abertura: Optional[int] = None,
                           regras=REGRAS_2026) -> ProjecaoNotas:
    """Projeta o ano a partir do faturamento de cada mês (ex.: somado das notas)."""
    if not 1 <= mes_atual <= 12:
        raise ValueError("mes_atual deve estar entre 1 e 12")
    inicio = mes_abertura or 1
    if mes_atual < inicio:
        raise ValueError("mes_atual não pode ser anterior ao mês de abertura")
    valores: dict[int, Decimal] = {}
    for m, v in por_mes.items():
        if not 1 <= int(m) <= 12:
            raise ValueError("mês inválido")
        v = _d(v)
        if v < 0:
            raise ValueError("valor mensal não pode ser negativo")
        valores[int(m)] = v

    real = [valores.get(m, D("0")) for m in range(1, mes_atual + 1)]
    acum_real, soma = [], D("0")
    for v in real:
        soma += v
        acum_real.append(soma)
    acumulado = soma

    limite = limite_do_ano(mes_abertura, regras)
    tol = regras["tolerancia_excesso"]
    limite20 = limite * (1 + tol)
    decorridos = mes_atual - inicio + 1
    restantes = 12 - mes_atual

    ritmos = [("ritmo médio do ano", acumulado / decorridos)]
    if decorridos > 3:
        ultimos = real[-3:]
        ritmos.append(("ritmo dos últimos 3 meses", sum(ultimos, D("0")) / 3))

    cenarios = []
    for nome, media in ritmos:
        serie, atual = list(acum_real), acumulado
        for _ in range(mes_atual + 1, 13):
            atual += media
            serie.append(atual)
        cenarios.append(CenarioProjecao(
            nome=nome, media_mensal=_r2(media), projecao_anual=_r2(serie[-1]),
            faixa=_faixa(serie[-1], limite, tol),
            mes_estouro=_primeiro_mes_acima(serie, limite),
            mes_estouro_20=_primeiro_mes_acima(serie, limite20),
            serie_acumulada=[_r2(x) for x in serie]))

    margem = max(limite - acumulado, D("0"))
    return ProjecaoNotas(
        mes_atual=mes_atual, acumulado=_r2(acumulado), limite=_r2(limite),
        limite_com_tolerancia=_r2(limite20), percentual_usado=_r2(acumulado / limite * 100),
        faixa_atual=_faixa(acumulado, limite, tol), margem_restante=_r2(margem),
        meses_restantes=restantes,
        media_possivel_para_ficar_no_limite=_r2(margem / restantes) if restantes else D("0.00"),
        por_mes={m: _r2(valores.get(m, D("0"))) for m in range(1, mes_atual + 1)},
        acumulado_por_mes=[_r2(x) for x in acum_real],
        cenarios=cenarios,
        premissas=[
            f"Considera {decorridos} mês(es) de atividade até o mês {mes_atual}; meses sem nota contam como zero.",
            f"Limite do ano: {fmt_brl(limite)}. Passando de {fmt_brl(limite20)} (20% acima), o efeito é retroativo.",
            "Só entram as notas enviadas. Vendas sem nota (por exemplo, para pessoa física) também contam para o teto.",
        ])


# ==============================================================================
# 3. LEITURA E CONSOLIDAÇÃO DE NOTAS FISCAIS (XML E CSV)
# ==============================================================================

@dataclass(frozen=True)
class Nota:
    data: date
    valor: Decimal
    descricao: str = ""
    origem: str = ""
    chave: str = ""

    @property
    def id(self) -> str:
        return self.chave or f"{self.data.isoformat()}|{self.valor}|{self.descricao}"


def parse_valor(txt) -> Decimal:
    """Aceita '1500.00', '1.500,00', 'R$ 1.500,00' e '1.500'."""
    s = str(txt).strip().replace("R$", "").replace(" ", "").replace(" ", "")
    if not s:
        raise ValueError("valor vazio")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"valor inválido: {txt!r}") from None
    if not v.is_finite():
        raise ValueError(f"valor inválido: {txt!r}")
    if v < 0:
        raise ValueError("valor negativo")
    return v


def parse_data(txt) -> date:
    """Aceita '2026-03-15', '2026-03-15T10:00:00-03:00' e '15/03/2026'."""
    s = str(txt).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    raise ValueError(f"data inválida: {txt!r}")


_NOS_NOTA = {"infNFe", "InfNfse", "infNFSe"}
_TAGS_DATA = ["dhEmi", "DataEmissao", "dEmi", "dCompet"]
_TAGS_VALOR = ["vNF", "ValorServicos", "vServ", "vLiq", "ValorLiquidoNfse"]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _achar(no, nomes):
    elementos = [(e, _local(e.tag)) for e in no.iter()]
    for nome in nomes:
        for e, loc in elementos:
            if loc == nome and e.text and e.text.strip():
                return e.text.strip()
    return None


def _cliente(no) -> str:
    for e in no.iter():
        if _local(e.tag) in ("dest", "toma", "TomadorServico", "Tomador"):
            nome = _achar(e, ["xNome", "RazaoSocial"])
            if nome:
                return nome
    return _achar(no, ["Discriminacao", "xDescServ"]) or ""


def parse_xml(texto: str, nome: str):
    if re.search(r"<!DOCTYPE|<!ENTITY", texto, re.I):
        return [], [f"{nome}: XML recusado por segurança (tem declaração DOCTYPE ou ENTITY)."]
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError as e:
        return [], [f"{nome}: não consegui ler o XML ({e})."]
    nos = [e for e in raiz.iter() if _local(e.tag) in _NOS_NOTA] or [raiz]
    notas, avisos = [], []
    for i, no in enumerate(nos, 1):
        d, v = _achar(no, _TAGS_DATA), _achar(no, _TAGS_VALOR)
        if not d or not v:
            avisos.append(f"{nome}: nota {i} sem data ou valor que eu reconheça, ignorada.")
            continue
        try:
            data, valor = parse_data(d), parse_valor(v)
        except ValueError as e:
            avisos.append(f"{nome}: nota {i} ignorada ({e}).")
            continue
        chave = no.get("Id") or ""
        if not chave:
            num = _achar(no, ["nNF", "Numero", "nNFSe"])
            chave = f"nfse-{num}-{data.isoformat()}" if num else f"{nome}#nota{i}"
        notas.append(Nota(data, valor, _cliente(no)[:60], nome, chave))
    return notas, avisos


def parse_csv(texto: str, nome: str):
    linhas = [l for l in texto.splitlines() if l.strip()]
    if not linhas:
        return [], [f"{nome}: arquivo vazio."]
    primeira = linhas[0]
    delim = ";" if ";" in primeira and primeira.count(";") >= primeira.count(",") else ","
    rows = list(csv.reader(linhas, delimiter=delim))
    cab = [_sem_acento(c) for c in rows[0]]
    tem_cab = any("data" in c or "valor" in c for c in cab)

    def achar(*termos):
        for i, c in enumerate(cab):
            if any(t in c for t in termos):
                return i
        return None

    if tem_cab:
        i_data, i_valor = achar("data", "emiss"), achar("valor", "total")
        i_desc = achar("desc", "cliente", "tomador", "nome")
        if i_data is None or i_valor is None:
            return [], [f"{nome}: o cabeçalho precisa ter uma coluna de data e uma de valor."]
        corpo, primeira_linha = rows[1:], 2
    else:
        i_data, i_valor, i_desc = 0, 1, 2
        corpo, primeira_linha = rows, 1

    notas, erros = [], []
    for n, row in enumerate(corpo, primeira_linha):
        try:
            data, valor = parse_data(row[i_data]), parse_valor(row[i_valor])
        except (ValueError, IndexError) as e:
            erros.append(f"{nome}, linha {n}: ignorada ({e}).")
            continue
        desc = row[i_desc].strip() if i_desc is not None and i_desc < len(row) else ""
        notas.append(Nota(data, valor, desc[:60], nome, f"{nome}#linha{n}"))
    if len(erros) > 5:
        erros = erros[:5] + [f"{nome}: e mais {len(erros) - 5} linha(s) ignorada(s)."]
    return notas, erros


def parse_arquivo(nome: str, conteudo: str):
    baixo = nome.lower()
    if baixo.endswith(".pdf"):
        return [], [f"{nome}: PDF ainda não é suportado. Envie o XML da nota ou uma planilha CSV."]
    conteudo = conteudo.lstrip("﻿")
    if baixo.endswith(".xml") or conteudo.lstrip()[:1] == "<":
        return parse_xml(conteudo, nome)
    return parse_csv(conteudo, nome)


def mesclar(existentes: list, novas: list):
    vistos = {n.id for n in existentes}
    lista, repetidas = list(existentes), 0
    for n in novas:
        if n.id in vistos:
            repetidas += 1
            continue
        vistos.add(n.id)
        lista.append(n)
    avisos = [f"{repetidas} nota(s) repetida(s) ignorada(s)."] if repetidas else []
    return lista, avisos


def agregar_por_mes(notas: list, ano: int):
    por_mes: dict[int, Decimal] = {}
    fora = 0
    for n in notas:
        if n.data.year != ano:
            fora += 1
            continue
        por_mes[n.data.month] = por_mes.get(n.data.month, Decimal("0")) + n.valor
    avisos = [f"{fora} nota(s) fora de {ano} não entraram na conta."] if fora else []
    return por_mes, avisos


# ==============================================================================
# 4. CURADORIA DE APOIO E ACOLHIMENTO (FLUXO 3)
# ==============================================================================

ACOLHIMENTO = {
    "isolamento": (
        "Trabalhar sozinha pesa mesmo, e sentir falta de gente que entenda o que você vive "
        "é muito comum entre mulheres MEI. Você não está exagerando."
    ),
    "cansaco": (
        "Dar conta do trabalho, da casa e de tudo mais cansa de verdade. "
        "Faz sentido você sentir isso."
    ),
}

CURADORIA = {
    "isolamento": [
        ("Sebrae Delas", "programa do Sebrae voltado a mulheres empreendedoras, com conteúdos e encontros de troca"),
        ("Rede Mulher Empreendedora", "rede e comunidade de mulheres que empreendem, com eventos e conteúdo"),
    ],
    "cansaco": [
        ("Sebrae Delas", "conteúdos sobre organização de rotina e gestão do tempo para quem empreende"),
    ],
    "duvida_fiscal": [
        ("Portal do Empreendedor (gov.br)", "canal oficial para emitir o DAS, fazer a declaração anual e comunicar desenquadramento"),
        ("Sebrae", "atendimento gratuito para orientação sobre MEI"),
    ],
}


def acolher_e_sugerir(tema: str) -> dict:
    """Devolve acolhimento + UMA sugestão (a primeira), para não despejar lista."""
    if tema not in ACOLHIMENTO:
        tema = "isolamento"
    nome, para_que = CURADORIA[tema][0]
    return {
        "tema": tema,
        "acolhimento": ACOLHIMENTO[tema],
        "sugestao": {"nome": nome, "para_que_serve": para_que},
        "outras_opcoes": [n for n, _ in CURADORIA[tema][1:]],
        "regra": "Acolha primeiro. Ofereça a sugestão como opcional, sem cobrar ação e sem virar terapia.",
    }


# ==============================================================================
# 5. CAMADA DO AGENTE (TOOLS, SESSÃO, LLM E MODO OFFLINE)
# ==============================================================================

MODELO_PADRAO = os.environ.get("MEI_MODEL", "claude-sonnet-4-5")

SYSTEM_PROMPT = """Você é a Copiloto MEI, assistente de clareza financeira para mulheres MEI.

Tom: acolhedor, direto, como uma amiga que entende do assunto. Frases curtas, sem juridiquês
nem economês. Explique qualquer termo técnico em uma linha na primeira vez. Nunca use tom de
alarme: risco vem com número, prazo e próximo passo. Respostas curtas, pensadas para o celular.

Regras que não se negociam:
1. Você NUNCA faz conta de cabeça. Todo número vem de uma ferramenta. Se faltar dado, pergunte só o que falta.
2. Repita os valores exatamente como a ferramenta devolveu e conte as premissas que ela trouxe.
3. Simulações de migração para ME são estimativas. Diga isso de forma clara, uma vez, sem repetir em toda frase.
4. Se a pergunta depender de regra que você não tem certeza, diga que não tem certeza e indique o Portal do Empreendedor ou um contador.
5. Se ela falar de cansaço, solidão ou falta de troca, acolha primeiro (sem lista de tarefas) e use a ferramenta de apoio para oferecer UMA sugestão opcional. Você não faz terapia.
6. Desabafo não vira tarefa. Só ofereça o próximo passo se ela quiser.
7. Fique no escopo: teto e enquadramento, caixa PF/PJ, preço mínimo e apoio. Fora disso, diga com gentileza que não é com você.
8. Se ela enviou notas fiscais, use a ferramenta analisar_notas para saber se o faturamento vai bater o teto. Lembre uma vez que só as notas enviadas foram contadas e que vendas sem nota também entram no teto.
"""

FERRAMENTAS = [
    {
        "name": "verificar_teto",
        "description": "Mostra quanto do teto anual do MEI já foi usado, quanto ainda cabe, a projeção até dezembro e a faixa de alerta.",
        "input_schema": {
            "type": "object",
            "properties": {
                "faturado": {"type": "number", "description": "Faturamento acumulado no ano, em reais."},
                "mes_atual": {"type": "integer", "description": "Último mês já fechado (1 a 12)."},
                "mes_abertura": {"type": "integer", "description": "Mês de abertura do MEI neste ano, se abriu no ano corrente."},
            },
            "required": ["faturado", "mes_atual"],
        },
    },
    {
        "name": "simular_migracao_me",
        "description": "Estimativa didática do custo de passar do teto e do que acontece (até 20% acima ou mais de 20% acima).",
        "input_schema": {
            "type": "object",
            "properties": {
                "faturamento_total": {"type": "number", "description": "Faturamento total do ano (real ou projetado), em reais."},
                "aliquota_estimada": {"type": "number", "description": "Alíquota do Simples como fração (0.06 = 6%). Opcional."},
                "tipo_atividade": {"type": "string", "enum": ["comercio", "servicos", "misto"]},
                "mes_abertura": {"type": "integer"},
            },
            "required": ["faturamento_total"],
        },
    },
    {
        "name": "registrar_lancamento",
        "description": "Registra uma entrada ou saída, dizendo em que conta o dinheiro passou (PF ou PJ) e a quem pertence (negócio ou pessoal).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["entrada", "saida"]},
                "conta": {"type": "string", "enum": ["PF", "PJ"]},
                "natureza": {"type": "string", "enum": ["negocio", "pessoal"]},
                "valor": {"type": "number"},
                "descricao": {"type": "string"},
            },
            "required": ["tipo", "conta", "natureza", "valor"],
        },
    },
    {
        "name": "resumo_caixa",
        "description": "Resumo dos lançamentos da sessão: saldo do negócio e quanto dinheiro passou na conta errada.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calcular_preco_minimo",
        "description": "Preço mínimo e sugerido por serviço a partir de custos fixos, pró-labore desejado e volume mensal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "custos_fixos_mensais": {"type": "number"},
                "pro_labore_desejado": {"type": "number"},
                "servicos_por_mes": {"type": "integer"},
                "custo_variavel_por_servico": {"type": "number"},
                "tipo_atividade": {"type": "string", "enum": ["comercio", "servicos", "misto"]},
            },
            "required": ["custos_fixos_mensais", "pro_labore_desejado", "servicos_por_mes"],
        },
    },
    {
        "name": "sugerir_apoio",
        "description": "Acolhimento e UMA sugestão opcional de rede de apoio. Use quando ela falar de solidão, cansaço ou falta de troca.",
        "input_schema": {
            "type": "object",
            "properties": {"tema": {"type": "string", "enum": ["isolamento", "cansaco", "duvida_fiscal"]}},
            "required": ["tema"],
        },
    },
    {
        "name": "analisar_notas",
        "description": "Soma as notas fiscais que a usuária enviou, mês a mês, e projeta se o faturamento vai bater o teto do MEI, em quais meses e quanto ela pode faturar por mês para ficar no limite.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mes_atual": {"type": "integer", "description": "Último mês fechado (1 a 12). Se omitido, usa o mês da nota mais recente."},
                "mes_abertura": {"type": "integer", "description": "Mês de abertura do MEI, se abriu neste ano."},
            },
        },
    },
]


def _plano(obj):
    """Converte Decimal/dataclass em estruturas JSON (valores com 2 casas, como texto)."""
    if isinstance(obj, Decimal):
        return f"{obj:.2f}"
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _plano(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {k: _plano(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plano(v) for v in obj]
    return obj


class Sessao:
    """Estado da conversa em memória. Nada é gravado em disco."""

    def __init__(self):
        self.lancamentos: list[Lancamento] = []
        self.notas: list[Nota] = []

    def executar(self, nome: str, args: dict) -> dict:
        """Executa uma ferramenta. Erros viram mensagem, nunca quebram a conversa."""
        try:
            if nome == "verificar_teto":
                return _plano(status_teto(args["faturado"], args["mes_atual"], args.get("mes_abertura")))
            if nome == "simular_migracao_me":
                kw = {}
                if args.get("aliquota_estimada") is not None:
                    kw["aliquota_estimada"] = Decimal(str(args["aliquota_estimada"]))
                return _plano(simular_excesso(
                    args["faturamento_total"],
                    tipo_atividade=args.get("tipo_atividade", "servicos"),
                    mes_abertura=args.get("mes_abertura"), **kw))
            if nome == "registrar_lancamento":
                l = Lancamento(args["tipo"], args["conta"], args["natureza"],
                               args["valor"], args.get("descricao", ""))
                self.lancamentos.append(l)
                return {"ok": True, "misturado": l.misturado, "total_lancamentos": len(self.lancamentos)}
            if nome == "resumo_caixa":
                if not self.lancamentos:
                    return {"erro": "Ainda não há lançamentos registrados."}
                return _plano(resumo_caixa(self.lancamentos))
            if nome == "calcular_preco_minimo":
                return _plano(preco_minimo(
                    args["custos_fixos_mensais"], args["pro_labore_desejado"],
                    args["servicos_por_mes"],
                    custo_variavel_por_servico=Decimal(str(args.get("custo_variavel_por_servico", 0))),
                    tipo_atividade=args.get("tipo_atividade", "servicos")))
            if nome == "analisar_notas":
                if not self.notas:
                    return {"erro": "Ainda não há notas enviadas."}
                ano = REGRAS_2026["ano"]
                por_mes, avisos = agregar_por_mes(self.notas, ano)
                if not por_mes:
                    return {"erro": f"Nenhuma das notas enviadas é de {ano}."}
                mes_atual = args.get("mes_atual") or max(por_mes)
                saida = _plano(projetar_com_historico(por_mes, mes_atual, args.get("mes_abertura")))
                saida["avisos"] = avisos
                saida["qtd_notas"] = len(self.notas)
                return saida
            if nome == "sugerir_apoio":
                return acolher_e_sugerir(args["tema"])
            return {"erro": f"Ferramenta desconhecida: {nome}"}
        except (ValueError, KeyError, InvalidOperation) as e:
            return {"erro": f"Não consegui calcular com esses dados: {e}"}


class Agente:
    def __init__(self, modelo: str = MODELO_PADRAO, cliente=None):
        if cliente is None:
            import anthropic
            cliente = anthropic.Anthropic()
        self.cliente = cliente
        self.modelo = modelo
        self.sessao = Sessao()
        self.historico: list[dict] = []

    def conversar(self, mensagem: str, max_voltas: int = 6) -> str:
        self.historico.append({"role": "user", "content": mensagem})
        for _ in range(max_voltas):
            resp = self.cliente.messages.create(
                model=self.modelo, max_tokens=1024, system=SYSTEM_PROMPT,
                tools=FERRAMENTAS, messages=self.historico)
            self.historico.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            resultados = []
            for b in resp.content:
                if b.type == "tool_use":
                    saida = self.sessao.executar(b.name, b.input)
                    resultados.append({"type": "tool_result", "tool_use_id": b.id,
                                       "content": json.dumps(saida, ensure_ascii=False)})
            self.historico.append({"role": "user", "content": resultados})
        return "Preciso de mais um dado seu para seguir. Pode me contar de novo o que você quer calcular?"


class AgenteHF:
    def __init__(self, repo_id: Optional[str] = None, cliente=None):
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        if cliente is None and not token:
            raise RuntimeError("Defina HF_TOKEN ou HUGGINGFACEHUB_API_TOKEN.")
        if token:
            os.environ["HUGGINGFACEHUB_API_TOKEN"] = token

        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
        from langchain_core.tools import tool
        from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

        self.sessao = Sessao()
        self._AIMessage = AIMessage
        self._HumanMessage = HumanMessage
        self._ToolMessage = ToolMessage

        if cliente is not None:
            base_llm = cliente
        else:
            endpoint = HuggingFaceEndpoint(
                repo_id=repo_id or os.environ.get("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
                task="text-generation",
                max_new_tokens=512,
                temperature=0.2,
            )
            base_llm = ChatHuggingFace(llm=endpoint)

        @tool
        def verificar_teto(faturado: float, mes_atual: int, mes_abertura: Optional[int] = None) -> str:
            """Mostra quanto do teto anual do MEI já foi usado, margem restante, projeção e faixa."""
            return json.dumps(
                self.sessao.executar("verificar_teto", {
                    "faturado": faturado, "mes_atual": mes_atual, "mes_abertura": mes_abertura
                }),
                ensure_ascii=False,
            )

        @tool
        def simular_migracao_me(
            faturamento_total: float,
            aliquota_estimada: Optional[float] = None,
            tipo_atividade: str = "servicos",
            mes_abertura: Optional[int] = None,
        ) -> str:
            """Estimativa didática do custo de passar do teto do MEI."""
            return json.dumps(
                self.sessao.executar("simular_migracao_me", {
                    "faturamento_total": faturamento_total,
                    "aliquota_estimada": aliquota_estimada,
                    "tipo_atividade": tipo_atividade,
                    "mes_abertura": mes_abertura,
                }),
                ensure_ascii=False,
            )

        @tool
        def registrar_lancamento(
            tipo: str, conta: str, natureza: str, valor: float, descricao: str = ""
        ) -> str:
            """Registra entrada ou saída em conta PF ou PJ com natureza negócio ou pessoal."""
            return json.dumps(
                self.sessao.executar("registrar_lancamento", {
                    "tipo": tipo, "conta": conta, "natureza": natureza,
                    "valor": valor, "descricao": descricao
                }),
                ensure_ascii=False,
            )

        @tool
        def resumo_caixa() -> str:
            """Resumo dos lançamentos da sessão: saldo do negócio e dinheiro na conta errada."""
            return json.dumps(self.sessao.executar("resumo_caixa", {}), ensure_ascii=False)

        @tool
        def calcular_preco_minimo(
            custos_fixos_mensais: float,
            pro_labore_desejado: float,
            servicos_por_mes: int,
            custo_variavel_por_servico: float = 0.0,
            tipo_atividade: str = "servicos",
        ) -> str:
            """Preço mínimo e sugerido por serviço."""
            return json.dumps(
                self.sessao.executar("calcular_preco_minimo", {
                    "custos_fixos_mensais": custos_fixos_mensais,
                    "pro_labore_desejado": pro_labore_desejado,
                    "servicos_por_mes": servicos_por_mes,
                    "custo_variavel_por_servico": custo_variavel_por_servico,
                    "tipo_atividade": tipo_atividade,
                }),
                ensure_ascii=False,
            )

        @tool
        def sugerir_apoio(tema: str) -> str:
            """Acolhimento e uma sugestão opcional de apoio para isolamento, cansaço ou dúvida fiscal."""
            return json.dumps(self.sessao.executar("sugerir_apoio", {"tema": tema}), ensure_ascii=False)

        @tool
        def analisar_notas(mes_atual: Optional[int] = None, mes_abertura: Optional[int] = None) -> str:
            """Soma as notas fiscais enviadas na sessão e projeta o teto anual."""
            return json.dumps(
                self.sessao.executar("analisar_notas", {"mes_atual": mes_atual, "mes_abertura": mes_abertura}),
                ensure_ascii=False,
            )

        tools = [
            verificar_teto,
            simular_migracao_me,
            registrar_lancamento,
            resumo_caixa,
            calcular_preco_minimo,
            sugerir_apoio,
            analisar_notas,
        ]
        self.llm_com_tools = base_llm.bind_tools(tools)
        self.mensagens = [SystemMessage(content=SYSTEM_PROMPT)]

    def conversar(self, mensagem: str, max_voltas: int = 6) -> str:
        self.mensagens.append(self._HumanMessage(content=mensagem))
        for _ in range(max_voltas):
            resp = self.llm_com_tools.invoke(self.mensagens)
            self.mensagens.append(resp)
            tool_calls = getattr(resp, "tool_calls", None) or []
            if not tool_calls:
                conteudo = getattr(resp, "content", "")
                return conteudo if isinstance(conteudo, str) else str(conteudo)
            for tc in tool_calls:
                saida = self.sessao.executar(tc["name"], tc["args"])
                self.mensagens.append(
                    self._ToolMessage(
                        content=json.dumps(saida, ensure_ascii=False),
                        tool_call_id=tc["id"],
                    )
                )
        return "Preciso de mais um dado seu para seguir. Pode me contar de novo o que você quer calcular?"


# --- Modo offline (regras determinísticas sem LLM) ---

MESES = {m: i + 1 for i, m in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
     "agosto", "setembro", "outubro", "novembro", "dezembro"])}

NOMES_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]


def extrair_valores(texto: str) -> list[Decimal]:
    """Acha valores em reais: 'R$ 45.000', '45 mil', '800,50'."""
    t = _sem_acento(texto)
    achados = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*(mil)?", t):
        bruto = m.group(1).replace(".", "").replace(",", ".")
        v = Decimal(bruto)
        if m.group(2):
            v *= 1000
        achados.append(v)
    return achados


def _mes(texto: str):
    t = _sem_acento(texto)
    for nome, n in MESES.items():
        if nome in t:
            return n
    return None


def _txt_teto(r: dict, simulando: bool = False) -> str:
    nomes = {"ok": "dentro do limite", "atencao": "hora de ficar de olho",
             "proximo": "bem perto do teto", "excesso_ate_20": "já passou do teto (até 20%)",
             "excesso_acima_20": "passou de 20% do teto"}
    linhas = [
        f"Até agora você usou {fmt_pct(r['percentual_usado'])} do seu limite de {fmt_brl(r['limite'])} "
        f"({fmt_brl(r['faturado'])}). Situação: {nomes[r['faixa_atual']]}.",
        f"Ainda cabem {fmt_brl(r['margem_restante'])}, o que dá cerca de {fmt_brl(r['media_mensal_possivel'])} "
        f"por mês nos {r['meses_restantes']} mês(es) que faltam.",
        f"Se o ritmo continuar igual, você fecha o ano com {fmt_brl(r['projecao_anual'])}.",
    ]
    if r["faixa_projetada"] in ("excesso_ate_20", "excesso_acima_20"):
        linhas.append("Nesse ritmo você passaria do teto." + ("" if simulando else " Quer que eu simule o que acontece?"))
    else:
        linhas.append("Nesse ritmo você fica dentro do teto. Sem susto.")
    linhas.append("Premissa: " + r["premissas"][0])
    return "\n".join(linhas)


def _txt_me(r: dict, projecao: bool = False) -> str:
    if r["faixa"] == "dentro_do_teto":
        return r["explicacao"]
    explic = r["explicacao"].replace("Você passou", "Você passaria") if projecao else r["explicacao"]
    intro = "Se o ritmo continuar: " if projecao else ""
    return (
        f"{intro}{explic}\n"
        f"Excedente: {fmt_brl(r['excesso'])} ({fmt_pct(r['percentual_excesso'])} acima do limite). "
        f"Imposto estimado: {fmt_brl(r['imposto_estimado'])}.\n"
        f"Importante: é uma estimativa. {r['premissas'][1]} {r['premissas'][2]}"
    )


def _txt_analise(r: dict) -> str:
    linhas = [f"Com as notas que você enviou, até {NOMES_MES[r['mes_atual'] - 1]} você faturou "
              f"{fmt_brl(r['acumulado'])}, {fmt_pct(r['percentual_usado'])} do limite de {fmt_brl(r['limite'])}."]
    for c in r["cenarios"]:
        base = (f"Se mantiver o {c['nome']} ({fmt_brl(c['media_mensal'])} por mês), "
                f"fecha o ano com {fmt_brl(c['projecao_anual'])}")
        if c["mes_estouro"]:
            tempo = "já passou do teto em" if c["mes_estouro"] <= r["mes_atual"] else "passaria do teto em"
            base += f" e {tempo} {NOMES_MES[c['mes_estouro'] - 1]}."
        else:
            base += ", dentro do teto."
        linhas.append(base)
    if r["meses_restantes"]:
        linhas.append("Para ficar dentro do limite, dá para faturar em média "
                      f"{fmt_brl(r['media_possivel_para_ficar_no_limite'])} por mês até dezembro.")
    linhas.append("Lembre: contei só as notas enviadas. Vendas sem nota também entram no teto.")
    return "\n".join(linhas)


def responder_offline(mensagem: str, sessao: Sessao) -> str:
    """Roteador por regras. Cobre o roteiro da demo, sem LLM."""
    t = _sem_acento(mensagem)
    valores = extrair_valores(mensagem)

    if re.search(r"sozinha|isolad|ninguem|falta de troca|sem ninguem", t):
        r = sessao.executar("sugerir_apoio", {"tema": "isolamento"})
    elif re.search(r"cansad|exaust|sobrecarreg|esgotad|nao aguento", t):
        r = sessao.executar("sugerir_apoio", {"tema": "cansaco"})
    else:
        r = None
    if r:
        s = r["sugestao"]
        return (
            f"{r['acolhimento']}\n\n"
            f"Se quiser, uma ideia leve: {s['nome']} ({s['para_que_serve']}). "
            "Sem pressa, só se fizer sentido para você."
        )

    if re.search(r"teto|limite|estour|bater|faturei|faturamento", t):
        mes = _mes(mensagem)
        if sessao.notas and not valores:
            r = sessao.executar("analisar_notas", {"mes_atual": mes} if mes else {})
            return r["erro"] if "erro" in r else _txt_analise(r)
        if not valores or mes is None:
            return ("Para olhar seu teto, me conta: quanto você faturou no ano até agora e até que mês? "
                    "Se preferir, envie suas notas fiscais pela página web que eu somo para você.")
        r = sessao.executar("verificar_teto", {"faturado": valores[0], "mes_atual": mes})
        if "erro" in r:
            return r["erro"]
        quer_sim = "simul" in t and r["faixa_projetada"] != "ok"
        texto = _txt_teto(r, simulando=quer_sim)
        if quer_sim:
            sim = sessao.executar("simular_migracao_me", {"faturamento_total": r["projecao_anual"]})
            texto += "\n\n" + _txt_me(sim, projecao=True)
        return texto

    if re.search(r"recebi|ganhei|paguei|gastei|comprei", t) and valores:
        entrada = bool(re.search(r"recebi|ganhei", t))
        conta = "PF" if re.search(r"pessoal|\bpf\b", t) else "PJ"
        natureza = "pessoal" if re.search(r"mercado|aluguel|farmacia|pessoal.*gast|gast.*pessoal", t) and not entrada else "negocio"
        r = sessao.executar("registrar_lancamento", {
            "tipo": "entrada" if entrada else "saida", "conta": conta,
            "natureza": natureza, "valor": valores[0], "descricao": mensagem[:60]})
        if "erro" in r:
            return r["erro"]
        aviso = ""
        if r["misturado"]:
            if natureza == "negocio":
                aviso = " Esse dinheiro é do negócio mas passou pela conta pessoal."
            else:
                aviso = " Esse gasto é pessoal mas saiu da conta do negócio."
            aviso += " Sem julgamento: anotei para você enxergar isso depois."
        return f"Anotado: {'entrada' if entrada else 'saída'} de {fmt_brl(valores[0])} ({'negócio' if natureza == 'negocio' else 'pessoal'}, conta {conta}).{aviso}"

    if re.search(r"resumo|saldo|como esta meu caixa", t):
        r = sessao.executar("resumo_caixa", {})
        if "erro" in r:
            return r["erro"]
        return (
            f"Seu negócio: entrou {fmt_brl(r['entradas_negocio'])}, saiu {fmt_brl(r['saidas_negocio'])}, "
            f"saldo {fmt_brl(r['saldo_negocio'])}.\n"
            f"Dinheiro na conta errada: {fmt_brl(r['valor_misturado'])} ({r['qtd_misturados']} lançamento(s))."
        )

    if re.search(r"preco|precificar|cobrar|quanto devo", t):
        if len(valores) < 3:
            return ("Vamos em 3 passos. Me conta numa mensagem só: (1) seus custos fixos por mês, "
                    "(2) quanto quer retirar para você por mês, (3) quantos serviços consegue fazer por mês. "
                    "Ex.: custos 500, retirar 3000, 10 serviços.")
        fixos, retirada, qtd = valores[0], valores[1], int(valores[2])
        r = sessao.executar("calcular_preco_minimo", {
            "custos_fixos_mensais": fixos, "pro_labore_desejado": retirada, "servicos_por_mes": qtd})
        if "erro" in r:
            return r["erro"]
        c = r["composicao"]
        return (
            f"Seu preço mínimo por serviço é {fmt_brl(r['preco_minimo'])}. Abaixo disso você trabalha no prejuízo.\n"
            f"Sugiro cobrar {fmt_brl(r['preco_sugerido'])} (com folga para imprevistos).\n"
            f"De onde vem: custos fixos {fmt_brl(c['custos_fixos_por_servico'])} + seu pró-labore "
            f"{fmt_brl(c['pro_labore_por_servico'])} + imposto (DAS) {fmt_brl(c['das_por_servico'])} "
            f"+ custo por entrega {fmt_brl(c['custo_variavel'])}.\n"
            f"Premissa: {r['premissas'][0]}"
        )

    return ("Posso ajudar com: seu teto de faturamento, a separação entre dinheiro pessoal e do negócio, "
            "o preço mínimo do seu serviço, ou só conversar sobre a rotina. Por onde quer começar?")


# ==============================================================================
# 6. SERVIDOR HTTP LOCAL + CLI / ROTEIRO DE DEMO
# ==============================================================================

BASE = Path(__file__).parent
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
EXEMPLOS = ["notas_jan_a_jun.csv", "nfse_jul_ago.xml", "nfe_set.xml", "nfe_out.xml"]
MAX_CORPO = 5 * 1024 * 1024
MAX_SESSOES = 200
CSP = ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; img-src 'self' data:")

SESSOES: dict = {}

ROTEIRO_DEMO = [
    "Oi! Faturei 52 mil até junho e fiquei com medo de estourar o teto. Pode simular o que acontece?",
    "Recebi 800 do cliente Bia na minha conta pessoal",
    "Paguei 150 de mercado na conta PJ sem querer",
    "Qual o resumo do meu caixa?",
    "Quero saber quanto cobrar: custos 500, retirar 3000, 10 serviços",
    "Estou cansada e me sinto sozinha nisso tudo",
]


def _provedor_llm() -> Optional[str]:
    tem_hf_token = bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN"))
    if tem_hf_token and importlib.util.find_spec("langchain_huggingface") and importlib.util.find_spec("langchain_core"):
        return "hf"
    tem_ant_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if tem_ant_key and importlib.util.find_spec("anthropic"):
        return "anthropic"
    return None


def modo() -> str:
    return "llm" if _provedor_llm() else "offline"


class Estado:
    def __init__(self):
        self.sessao = Sessao()
        self.agente = None
        provedor = _provedor_llm()
        if provedor == "hf":
            self.agente = AgenteHF()
            self.agente.sessao = self.sessao
        elif provedor == "anthropic":
            self.agente = Agente()
            self.agente.sessao = self.sessao


def estado(sid) -> Estado:
    if not isinstance(sid, str) or not 8 <= len(sid) <= 64:
        raise ValueError("sessão inválida")
    if sid not in SESSOES:
        if len(SESSOES) >= MAX_SESSOES:
            SESSOES.pop(next(iter(SESSOES)))
        SESSOES[sid] = Estado()
    return SESSOES[sid]


def lista_notas(st: Estado) -> list:
    return [{"id": n.id, "data": n.data.isoformat(), "valor": f"{n.valor:.2f}",
             "descricao": n.descricao, "origem": n.origem}
            for n in sorted(st.sessao.notas, key=lambda n: (n.data, n.id))]


def resposta_notas(st: Estado, avisos: list) -> dict:
    return {"notas": lista_notas(st), "avisos": avisos}


def r_notas(corpo, st):
    arquivos = corpo.get("arquivos")
    if not isinstance(arquivos, list) or not 0 < len(arquivos) <= 50:
        raise ValueError("envie de 1 a 50 arquivos")
    novas, avisos = [], []
    for a in arquivos:
        ns, av = parse_arquivo(str(a.get("nome", "arquivo"))[:80], str(a.get("conteudo", "")))
        novas += ns
        avisos += av
    st.sessao.notas, av = mesclar(st.sessao.notas, novas)
    avisos += av
    if novas:
        avisos.insert(0, f"{len(novas)} nota(s) lida(s).")
    return resposta_notas(st, avisos)


def r_manual(corpo, st):
    n = Nota(parse_data(corpo.get("data", "")), parse_valor(corpo.get("valor", "")),
             str(corpo.get("descricao", ""))[:60] or "Receita sem nota", "manual", f"manual-{uuid.uuid4().hex}")
    st.sessao.notas.append(n)
    return resposta_notas(st, [])


def r_remover(corpo, st):
    alvo = corpo.get("id")
    st.sessao.notas = [n for n in st.sessao.notas if n.id != alvo]
    return resposta_notas(st, [])


def r_exemplo(corpo, st):
    novas = []
    for nome in EXEMPLOS:
        ns, _ = parse_arquivo(nome, (BASE / "exemplos" / nome).read_text(encoding="utf-8"))
        novas += ns
    st.sessao.notas, _ = mesclar(st.sessao.notas, novas)
    return resposta_notas(st, ["Notas de exemplo da Marina (fictícias) carregadas."])


def r_limpar(corpo, st):
    st.sessao.notas = []
    return resposta_notas(st, [])


def r_analise(corpo, st):
    args = {k: corpo[k] for k in ("mes_atual", "mes_abertura") if isinstance(corpo.get(k), int)}
    return st.sessao.executar("analisar_notas", args)


def r_chat(corpo, st):
    msg = str(corpo.get("mensagem", "")).strip()[:2000]
    if not msg:
        raise ValueError("mensagem vazia")
    if st.agente:
        try:
            return {"resposta": st.agente.conversar(msg), "modo": "llm"}
        except Exception:
            return {"resposta": responder_offline(msg, st.sessao), "modo": "offline",
                    "aviso": "Não consegui falar com o modelo agora. Respondi no modo offline."}
    return {"resposta": responder_offline(msg, st.sessao), "modo": "offline"}


def r_listar_notas(corpo, st):
    return resposta_notas(st, [])


# --- Caixa PF/PJ
MAX_LANCAMENTOS = 2000

CAIXA_EXEMPLO = [
    ("entrada", "PF", "negocio", "800", "Cliente Exemplo B (caiu na conta pessoal)"),
    ("entrada", "PJ", "negocio", "2500", "Cliente Exemplo A - site"),
    ("saida", "PJ", "negocio", "120", "Licença de software"),
    ("saida", "PJ", "pessoal", "150", "Mercado (saiu da conta do negócio)"),
    ("saida", "PF", "pessoal", "400", "Aluguel"),
    ("saida", "PJ", "negocio", "85", "Impressão de material"),
]


def _lista_caixa(st: Estado) -> dict:
    itens = [{"indice": i, "tipo": l.tipo, "conta": l.conta, "natureza": l.natureza,
              "valor": f"{l.valor:.2f}", "descricao": l.descricao, "misturado": l.misturado}
             for i, l in enumerate(st.sessao.lancamentos)]
    return {"lancamentos": itens, "resumo": _plano(resumo_caixa(st.sessao.lancamentos))}


def r_caixa(corpo, st):
    return _lista_caixa(st)


def r_caixa_registrar(corpo, st):
    if len(st.sessao.lancamentos) >= MAX_LANCAMENTOS:
        raise ValueError("limite de lançamentos da sessão atingido")
    st.sessao.lancamentos.append(Lancamento(
        str(corpo.get("tipo", "")), str(corpo.get("conta", "")), str(corpo.get("natureza", "")),
        parse_valor(corpo.get("valor", "")), str(corpo.get("descricao", ""))[:60]))
    return _lista_caixa(st)


def r_caixa_remover(corpo, st):
    i = corpo.get("indice")
    if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(st.sessao.lancamentos):
        raise ValueError("lançamento não encontrado")
    del st.sessao.lancamentos[i]
    return _lista_caixa(st)


def r_caixa_exemplo(corpo, st):
    if not st.sessao.lancamentos:
        for tipo, conta, natureza, valor, desc in CAIXA_EXEMPLO:
            st.sessao.lancamentos.append(Lancamento(tipo, conta, natureza, D(valor), desc))
    return _lista_caixa(st)


def r_caixa_limpar(corpo, st):
    st.sessao.lancamentos = []
    return _lista_caixa(st)


# --- Preço mínimo e simulador de teto (formulários chamam o motor direto, sem LLM)
def _opcional(corpo, chave, padrao):
    v = corpo.get(chave)
    return padrao if v is None or str(v).strip() == "" else v


def _tipo_atividade(corpo) -> str:
    tipo = str(_opcional(corpo, "tipo_atividade", "servicos"))
    if tipo not in REGRAS_2026["das_mensal"]:
        raise ValueError("tipo de atividade inválido")
    return tipo


def r_preco(corpo, st):
    try:
        qtd = int(str(corpo.get("servicos", "")).strip())
    except ValueError:
        raise ValueError("informe quantos serviços você faz por mês (número inteiro)") from None
    margem = parse_valor(_opcional(corpo, "margem", "10")) / 100
    return _plano(preco_minimo(
        parse_valor(corpo.get("custos_fixos", "")), parse_valor(corpo.get("pro_labore", "")), qtd,
        custo_variavel_por_servico=parse_valor(_opcional(corpo, "custo_variavel", "0")),
        margem_seguranca=margem, tipo_atividade=_tipo_atividade(corpo)))


def r_simular(corpo, st):
    aliquota = parse_valor(_opcional(corpo, "aliquota", "6")) / 100
    if aliquota > 1:
        raise ValueError("a alíquota deve estar entre 0% e 100%")
    mes = _opcional(corpo, "mes_abertura", None)
    return _plano(simular_excesso(
        parse_valor(corpo.get("faturamento_total", "")), aliquota, _tipo_atividade(corpo),
        int(mes) if mes is not None else None))


def r_chat_limpar(corpo, st):
    ag = st.agente
    if ag is not None:
        if hasattr(ag, "historico"):
            ag.historico = []
        if hasattr(ag, "mensagens"):
            ag.mensagens = ag.mensagens[:1]
    return {"ok": True}


ROTAS = {
    "/api/notas/listar": r_listar_notas,
    "/api/caixa": r_caixa,
    "/api/caixa/registrar": r_caixa_registrar,
    "/api/caixa/remover": r_caixa_remover,
    "/api/caixa/exemplo": r_caixa_exemplo,
    "/api/caixa/limpar": r_caixa_limpar,
    "/api/preco": r_preco,
    "/api/simular": r_simular,
    "/api/chat/limpar": r_chat_limpar,
    "/api/notas": r_notas,
    "/api/notas/manual": r_manual,
    "/api/notas/remover": r_remover,
    "/api/exemplo": r_exemplo,
    "/api/limpar": r_limpar,
    "/api/analise": r_analise,
    "/api/chat": r_chat,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, status, corpo: bytes, tipo: str):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, status, obj):
        self._enviar(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        caminho = self.path.split("?")[0]
        if caminho == "/api/status":
            return self._json(200, {"modo": modo(), "ano": REGRAS_2026["ano"], "hoje": date.today().isoformat()})
        if caminho in STATIC:
            arq, tipo = STATIC[caminho]
            try:
                return self._enviar(200, (BASE / "static" / arq).read_bytes(), tipo)
            except FileNotFoundError:
                return self._json(404, {"erro": "não encontrado"})
        self._json(404, {"erro": "não encontrado"})

    def do_POST(self):
        rota = ROTAS.get(self.path.split("?")[0])
        if not rota:
            return self._json(404, {"erro": "não encontrado"})
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if tamanho > MAX_CORPO:
                return self._json(413, {"erro": "arquivos grandes demais (limite de 5 MB por envio)"})
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
            if not isinstance(corpo, dict):
                raise ValueError("corpo inválido")
            self._json(200, rota(corpo, estado(corpo.get("sessao"))))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self._json(400, {"erro": str(e) or "pedido inválido"})


def criar_servidor(porta: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", porta), Handler)


def rodar_cli(offline: bool = False, roteiro: bool = False) -> None:
    provedor = None if offline else _provedor_llm()
    if provedor is None:
        sessao = Sessao()
        responder = lambda m: responder_offline(m, sessao)
        print("[modo offline: respostas por regras, sem LLM]\n")
    elif provedor == "hf":
        agente = AgenteHF()
        responder = agente.conversar
        print(f"[modo llm: Hugging Face ({agente.llm_com_tools.bound.llm.repo_id})]\n")
    else:
        agente = Agente()
        responder = agente.conversar
        print(f"[modo llm: Anthropic ({agente.modelo})]\n")

    if roteiro:
        for fala in ROTEIRO_DEMO:
            print(f"Marina: {fala}\n")
            print(f"Copiloto: {responder(fala)}\n" + "-" * 60)
        return

    print("Copiloto MEI. Escreva 'sair' para encerrar.\n")
    while True:
        try:
            fala = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if fala.lower() in {"sair", "exit"}:
            break
        if fala:
            try:
                print(f"\nCopiloto: {responder(fala)}\n")
            except Exception as e:
                print(f"\n[aviso: erro no modelo ({e}); respondendo no modo offline]")
                if "sessao" not in locals():
                    sessao = agente.sessao
                print(f"Copiloto: {responder_offline(fala, sessao)}\n")


def main(argv: list[str]) -> None:
    if "--roteiro" in argv:
        rodar_cli(offline="--offline" in argv, roteiro=True)
        return
    if "--cli" in argv:
        rodar_cli(offline="--offline" in argv, roteiro=False)
        return

    porta = (
    int(argv[argv.index("--porta") + 1])
    if "--porta" in argv
    else int(os.environ.get("PORT", 8000))
    )
    srv = criar_servidor(porta)
    print(f"Copiloto MEI em http://127.0.0.1:{porta}  (modo: {modo()})  Ctrl+C para sair")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
