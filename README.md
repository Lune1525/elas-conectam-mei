# elas conectam mei — Agente Elas (Copiloto MEI)

> **Feita com e para mulheres MEI — Você não precisa empreender sozinha.**

Uma iniciativa de apoio, troca e tecnologia feita para mulheres que empreendem. O projeto combina o **Agente Elas** (assistente de IA acolhedor conectado a um motor determinístico de cálculo), **consolidação de notas fiscais (XML e CSV)**, **trilhas práticas** e **curadoria de comunidade**.

---

## Funcionalidades

- **Agente Elas (IA Acolhedora + Motor Determinístico)**
  - Responde em linguagem clara, sem juridiquês nem economês, apoiado por ferramentas que calculam cada centavo sem "conta de cabeça".
- **Termômetro e Projeção do Teto MEI (R$ 81.000,00 / R$ 6.750,00 por mês proporcional)**
  - Calcula percentual utilizado, margem restante e média mensal possível até dezembro.
  - Simula cenários de ritmo médio do ano e dos últimos 3 meses, apontando em qual mês o teto seria ultrapassado.
- **Simulação de Transição para Microempresa (ME)**
  - Estima o impacto financeiro quando o faturamento ultrapassa o teto em **até 20%** (imposto complementar sobre o excedente e migração em janeiro) ou **acima de 20%** (efeito retroativo a janeiro).
- **Separação de Caixa PF x PJ**
  - Registra entradas e saídas identificando a conta utilizada (`PF` ou `PJ`) e a natureza (`negócio` ou `pessoal`), sinalizando valores misturados sem julgamento.
- **Calculadora de Preço Mínimo**
  - Divide custos fixos, pró-labore desejado e DAS mensal pelo volume de atendimentos, somando custo variável e margem de segurança.
- **Consolidação de Notas Fiscais (XML e CSV) e Receitas Manuais**
  - Lê arquivos NF-e / NFS-e (`.xml`) e planilhas (`.csv`), ignora duplicatas automaticamente e permite lançar receitas sem nota.
- **Trilhas Práticas & Comunidade**
  - Atalhos contextuais (*Finanças sem mistura*, *Preço que sustenta*, *Meu MEI está crescendo* e *Semana possível*) que já abrem o Agente com o tema selecionado, além de curadoria de apoio para dupla jornada e isolamento.

---

## Telas do Sistema

O front é um sistema com menu lateral (não uma página de apresentação). Cada tela chama o motor de cálculo direto; o Agente é só mais uma tela.

| Tela | O que faz |
|---|---|
| **Painel** | Faixa de status, KPIs, gráfico do acumulado com teto e limite de 20%, cenários de ritmo, próximos passos e últimas notas |
| **Notas fiscais** | Envio de XML/CSV (arrastar e soltar), busca e filtro por mês, receita sem nota, faturamento por mês |
| **Caixa PF/PJ** | Lançamentos de entrada e saída, saldo do negócio e destaque do dinheiro misturado |
| **Preço mínimo** | Calculadora em 3 passos com resultado ao vivo e composição do preço |
| **Simulador de teto** | Estimativa do que acontece ao passar de R$ 81.000, com limite proporcional para quem abriu no ano |
| **Agente Elas** | Chat com IA (ou modo offline), que usa as mesmas notas e o mesmo caixa |
| **Apoio e trilhas** | Trilhas rápidas, rodas de conversa e rede de apoio sugerida |

## Estrutura do Projeto

```text
mei/
├── copiloto_mei.py          # Backend: motor financeiro, parser XML/CSV, Agente, rotas e servidor HTTP
├── README.md
├── static/
│   ├── index.html           # Shell do sistema: menu lateral e as 7 telas
│   ├── style.css            # Identidade elas conectam mei (roxo/lilás, Plus Jakarta Sans), responsivo
│   └── app.js               # Roteamento por hash, formulários, gráfico e chat
├── exemplos/                # Notas fictícias da jornada de demonstração
└── test_*.py                # Testes (pytest)
```

---

## Como Executar

### 1. Modo offline (zero dependências externas)

Basta ter **Python 3.10+** instalado:

```powershell
python copiloto_mei.py
```

Acesse no navegador: `http://127.0.0.1:8000`

### 2. Modo com LLM via Hugging Face

Instale os pacotes leves do LangChain + Hugging Face (usando `python -m pip` caso políticas do Windows bloqueiem `pip.exe`):

```powershell
python -m pip install --user langchain-core langchain-huggingface huggingface_hub
```

Configure seu token da Hugging Face e inicie o servidor:

```powershell
$env:HF_TOKEN = "hf_seu_token_aqui"
$env:HF_MODEL = "Qwen/Qwen2.5-7B-Instruct"
python copiloto_mei.py
```

### 3. Outros modos de execução via terminal

```powershell
python copiloto_mei.py --porta 8080     # Inicia o servidor web em outra porta
python copiloto_mei.py --cli            # Chat interativo direto no terminal
python copiloto_mei.py --cli --offline  # Chat no terminal apenas com regras locais
python copiloto_mei.py --roteiro        # Executa a jornada de demonstração da Marina
```

---

## Privacidade e Premissas

- **Sessão em memória:** nenhum dado financeiro ou nota fiscal é gravado em banco de dados ou disco; ao reiniciar o servidor ou clicar em *Nova conversa*, os dados são descartados.
- **Caráter educativo:** as simulações de imposto e desenquadramento são estimativas de planejamento e não substituem orientação contábil ou jurídica formal.

---

## Testes

```powershell
python -m pip install --user pytest
python -m pytest -q
```
