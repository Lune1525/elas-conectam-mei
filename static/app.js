"use strict";
/* elas conectam mei: sistema MEI. Todo número exibido vem do servidor (motor de cálculo).
   O front só formata, filtra e desenha. */
(() => {
  const MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  const MES_CURTO = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const reais = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const compacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
  const brl = (v) => reais.format(Number(v || 0));
  const pct = (v) => Number(v || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%";
  const dataBR = (iso) => iso.split("-").reverse().join("/");
  const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);

  const VIEWS = {
    painel: ["Painel", "Seu ano de faturamento em um só lugar."],
    notas: ["Notas fiscais", "Envie XML ou CSV e lance receitas sem nota."],
    caixa: ["Caixa PF/PJ", "Separe o dinheiro pessoal do dinheiro do negócio."],
    preco: ["Preço mínimo", "Quanto cobrar para o serviço sustentar você."],
    teto: ["Simulador de teto", "Veja o que muda se o faturamento passar de R$ 81.000."],
    agente: ["Agente Elas", "Converse em linguagem simples. Os números vêm do motor de cálculo."],
    apoio: ["Apoio e trilhas", "Caminhos curtos e uma rede para você não empreender sozinha."],
  };

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  function h(tag, props = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === false || v == null) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    kids.flat().forEach((c) => c != null && e.append(c));
    return e;
  }

  function gerarSessao() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "sessao-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
  let sessaoId;
  try { sessaoId = localStorage.getItem("elas_conectam_mei_sessao"); } catch (_) { sessaoId = null; }
  if (!sessaoId) sessaoId = gerarSessao();
  function guardarSessao() { try { localStorage.setItem("elas_conectam_mei_sessao", sessaoId); } catch (_) { /* sem storage: segue em memória */ } }
  guardarSessao();

  async function api(url, corpo = {}) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sessao: sessaoId, ...corpo }) });
    const dados = await r.json();
    if (!r.ok) throw new Error(dados.erro || "Algo deu errado. Tente de novo.");
    return dados;
  }

  function toast(msg, erro = false) {
    const n = h("div", { class: "toast" + (erro ? " erro" : ""), text: msg });
    $("#toasts").append(n);
    setTimeout(() => n.remove(), 4800);
  }
  const falha = (e) => toast(e.message, true);

  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // ------------------------------------------------------------ estado
  const S = { notas: [], analise: null, caixa: { lancamentos: [], resumo: null }, status: {}, filtro: { q: "", mes: "" } };

  // ------------------------------------------------------------ rotas de tela
  function rotear() {
    const pedido = location.hash.replace(/^#\/?/, "");
    const v = VIEWS[pedido] ? pedido : "painel";
    $$(".view").forEach((s) => { s.hidden = s.id !== "v-" + v; });
    $$(".nav-item").forEach((a) => {
      const ativo = a.dataset.view === v;
      a.classList.toggle("ativo", ativo);
      if (ativo) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    $("#titulo").textContent = VIEWS[v][0];
    $("#subtitulo").textContent = VIEWS[v][1];
    document.title = VIEWS[v][0] + " | elas conectam mei";
    if (v === "painel" && S.analise) desenharGrafico(S.analise);
    if (v === "teto") prepararSimulador();
    if (v === "agente") { const log = $("#chat-log"); log.scrollTop = log.scrollHeight; }
    window.scrollTo(0, 0);
  }

  // ------------------------------------------------------------ carregar dados
  async function carregarNotas() {
    const r = await api("/api/notas/listar");
    S.notas = r.notas;
  }
  async function carregarAnalise(mes = null) {
    const r = await api("/api/analise", mes ? { mes_atual: mes } : {});
    S.analise = r.erro ? null : r;
  }
  async function carregarCaixa(resp = null) {
    S.caixa = resp || (await api("/api/caixa"));
  }
  async function aplicarNotas(resp, mes = null) {
    S.notas = resp.notas;
    renderAvisos(resp.avisos || []);
    await carregarAnalise(mes);
    renderTudo();
  }

  function renderTudo() {
    renderNav(); renderPainel(); renderNotas(); renderCaixa(); renderBarras();
  }

  function renderNav() {
    const n = $("#nav-notas-n");
    n.hidden = S.notas.length === 0;
    n.textContent = S.notas.length;
    const c = $("#nav-caixa-n");
    const q = S.caixa.resumo ? S.caixa.resumo.qtd_misturados : 0;
    c.hidden = !q;
    c.textContent = q;
    c.title = q ? `${q} lançamento(s) misturado(s) entre PF e PJ` : "";
  }

  // ------------------------------------------------------------ PAINEL
  function tomDe(a) {
    if (a.faixa_atual.startsWith("excesso")) return "acima";
    if (a.cenarios.some((c) => c.mes_estouro)) return "atencao";
    return "ok";
  }

  function renderPainel() {
    const a = S.analise;
    $("#painel-vazio").hidden = !!a;
    $("#painel-dados").hidden = !a;
    if (!a) return;
    const tom = tomDe(a);
    const estouros = a.cenarios.filter((c) => c.mes_estouro).sort((x, y) => x.mes_estouro - y.mes_estouro);
    const primeiro = estouros[0] || null;

    // banner
    const b = $("#banner");
    b.className = "banner " + tom;
    let titulo, texto = "";
    if (tom === "acima") {
      titulo = `Seu faturamento já passou do teto de ${brl(a.limite)}.`;
      texto = "Isso muda como o imposto é calculado. Veja o simulador e confirme com um contador ou no Portal do Empreendedor.";
    } else if (tom === "atencao") {
      const todos = a.cenarios.every((c) => c.mes_estouro);
      titulo = (todos ? "Nesse ritmo, você deve passar do teto em " : "Dependendo do ritmo, você pode passar do teto em ") + MESES[primeiro.mes_estouro - 1] + ".";
      if (a.meses_restantes) texto = `Para ficar no limite, dá para faturar em média ${brl(a.media_possivel_para_ficar_no_limite)} por mês até dezembro.`;
    } else {
      titulo = "Nesse ritmo, você termina o ano dentro do teto.";
      if (a.meses_restantes) texto = `Ainda cabem ${brl(a.margem_restante)}, cerca de ${brl(a.media_possivel_para_ficar_no_limite)} por mês até dezembro.`;
    }
    $("#banner-titulo").textContent = titulo;
    $("#banner-texto").textContent = texto;

    // KPIs
    $("#kpi-faturado").textContent = brl(a.acumulado);
    $("#kpi-faturado-sub").textContent = `até ${MESES[a.mes_atual - 1]}, ${S.notas.length} nota(s)`;
    $("#kpi-margem").textContent = brl(a.margem_restante);
    $("#kpi-margem-sub").textContent = `Teto de ${brl(a.limite)}`;
    $("#kpi-pct").textContent = pct(a.percentual_usado);
    const barra = $("#barra-pct");
    barra.className = "barra-cheia " + tom;
    barra.style.width = Math.min(Number(a.percentual_usado), 100) + "%";
    if (primeiro) {
      $("#kpi-prev-rotulo").textContent = "Mês previsto de estouro";
      $("#kpi-prev").textContent = cap(MESES[primeiro.mes_estouro - 1]);
      $("#kpi-prev-sub").textContent = a.cenarios.length > 1 && estouros.length === 1 ? `só no ${primeiro.nome}` : "nos dois ritmos";
    } else {
      $("#kpi-prev-rotulo").textContent = "Previsão até dezembro";
      $("#kpi-prev").textContent = "Dentro do teto";
      $("#kpi-prev-sub").textContent = "nos ritmos simulados";
    }

    // seletor de mês
    const sel = $("#mes-select");
    if (!sel.options.length) MESES.forEach((m, i) => sel.append(h("option", { value: i + 1, text: m })));
    sel.value = a.mes_atual;

    desenharGrafico(a);

    $("#cenarios").replaceChildren(...a.cenarios.map((c) => {
      let t, cls;
      if (!c.mes_estouro) { t = "Dentro do teto"; cls = "teto-ok"; }
      else if (c.mes_estouro <= a.mes_atual) { t = "Passou em " + MESES[c.mes_estouro - 1]; cls = "teto-passa"; }
      else { t = "Passa em " + MESES[c.mes_estouro - 1]; cls = "teto-passa"; }
      const tds = [
        h("td", { text: c.nome }),
        h("td", { class: "num", text: brl(c.media_mensal) }),
        h("td", { class: "num", text: brl(c.projecao_anual) }),
        h("td", { class: cls, text: t }),
      ];
      ["", "Por mês", "Fecha o ano com", "Teto"].forEach((rot, i) => rot && tds[i].setAttribute("data-label", rot));
      return h("tr", {}, ...tds);
    }));
    $("#premissas").replaceChildren(...[...a.premissas, ...(a.avisos || [])].map((p) => h("li", { text: p })));

    // próximos passos
    const passos = [];
    if (tom === "ok") {
      passos.push(h("li", {}, "Você está no caminho. Siga acompanhando mês a mês pelo painel."));
    } else {
      passos.push(h("li", {}, tom === "acima" ? "Veja o que muda e o custo estimado de migrar para ME." : "Veja o que acontece se o faturamento passar do teto.",
        h("a", { class: "btn btn-secondary btn-sm", href: "#/teto" }, "Abrir simulador")));
    }
    passos.push(h("li", {}, "Só contei as notas enviadas. Vendas sem nota também entram no teto.",
      h("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: () => { location.hash = "#/notas"; abrirManual(true); } }, "Lançar receita sem nota")));
    const mist = S.caixa.resumo ? S.caixa.resumo.qtd_misturados : 0;
    if (mist) {
      passos.push(h("li", {}, `${brl(S.caixa.resumo.valor_misturado)} passaram pela conta errada entre PF e PJ.`,
        h("a", { class: "btn btn-secondary btn-sm", href: "#/caixa" }, "Ver no caixa")));
    }
    passos.push(h("li", {}, "A declaração anual do MEI (DASN-SIMEI) é entregue até 31 de maio.",
      h("a", { class: "btn btn-ghost btn-sm", href: "#/agente" }, "Tirar dúvida com o Agente")));
    $("#passos").replaceChildren(...passos);

    // últimas notas
    const ult = [...S.notas].sort((x, y) => y.data.localeCompare(x.data)).slice(0, 5);
    $("#ultimas").replaceChildren(...ult.map((n) => h("tr", {},
      h("td", { text: dataBR(n.data) }), h("td", { text: n.descricao || n.origem }), h("td", { class: "num", text: brl(n.valor) }))));
  }

  // ------------------------------------------------------------ gráfico
  const NS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs = {}, texto) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (texto !== undefined) e.textContent = texto;
    return e;
  }

  function desenharGrafico(r) {
    const svg = $("#grafico");
    svg.replaceChildren();
    const W = Math.max(300, Math.round(svg.getBoundingClientRect().width)) || 680;
    const H = W < 520 ? 260 : 300;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const L = 44, R = 12, T = 18, B = 28;
    const limite = Number(r.limite), limite20 = Number(r.limite_com_tolerancia);
    const todos = [limite20, ...r.acumulado_por_mes.map(Number), ...r.cenarios.flatMap((c) => c.serie_acumulada.map(Number))];
    const topo = Math.max(...todos) * 1.06;
    const x = (m) => L + ((m - 1) / 11) * (W - L - R);
    const y = (v) => T + (1 - v / topo) * (H - T - B);

    const passo = topo > 120000 ? 40000 : 20000;
    for (let v = 0; v <= topo; v += passo) {
      svg.append(sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grade" }));
      svg.append(sv("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "eixo" }, compacto.format(v)));
    }
    MES_CURTO.forEach((n, i) => svg.append(sv("text", { x: x(i + 1), y: H - 8, "text-anchor": "middle", class: "eixo" }, n)));

    svg.append(sv("line", { x1: L, x2: W - R, y1: y(limite), y2: y(limite), class: "linha-teto" }));
    svg.append(sv("text", { x: L + 6, y: y(limite) - 6, class: "rotulo-teto" }, "Teto " + brl(limite)));
    svg.append(sv("line", { x1: L, x2: W - R, y1: y(limite20), y2: y(limite20), class: "linha-teto20" }));
    svg.append(sv("text", { x: L + 6, y: y(limite20) - 6, class: "rotulo-teto" }, "20% acima: " + brl(limite20)));

    r.cenarios.forEach((c, i) => {
      const pts = c.serie_acumulada.map((v, k) => [k + 1, Number(v)]).filter(([m]) => m >= r.mes_atual);
      svg.append(sv("polyline", { points: pts.map(([m, v]) => `${x(m)},${y(v)}`).join(" "), class: i === 0 ? "linha-c1" : "linha-c2" }));
    });
    const real = r.acumulado_por_mes.map((v, k) => [k + 1, Number(v)]);
    svg.append(sv("polyline", { points: real.map(([m, v]) => `${x(m)},${y(v)}`).join(" "), class: "linha-real" }));
    real.forEach(([m, v]) => svg.append(sv("circle", { cx: x(m), cy: y(v), r: 3.5, class: "ponto-real" })));

    const itens = [["Faturado até agora", "linha-real"], ...r.cenarios.map((c, i) => ["Projeção: " + c.nome, i === 0 ? "linha-c1" : "linha-c2"])];
    $("#legenda").replaceChildren(...itens.map(([nome, cls]) => {
      const mini = sv("svg", { viewBox: "0 0 34 8", "aria-hidden": "true" });
      mini.append(sv("line", { x1: 2, x2: 32, y1: 4, y2: 4, class: cls }));
      return h("li", {}, mini, nome);
    }));
  }
  let larguraAntes = 0;
  window.addEventListener("resize", () => {
    const w = $("#grafico").getBoundingClientRect().width;
    if (S.analise && w && Math.abs(w - larguraAntes) > 8) { larguraAntes = w; desenharGrafico(S.analise); }
  });

  // ------------------------------------------------------------ NOTAS
  function renderAvisos(avisos) {
    const ul = $("#avisos-notas");
    ul.hidden = !avisos.length;
    ul.replaceChildren(...avisos.map((a) => h("li", { text: a })));
  }

  function renderNotas() {
    const filtroMes = $("#filtro-mes");
    if (filtroMes.options.length <= 1) MESES.forEach((m, i) => filtroMes.append(h("option", { value: String(i + 1).padStart(2, "0"), text: cap(m) })));
    const q = S.filtro.q.trim().toLowerCase();
    const lista = S.notas.filter((n) =>
      (!S.filtro.mes || n.data.slice(5, 7) === S.filtro.mes) &&
      (!q || (n.descricao + " " + n.origem).toLowerCase().includes(q)));
    $("#tabela-notas").hidden = lista.length === 0;
    $("#notas-vazio").hidden = S.notas.length > 0;
    $("#btn-limpar-notas").hidden = S.notas.length === 0;
    $("#notas-corpo").replaceChildren(...lista.map((n) => {
      const rem = h("button", { type: "button", class: "rem", title: "Remover", "aria-label": `Remover nota de ${dataBR(n.data)}, ${brl(n.valor)}`, onclick: async () => {
        try { await aplicarNotas(await api("/api/notas/remover", { id: n.id })); toast("Nota removida."); } catch (e) { falha(e); }
      } }, "×");
      return h("tr", {}, h("td", { text: dataBR(n.data) }), h("td", { text: n.descricao || "Nota fiscal" }),
        h("td", { text: n.origem }), h("td", { class: "num", text: brl(n.valor) }), h("td", {}, rem));
    }));
    $("#notas-contagem").textContent = S.notas.length
      ? `${lista.length} de ${S.notas.length} nota(s) na lista.` : "";
  }

  function renderBarras() {
    const box = $("#barras");
    const a = S.analise;
    if (!a) { box.replaceChildren(); $("#barras-legenda").textContent = "Envie notas para ver o ritmo de cada mês."; return; }
    const max = Math.max(...Object.values(a.por_mes).map(Number), 1);
    box.replaceChildren(...MES_CURTO.map((nome, i) => {
      const v = a.por_mes[String(i + 1)];
      const tem = v !== undefined;
      const b = h("div", { class: "b" });
      b.style.height = tem ? Math.max(Number(v) / max * 100, 1) * 0.72 + "%" : "2px";
      return h("div", { class: "barra-col" + (tem ? "" : " futuro"), title: tem ? `${cap(MESES[i])}: ${brl(v)}` : "" },
        tem && (i + 1 === a.mes_atual || Number(v) === max) ? h("span", { class: "valor", text: compacto.format(Number(v)) }) : null, b, h("span", { class: "mes", text: nome }));
    }));
    $("#barras-legenda").textContent = `Soma das notas de cada mês até ${MESES[a.mes_atual - 1]}.`;
  }

  async function enviarArquivos(files) {
    const lista = [];
    for (const f of files) {
      if (f.size > 2 * 1024 * 1024) { toast(`${f.name}: arquivo grande demais (limite de 2 MB).`, true); continue; }
      lista.push({ nome: f.name, conteudo: await f.text() });
    }
    if (!lista.length) return;
    try { await aplicarNotas(await api("/api/notas", { arquivos: lista })); } catch (e) { falha(e); }
  }

  function abrirManual(abrir) {
    const f = $("#manual-form");
    f.hidden = abrir === undefined ? !f.hidden : !abrir;
    if (!f.hidden) {
      if (!$("#manual-data").value && S.status.hoje) $("#manual-data").value = S.status.hoje;
      $("#manual-valor").focus();
    }
  }

  // ------------------------------------------------------------ CAIXA
  function renderCaixa() {
    const { lancamentos, resumo } = S.caixa;
    const r = resumo || {};
    $("#cx-saldo").textContent = brl(r.saldo_negocio);
    $("#cx-entradas").textContent = brl(r.entradas_negocio);
    $("#cx-saidas").textContent = brl(r.saidas_negocio);
    $("#cx-mistura").textContent = brl(r.valor_misturado);
    $("#cx-mistura-sub").textContent = r.qtd_misturados ? `${r.qtd_misturados} lançamento(s) misturado(s)` : "Nada misturado por enquanto";
    $("#tabela-caixa").hidden = !lancamentos.length;
    $("#caixa-vazio").hidden = lancamentos.length > 0;
    $("#btn-caixa-limpar").hidden = !lancamentos.length;
    $("#caixa-corpo").replaceChildren(...lancamentos.map((l) => {
      const rem = h("button", { type: "button", class: "rem", title: "Remover", "aria-label": `Remover lançamento ${l.descricao || ""} de ${brl(l.valor)}`, onclick: async () => {
        try { await carregarCaixa(await api("/api/caixa/remover", { indice: l.indice })); renderCaixa(); renderNav(); renderPainel(); } catch (e) { falha(e); }
      } }, "×");
      return h("tr", {},
        h("td", {}, h("span", { class: "chip " + (l.tipo === "entrada" ? "chip-ok" : "chip-neutro"), text: l.tipo === "entrada" ? "Entrada" : "Saída" })),
        h("td", {}, l.descricao || "Sem descrição", l.misturado ? h("span", { class: "chip misturado", text: "Misturado" }) : null),
        h("td", { text: l.conta }), h("td", { text: l.natureza === "negocio" ? "Negócio" : "Pessoal" }),
        h("td", { class: "num", text: brl(l.valor) }), h("td", {}, rem));
    }));
  }

  // ------------------------------------------------------------ PREÇO
  async function calcularPreco() {
    const v = (id) => $(id).value.trim();
    const erro = $("#preco-erro");
    if (!v("#p-fixos") || !v("#p-pro") || !v("#p-qtd")) {
      $("#preco-res").hidden = true; $("#preco-vazio").hidden = false; erro.hidden = true; return;
    }
    try {
      const r = await api("/api/preco", { custos_fixos: v("#p-fixos"), custo_variavel: v("#p-var"), pro_labore: v("#p-pro"),
        servicos: v("#p-qtd"), tipo_atividade: $("#p-tipo").value, margem: v("#p-margem") });
      erro.hidden = true; $("#preco-vazio").hidden = true; $("#preco-res").hidden = false;
      $("#pr-minimo").textContent = brl(r.preco_minimo);
      $("#pr-sugerido").textContent = brl(r.preco_sugerido);
      const c = r.composicao;
      const partes = [["Custos fixos", c.custos_fixos_por_servico, "seg-a"], ["Sua retirada (pró-labore)", c.pro_labore_por_servico, "seg-b"],
        ["Imposto (DAS)", c.das_por_servico, "seg-c"], ["Custo de cada serviço", c.custo_variavel, "seg-d"]];
      const total = partes.reduce((s, p) => s + Number(p[1]), 0) || 1;
      $("#pr-barra").replaceChildren(...partes.filter((p) => Number(p[1]) > 0).map((p) => {
        const s = h("span", { class: p[2] }); s.style.width = (Number(p[1]) / total * 100) + "%"; return s;
      }));
      $("#pr-comp").replaceChildren(...partes.map((p) => h("tr", {},
        h("td", {}, h("span", { class: "amostra " + p[2] }), p[0]), h("td", { class: "num", text: brl(p[1]) }))));
      $("#pr-prem").replaceChildren(...r.premissas.map((p) => h("li", { text: p })));
    } catch (e) {
      $("#preco-res").hidden = true; $("#preco-vazio").hidden = true; erro.hidden = false; erro.textContent = e.message;
    }
  }

  // ------------------------------------------------------------ SIMULADOR
  function prepararSimulador() {
    const ab = $("#s-abertura");
    if (ab.options.length <= 1) MESES.forEach((m, i) => ab.append(h("option", { value: i + 1, text: cap(m) })));
    const a = S.analise;
    $("#usar-proj").hidden = !a;
    if (a) {
      $("#usar-proj-botoes").replaceChildren(...a.cenarios.map((c) => h("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: () => {
        $("#s-total").value = Number(c.projecao_anual).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        simular();
      } }, `${cap(c.nome)} (${brl(c.projecao_anual)})`)));
    }
  }

  async function simular() {
    const erro = $("#sim-erro");
    try {
      const r = await api("/api/simular", { faturamento_total: $("#s-total").value, tipo_atividade: $("#s-tipo").value,
        aliquota: $("#s-aliq").value, mes_abertura: $("#s-abertura").value });
      erro.hidden = true; $("#sim-vazio").hidden = true; $("#sim-res").hidden = false;
      const faixa = { dentro_do_teto: ["Dentro do teto", "chip-ok"], excesso_ate_20: ["Passou do teto em até 20%", "chip-warn"], excesso_acima_20: ["Passou mais de 20% do teto", "chip-danger"] }[r.faixa];
      const chip = $("#sim-faixa"); chip.className = "chip " + faixa[1]; chip.textContent = faixa[0];
      $("#sim-texto").textContent = r.explicacao;
      const limite = r.premissas[0].match(/limite de (R\$ [\d.,]+)/);
      $("#sim-limite").textContent = limite ? limite[1].replace(/[.,]+$/, "") : "R$ 81.000,00";
      $("#sim-excesso").textContent = r.faixa === "dentro_do_teto" ? "Não passou" : `${brl(r.excesso)} (${pct(r.percentual_excesso)})`;
      $("#sim-imposto").textContent = brl(r.imposto_estimado);
      $("#sim-prem").replaceChildren(...r.premissas.map((p) => h("li", { text: p })));
    } catch (e) {
      $("#sim-res").hidden = true; $("#sim-vazio").hidden = true; erro.hidden = false; erro.textContent = e.message;
    }
  }

  // ------------------------------------------------------------ AGENTE
  const BOAS_VINDAS = "Oi! Eu sou o Agente Elas. Escreva do seu jeito, você não precisa conhecer os termos certos. Posso olhar seu teto (inclusive pelas notas que você enviou), separar PF e PJ, calcular seu preço mínimo ou só ouvir como está a rotina.";
  function adicionarMensagem(autor, texto, meta = "") {
    const log = $("#chat-log");
    log.append(h("div", { class: "msg " + (autor === "user" ? "msg-user" : "msg-bot") },
      h("div", { class: "msg-bubble", text: texto }), meta ? h("span", { class: "msg-meta", text: meta }) : null));
    log.scrollTop = log.scrollHeight;
  }

  async function enviarMensagem(texto) {
    const msg = texto.trim();
    if (!msg) return;
    if (location.hash !== "#/agente") location.hash = "#/agente";
    const input = $("#chat-input"), btn = $("#chat-send");
    adicionarMensagem("user", msg);
    input.value = ""; btn.disabled = true; btn.textContent = "Pensando...";
    try {
      const r = await api("/api/chat", { mensagem: msg });
      adicionarMensagem("bot", r.resposta, r.aviso || (r.modo === "llm" ? "Agente Elas, IA ativa" : "Agente Elas, modo offline"));
      // o agente pode ter registrado lançamentos: atualiza o caixa
      await carregarCaixa(); renderCaixa(); renderNav();
    } catch (e) { adicionarMensagem("bot", "Não consegui responder agora. " + e.message); }
    finally { btn.disabled = false; btn.textContent = "Enviar"; input.focus(); }
  }

  // ------------------------------------------------------------ eventos
  async function carregarExemplo() {
    try {
      const [n, c] = await Promise.all([api("/api/exemplo"), api("/api/caixa/exemplo")]);
      await carregarCaixa(c);
      await aplicarNotas(n);
      toast("Dados de exemplo da Marina carregados (fictícios).");
    } catch (e) { falha(e); }
  }

  function ligarEventos() {
    window.addEventListener("hashchange", rotear);
    $("#btn-exemplo").addEventListener("click", carregarExemplo);
    $$("[data-acao='exemplo']").forEach((b) => b.addEventListener("click", carregarExemplo));

    $("#btn-nova-sessao").addEventListener("click", async () => {
      if (!confirm("Começar uma nova sessão? As notas e lançamentos desta sessão serão descartados.")) return;
      sessaoId = gerarSessao(); guardarSessao();
      S.notas = []; S.analise = null; S.caixa = { lancamentos: [], resumo: null };
      await carregarCaixa();
      $("#chat-log").replaceChildren(); adicionarMensagem("bot", BOAS_VINDAS);
      renderAvisos([]); renderTudo(); location.hash = "#/painel"; toast("Nova sessão iniciada.");
    });

    // notas
    $("#file-input").addEventListener("change", (e) => { enviarArquivos(e.target.files); e.target.value = ""; });
    const dz = $("#dropzone");
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("sobre"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("sobre"); }));
    dz.addEventListener("drop", (e) => e.dataTransfer && enviarArquivos(e.dataTransfer.files));
    $("#busca-notas").addEventListener("input", (e) => { S.filtro.q = e.target.value; renderNotas(); });
    $("#filtro-mes").addEventListener("change", (e) => { S.filtro.mes = e.target.value; renderNotas(); });
    $("#btn-manual").addEventListener("click", () => abrirManual());
    $("#btn-limpar-notas").addEventListener("click", async () => {
      try { await aplicarNotas(await api("/api/limpar")); toast("Notas removidas."); } catch (e) { falha(e); }
    });
    $("#manual-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/notas/manual", { data: $("#manual-data").value, valor: $("#manual-valor").value, descricao: $("#manual-desc").value });
        $("#manual-valor").value = ""; $("#manual-desc").value = "";
        await aplicarNotas(r); toast("Receita lançada.");
      } catch (err) { falha(err); }
    });
    $("#mes-select").addEventListener("change", async (e) => {
      try { await carregarAnalise(Number(e.target.value)); renderPainel(); renderBarras(); } catch (err) { falha(err); }
    });

    // caixa
    $("#caixa-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api("/api/caixa/registrar", { tipo: $("input[name='cx-tipo']:checked").value, conta: $("#cx-conta").value,
          natureza: $("#cx-natureza").value, valor: $("#cx-valor").value, descricao: $("#cx-desc").value });
        await carregarCaixa(r); $("#cx-valor").value = ""; $("#cx-desc").value = "";
        renderCaixa(); renderNav(); renderPainel(); toast("Lançamento registrado."); $("#cx-valor").focus();
      } catch (err) { falha(err); }
    });
    $("#btn-caixa-limpar").addEventListener("click", async () => {
      try { await carregarCaixa(await api("/api/caixa/limpar")); renderCaixa(); renderNav(); renderPainel(); } catch (e) { falha(e); }
    });

    // preço e simulador
    const recalcular = debounce(calcularPreco, 250);
    $$("#preco-form input, #preco-form select").forEach((el) => el.addEventListener("input", recalcular));
    $("#preco-form").addEventListener("submit", (e) => e.preventDefault());
    $("#sim-form").addEventListener("submit", (e) => { e.preventDefault(); simular(); });

    // agente
    $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); enviarMensagem($("#chat-input").value); });
    $("#chat-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviarMensagem($("#chat-input").value); }
    });
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-prompt]");
      if (b) enviarMensagem(b.dataset.prompt);
    });
    $("#btn-limpar-chat").addEventListener("click", async () => {
      try { await api("/api/chat/limpar"); } catch (_) { /* segue limpando a tela */ }
      $("#chat-log").replaceChildren(); adicionarMensagem("bot", BOAS_VINDAS);
    });
  }

  // ------------------------------------------------------------ início
  async function iniciar() {
    ligarEventos();
    adicionarMensagem("bot", BOAS_VINDAS);
    try {
      S.status = await (await fetch("/api/status")).json();
      $("#badge-ano").textContent = `MEI ${S.status.ano}`;
      const m = $("#badge-modo");
      m.textContent = S.status.modo === "llm" ? "IA ativa" : "Modo offline (sem IA)";
      m.classList.toggle("ia", S.status.modo === "llm");
    } catch (_) { $("#badge-modo").textContent = "Sem conexão com o servidor"; }
    try {
      await Promise.all([carregarNotas(), carregarCaixa()]);
      await carregarAnalise();
    } catch (e) { falha(e); }
    renderTudo();
    rotear();
  }
  iniciar();
})();
