import json
import threading
import urllib.request

import pytest

import copiloto_mei as server


@pytest.fixture(scope="module")
def url():
    srv = server.criar_servidor(0)
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


SID = "sessao-teste-1234"


def test_paginas_estaticas_e_status(url):
    for caminho in ("/", "/app.js", "/style.css"):
        with urllib.request.urlopen(url + caminho) as r:
            assert r.status == 200 and "default-src" in r.headers["Content-Security-Policy"]
    with urllib.request.urlopen(url + "/api/status") as r:
        assert json.loads(r.read())["modo"] in ("llm", "offline")


def test_fluxo_completo_exemplo_analise_chat(url):
    s, r = post(url, "/api/exemplo", {"sessao": SID})
    assert s == 200 and len(r["notas"]) == 17
    s, a = post(url, "/api/analise", {"sessao": SID})
    assert a["acumulado"] == "67300.00" and a["mes_atual"] == 10
    assert a["cenarios"][0]["mes_estouro"] is None and a["cenarios"][1]["mes_estouro"] == 12
    s, c = post(url, "/api/chat", {"sessao": SID, "mensagem": "vou bater o teto se eu continuar assim?"})
    assert "R$ 67.300,00" in c["resposta"] and "dezembro" in c["resposta"]


def test_upload_remover_manual_e_limpar(url):
    sid = "sessao-teste-5678"
    s, r = post(url, "/api/notas", {"sessao": sid, "arquivos": [{"nome": "a.csv", "conteudo": "data;valor\n01/02/2026;1000\n"}]})
    assert s == 200 and len(r["notas"]) == 1 and r["avisos"][0] == "1 nota(s) lida(s)."
    s, r2 = post(url, "/api/notas", {"sessao": sid, "arquivos": [{"nome": "a.csv", "conteudo": "data;valor\n01/02/2026;1000\n"}]})
    assert len(r2["notas"]) == 1  # reenvio não duplica
    s, r3 = post(url, "/api/notas/manual", {"sessao": sid, "data": "2026-03-01", "valor": "250,00", "descricao": "Venda sem nota"})
    assert len(r3["notas"]) == 2
    s, r4 = post(url, "/api/notas/remover", {"sessao": sid, "id": r3["notas"][0]["id"]})
    assert len(r4["notas"]) == 1
    s, r5 = post(url, "/api/limpar", {"sessao": sid})
    assert r5["notas"] == []
    assert "erro" in post(url, "/api/analise", {"sessao": sid})[1]


def test_entradas_invalidas_dao_400(url):
    assert post(url, "/api/chat", {"sessao": "curta", "mensagem": "oi"})[0] == 400
    assert post(url, "/api/notas/manual", {"sessao": SID, "data": "x", "valor": "1"})[0] == 400
    assert post(url, "/api/notas", {"sessao": SID, "arquivos": []})[0] == 400
    assert post(url, "/api/nao-existe", {})[0] == 404
