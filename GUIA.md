# Guia do Fluxor

Tudo que você precisa para usar o Fluxor, do zero ao deploy.

**Índice**

1. [Instalação](#1-instalação)
2. [Seu primeiro workflow](#2-seu-primeiro-workflow)
3. [Como o motor executa](#3-como-o-motor-executa)
4. [Referência do YAML](#4-referência-do-yaml)
5. [Templates e filtros](#5-templates-e-filtros)
6. [Catálogo de actions](#6-catálogo-de-actions)
7. [Linha de comando](#7-linha-de-comando)
8. [API REST e dashboard](#8-api-rest-e-dashboard)
9. [Agendamento](#9-agendamento)
10. [Configuração](#10-configuração)
11. [Receitas práticas](#11-receitas-práticas)
12. [Deploy em produção](#12-deploy-em-produção)
13. [Problemas comuns](#13-problemas-comuns)
14. [Estendendo o Fluxor](#14-estendendo-o-fluxor)

---

## 1. Instalação

Requisito: **Python 3.11 ou superior**. Confira com `python --version`.

### Opção A — a partir do repositório (recomendada para desenvolver)

```bash
git clone https://github.com/NeithanDev-Arch/fluxor.git
cd fluxor

python -m venv .venv

# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (cmd)
.venv\Scripts\activate.bat

pip install -e ".[dev]"
```

O `-e` instala em modo editável: você altera o código e a mudança vale na hora, sem reinstalar. O `[dev]` traz pytest, ruff e mypy junto.

Confira:

```bash
fluxor --version
fluxor actions
```

### Opção B — só para usar

```bash
pip install git+https://github.com/NeithanDev-Arch/fluxor.git
```

### Opção C — Docker

```bash
docker compose up -d          # dashboard + agendador em http://localhost:8000
docker compose logs -f        # acompanhar
docker compose down           # parar
```

O `docker-compose.yml` monta `./examples` dentro do container como somente-leitura. Edite o YAML na sua máquina e clique em **recarregar YAML** no dashboard — não precisa reconstruir a imagem.

### Configurando segredos

```bash
cp .env.example .env
```

Preencha o que for usar. O `.env` está no `.gitignore` e **nunca** deve ir para o repositório.

---

## 2. Seu primeiro workflow

### 2.1 Rode o exemplo que já vem pronto

```bash
fluxor run examples/hello-mundo.yaml
```

Ele não usa internet nem chave de API. Você deve ver algo assim:

```
  ✔ saudacao      flow.set              3ms
  ✔ numerados     transform.map         3ms
  ✔ relatorio     transform.template    2ms
  ✔ mostrar       notify.log            0ms
  ⊘ so_para_listas_grandes  notify.log  0ms (when: vars.itens | length > 5)

SUCCESS  4 ok · 0 falhas · 1 pulados · 49ms
```

Os símbolos: `✔` executou, `✘` falhou, `⊘` foi pulado porque a condição `when` deu falso.

### 2.2 Crie o seu

```bash
fluxor init meus-workflows
```

Isso cria `meus-workflows/meu-primeiro-workflow.yaml` já comentado. Ou escreva do zero — crie `cotacao.yaml`:

```yaml
name: minha-cotacao
description: Busca o dólar e mostra no log

trigger:
  type: manual

vars:
  par: USD-BRL

steps:
  - id: buscar
    use: http.get
    with:
      url: "https://economia.awesomeapi.com.br/json/last/{{ vars.par }}"

  - id: valor
    use: parse.json
    with:
      data: "{{ steps.buscar.json }}"
      path: "USDBRL.bid"

  - id: mostrar
    use: notify.log
    with:
      message: "Dólar: {{ steps.valor | brl }}"
```

Valide antes de rodar:

```bash
fluxor validate cotacao.yaml
```

Simule sem causar nenhum efeito:

```bash
fluxor run cotacao.yaml --dry-run
```

O `--dry-run` resolve todas as variáveis e valida os parâmetros de cada action, mas **não executa nada**: não faz requisição, não escreve arquivo, não manda mensagem. É a forma segura de conferir se os templates estão certos.

Execute de verdade:

```bash
fluxor run cotacao.yaml
```

### 2.3 Entenda o encadeamento

Os três passos acima ilustram a ideia central:

```
buscar  →  publica a resposta HTTP em {{ steps.buscar }}
valor   →  lê {{ steps.buscar.json }} e publica o preço em {{ steps.valor }}
mostrar →  lê {{ steps.valor }} e formata
```

Cada passo enxerga a saída de **todos** os anteriores. É isso, e só isso, que liga um passo ao outro.

---

## 3. Como o motor executa

Para cada passo, sempre na mesma ordem:

```
1. when       → condição falsa? marca "skipped" e vai para o próximo passo
2. foreach    → resolve a lista e prepara uma execução por item
3. with       → renderiza os {{ }} com o contexto atual
4. schema     → valida os parâmetros contra o modelo da action
5. executar   → com retry e timeout
6. registrar  → guarda a saída em steps.<id> e grava no banco
```

**Se um passo falha:**

- `on_error: fail` (padrão) — o workflow para, roda os passos de `on_failure` e termina com status `failed`.
- `on_error: continue` — a falha é registrada, o workflow segue, e o status final é `partial`.

**Status possíveis de uma execução:**

| Status | Quando acontece |
|---|---|
| `success` | Todos os passos terminaram bem (pulados contam como bem). |
| `partial` | Terminou até o fim, mas algum passo com `on_error: continue` falhou. |
| `failed` | Um passo abortou o fluxo, ou o workflow estourou o timeout. |

Um detalhe importante: **passo pulado não publica saída**. Se o passo `b` usa `{{ steps.a }}` e `a` foi pulado, `b` falha com uma mensagem clara. É proposital — melhor um erro explícito do que um valor vazio circulando pelo fluxo.

---

## 4. Referência do YAML

### 4.1 Campos do workflow

```yaml
name: monitor-precos        # obrigatório · minúsculas, números, ".", "-", "_"
description: O que ele faz  # opcional · aparece na CLI e no dashboard
version: 1                  # opcional · seu controle, o motor não usa

trigger: { ... }            # opcional · padrão: manual
env: [NOME_DA_VAR]          # opcional · allowlist de variáveis de ambiente
vars: { ... }               # opcional · valores do workflow
timeout: 300                # opcional · segundos para o workflow inteiro

steps: [ ... ]              # obrigatório · pelo menos um
on_failure: [ ... ]         # opcional · compensação quando falha
```

Qualquer chave fora dessa lista causa erro de validação. É de propósito: `descripton` em vez de `description` precisa falhar, não ser ignorado silenciosamente.

### 4.2 `trigger` — o que dispara

```yaml
# Manual (padrão): só roda com `fluxor run` ou pelo dashboard
trigger:
  type: manual

# Agendado: cron de 5 campos
trigger:
  type: schedule
  cron: "0 9 * * 1-5"              # 9h, de segunda a sexta
  timezone: America/Sao_Paulo      # opcional · padrão: FLUXOR_TIMEZONE

# Webhook: disparado por POST na API
trigger:
  type: webhook
  token: um-segredo-longo-e-aleatorio
```

O cron é validado no carregamento — expressão inválida é erro de arquivo, não surpresa às 3 da manhã.

Formato: `minuto hora dia mês dia-da-semana`

| Expressão | Significado |
|---|---|
| `*/15 * * * *` | a cada 15 minutos |
| `0 * * * *` | de hora em hora, no minuto 0 |
| `0 9 * * 1-5` | 9h, dias úteis |
| `0 0 1 * *` | meia-noite do dia 1 de cada mês |
| `30 7 * * 0` | 7h30 de domingo |

### 4.3 `vars` — valores do workflow

```yaml
vars:
  base_url: https://api.exemplo.com
  endpoint: "{{ vars.base_url }}/v1/pedidos"     # pode usar as anteriores
  limite: 100
  ativo: true
  cidades:
    - { nome: "São Paulo", uf: SP }
    - { nome: "Recife", uf: PE }
```

São resolvidas **em ordem**, então uma var pode usar as declaradas acima dela.

Sobrescreva na hora de rodar:

```bash
fluxor run meu-workflow --var limite=10 --var ativo=false
```

O valor passa por JSON, então os tipos funcionam: `limite=10` vira inteiro, `ativo=false` vira booleano, e qualquer coisa que não seja JSON válido continua string. Para forçar string, use aspas: `--var nome='"texto"'`.

### 4.4 `env` — segredos, com allowlist

```yaml
env:
  - TELEGRAM_BOT_TOKEN
  - DATABASE_PASSWORD
```

Só o que está nessa lista aparece em `{{ env.NOME }}`. O resto do ambiente é invisível para o workflow, mesmo que exista no processo. Um YAML que você não escreveu não consegue ler a sua chave da AWS.

Se a variável não estiver definida, o Fluxor registra um aviso no carregamento e `{{ env.NOME }}` falha com mensagem clara na hora do uso.

### 4.5 `steps` — os passos

```yaml
steps:
  - id: buscar_pedidos          # obrigatório · único · vira {{ steps.buscar_pedidos }}
    use: http.get               # obrigatório · nome da action
    description: Busca na API   # opcional · documentação

    with:                       # parâmetros da action (renderizados)
      url: "{{ vars.endpoint }}"
      headers:
        Authorization: "Bearer {{ env.API_TOKEN }}"

    when: "vars.ativo"          # opcional · condição booleana
    foreach: "{{ vars.itens }}" # opcional · roda uma vez por item
    timeout: 30                 # opcional · segundos, por tentativa
    on_error: fail              # opcional · fail (padrão) ou continue

    retry:                      # opcional
      attempts: 3               # total, incluindo a primeira · 1 a 20
      delay: 2                  # segundos de base
      backoff: exponential      # fixed | linear | exponential
      max_delay: 60             # teto do intervalo
      jitter: true              # ±25% de ruído
```

Regras do `id`: letras, números e `_`, começando por letra ou `_`. Não pode ser `vars`, `env`, `run`, `steps`, `item`, `index` ou `error` — são nomes que o motor injeta no contexto.

**Como o backoff cresce** (com `delay: 2`):

| Estratégia | 1ª espera | 2ª | 3ª | 4ª |
|---|---|---|---|---|
| `fixed` | 2s | 2s | 2s | 2s |
| `linear` | 2s | 4s | 6s | 8s |
| `exponential` | 2s | 4s | 8s | 16s |

Sempre limitado por `max_delay`. Com `jitter: true`, cada valor recebe ±25% de variação aleatória — o que evita que dez workers que falharam juntos voltem exatamente juntos.

**Retry não é aplicado a erro permanente.** Um 404, um campo obrigatório faltando ou um seletor CSS que não casou não são retentados: repetir daria o mesmo resultado.

### 4.6 `when` — passo condicional

```yaml
when: "steps.preco.valor <= vars.teto"
when: "{{ steps.preco.valor <= vars.teto }}"   # as chaves são opcionais
when: "steps.resposta.json.itens | length > 0"
when: "steps.status.texto != 'ok' and vars.alertar"
```

Se der falso, o passo fica com status `skipped` e o workflow continua normalmente.

Strings que APIs costumam devolver como negativas — `""`, `"false"`, `"no"`, `"0"`, `"none"`, `"null"` — são tratadas como falso.

### 4.7 `foreach` — um passo, vários itens

```yaml
- id: consultar
  use: http.get
  foreach: "{{ vars.cidades }}"
  with:
    url: "https://api.exemplo.com/clima?cidade={{ item.nome }}"
```

Dentro do passo você tem `{{ item }}` (o elemento) e `{{ index }}` (a posição, começando em 0).

A saída vira uma **lista**, na mesma ordem da entrada:

```yaml
- id: resumo
  use: transform.map
  with:
    items: "{{ vars.cidades }}"
    expr: "{{ item.nome }}: {{ steps.consultar[index].json.temperatura }}°C"
```

Os itens rodam **em paralelo**, limitados por `FLUXOR_FOREACH_CONCURRENCY` (5 por padrão). Quatro requisições HTTP levam o tempo da mais lenta, não a soma das quatro.

Se qualquer item falhar, o passo inteiro falha — e aí valem as regras normais de `on_error`.

### 4.8 `on_failure` — compensação

```yaml
on_failure:
  - id: avisar_time
    use: notify.telegram
    with:
      token: "{{ env.TELEGRAM_BOT_TOKEN }}"
      chat_id: "{{ env.TELEGRAM_CHAT_ID }}"
      text: "🚨 {{ run.workflow }} falhou: {{ error }}"
```

Roda quando o workflow termina em `failed`. Dentro dele, `{{ error }}` traz a mensagem, e as saídas dos passos que chegaram a rodar continuam acessíveis em `{{ steps.* }}`.

Falha dentro do `on_failure` é registrada no log, mas não gera cascata.

---

## 5. Templates e filtros

### 5.1 O que existe no contexto

| Nome | Conteúdo | Onde vale |
|---|---|---|
| `vars` | Variáveis do workflow, já resolvidas | sempre |
| `env` | Variáveis de ambiente da allowlist | sempre |
| `steps` | Saída dos passos concluídos, por `id` | sempre |
| `run.id` | Identificador da execução | sempre |
| `run.workflow` | Nome do workflow | sempre |
| `run.trigger` | `manual`, `schedule`, `api` ou `webhook` | sempre |
| `run.started_at` | Início, em ISO 8601 | sempre |
| `item` | Elemento da iteração | dentro de `foreach` e nas actions `transform.*` |
| `index` | Posição do elemento (base 0) | idem |
| `error` | Mensagem do erro | dentro de `on_failure` |
| `vars.payload` | Corpo JSON do POST | em workflows do tipo `webhook` |

### 5.2 Tipagem nativa — o detalhe que evita bugs

Uma string que é **apenas** uma expressão preserva o tipo original:

```yaml
limite: "{{ vars.teto }}"          # -> 2500      (int)
lista:  "{{ vars.itens }}"         # -> [1, 2, 3] (list)
objeto: '{{ {"a": 1} }}'           # -> {"a": 1}  (dict)
texto:  "Teto de {{ vars.teto }}"  # -> "Teto de 2500" (str)
```

Sem isso, `when: "steps.preco.valor > 10"` compararia strings — e `"9" > "10"` é verdadeiro em comparação de texto. Esse é exatamente o tipo de bug que custa uma tarde inteira para achar.

### 5.3 Filtros do Fluxor

| Filtro | O que faz | Exemplo |
|---|---|---|
| `to_number` | Texto → número, entendendo pt-BR e en-US | `"R$ 2.499,90"` → `2499.9` |
| `to_int` | Texto → inteiro | `"42.9"` → `42` |
| `to_json` | Estrutura → texto JSON | `{"a":1}` → `'{"a": 1}'` |
| `from_json` | Texto JSON → estrutura | `'{"a":1}'` → `{"a": 1}` |
| `brl` | Formata como moeda brasileira | `2499.9` → `"R$ 2.499,90"` |
| `slugify` | Texto → slug de URL | `"Olá Mundo!"` → `"olá-mundo"` |
| `strip_html` | Remove tags, devolve o texto | `"<b>oi</b>"` → `"oi"` |
| `regex_search` | Extrai por regex | `"v2.1" \| regex_search("\\d+\\.\\d+")` → `"2.1"` |
| `regex_replace` | Substitui por regex | `"a1b2" \| regex_replace("\\d", "")` → `"ab"` |
| `path` | Navega caminho pontilhado | `dados \| path("itens.0.nome")` |
| `as_list` | Garante lista | `"x"` → `["x"]` |
| `b64` | Codifica em base64 | para autenticação Basic |
| `sha256` | Hash hexadecimal | para assinar webhooks |

Todos os filtros nativos do Jinja continuam disponíveis: `upper`, `lower`, `length`, `join`, `default`, `round`, `sort`, `sum`, `first`, `last`, `replace`, `trim`, `map`, `select`, `tojson`…

### 5.4 Funções globais

```yaml
"{{ now() }}"            # datetime UTC — use .isoformat() para texto
"{{ today() }}"          # "2026-08-11"
"{{ timestamp() }}"      # 1786545600 (epoch em segundos)
"{{ uuid4() }}"          # identificador único
```

### 5.5 Blocos de controle

O Jinja completo funciona em campos de texto:

```yaml
- id: relatorio
  use: transform.template
  with:
    template: |
      Relatório de {{ today() }}
      {% for item in steps.dados.json %}
      • {{ item.nome }}: {{ item.valor | brl }}
      {%- endfor %}

      Total: {{ steps.dados.json | map(attribute='valor') | sum | brl }}
```

### 5.6 Variável inexistente falha

Referenciar algo que não existe levanta erro em vez de virar string vazia:

```yaml
"{{ vars.nao_existe }}"                     # erro: não existe no contexto
"{{ vars.nao_existe | default('padrão') }}" # "padrão"
```

Silêncio é pior que erro: um alerta dizendo "Preço atual: " é mais difícil de diagnosticar do que um passo vermelho no dashboard.

### 5.7 Sandbox

Os templates rodam em `SandboxedEnvironment`. Acesso a atributos internos é bloqueado:

```yaml
"{{ ''.__class__.__mro__ }}"    # erro de segurança, não execução arbitrária
```

---

## 6. Catálogo de actions

Consulte a qualquer momento pelo terminal:

```bash
fluxor actions              # lista completa
fluxor actions http.get     # parâmetros, tipos, padrões e descrição
```

### 6.1 `http.*` — requisições

```yaml
- id: buscar
  use: http.get
  with:
    url: https://api.exemplo.com/pedidos
    headers:
      Authorization: "Bearer {{ env.API_TOKEN }}"
    params:
      status: aberto
      limite: 50
    timeout: 30
    follow_redirects: true
    raise_for_status: true    # false devolve o erro como saída normal
```

**Saída:**

```yaml
{{ steps.buscar.status }}       # 200
{{ steps.buscar.ok }}           # true
{{ steps.buscar.json }}         # corpo parseado (null se não for JSON)
{{ steps.buscar.text }}         # corpo como texto
{{ steps.buscar.headers }}      # dicionário de cabeçalhos
{{ steps.buscar.url }}          # URL final, depois de redirecionamentos
{{ steps.buscar.elapsed_ms }}   # duração
```

`http.post` e `http.request` aceitam os mesmos parâmetros, mais `json` (corpo JSON) e `data` (formulário ou texto).

```yaml
- id: criar
  use: http.post
  with:
    url: https://api.exemplo.com/pedidos
    json:
      cliente: "{{ vars.cliente }}"
      itens: "{{ steps.carrinho.json }}"
```

> **Classificação de erro:** 4xx (exceto 408, 425 e 429) vira `PermanentError` e não é retentado. 5xx, 429 e falhas de conexão são retentáveis.

### 6.2 `parse.*` — extração

```yaml
# HTML por seletor CSS
- id: preco
  use: parse.css
  with:
    html: "{{ steps.pagina.text }}"
    selector: "div.produto p.preco"
    attr: null        # sem attr = texto; com attr = valor do atributo
    first: true       # true = um valor; false = lista
    limit: 10
    required: true    # falha se não casar nada

# JSON por caminho pontilhado
- id: total
  use: parse.json
  with:
    data: "{{ steps.resposta.json }}"
    path: "resultado.itens.0.valor"
    default: 0
    required: false

# Regex
- id: versao
  use: parse.regex
  with:
    text: "{{ steps.pagina.text }}"
    pattern: "versão (\\d+\\.\\d+\\.\\d+)"
    group: 1
    all: false
    ignore_case: true
```

### 6.3 `transform.*` — moldar dados

```yaml
# map — transforma cada item
- id: nomes
  use: transform.map
  with:
    items: "{{ steps.dados.json }}"
    expr: "{{ item.nome | upper }}"

# map produzindo dicionários
- id: linhas
  use: transform.map
  with:
    items: "{{ steps.dados.json }}"
    expr: '{{ {"data": today(), "nome": item.nome, "valor": item.preco} }}'

# filter
- id: caros
  use: transform.filter
  with:
    items: "{{ steps.produtos.json }}"
    condition: "item.preco > 100"

# sort
- id: ordenado
  use: transform.sort
  with:
    items: "{{ steps.produtos.json }}"
    key: "{{ item.preco }}"
    reverse: true

# unique — remove duplicados preservando a ordem
- id: distintos
  use: transform.unique
  with:
    items: "{{ steps.emails }}"

# merge — junta dicionários (o último vence)
- id: config
  use: transform.merge
  with:
    sources: ["{{ vars.padrao }}", "{{ steps.customizado.json }}"]

# template — monta texto livre
- id: mensagem
  use: transform.template
  with:
    template: |
      {{ steps.dados.json | length }} itens processados em {{ today() }}
```

### 6.4 `flow.*` — controle

```yaml
# set — guarda valores calculados para reusar
- id: calculado
  use: flow.set
  with:
    values:
      total: "{{ steps.itens.json | length }}"
      media: "{{ steps.soma.valor / steps.itens.json | length }}"
# uso: {{ steps.calculado.total }}

# assert — barreira de qualidade
- id: sanidade
  use: flow.assert
  with:
    that: "{{ steps.preco.valor > 0 }}"
    message: "preço inválido: {{ steps.preco_texto }}"

# sleep — respeita rate limit
- id: pausa
  use: flow.sleep
  with:
    seconds: 2

# fail — falha de propósito (útil para testar on_failure)
- id: forcar_erro
  use: flow.fail
  with:
    message: "condição inaceitável"
```

### 6.5 `file.*` — disco

```yaml
- id: ler
  use: file.read
  with:
    path: dados/entrada.json
    as_json: true
    missing_ok: false

- id: escrever
  use: file.write
  with:
    path: saida/relatorio.txt
    content: "{{ steps.relatorio }}"
    mode: w              # w sobrescreve, a acrescenta
    create_dirs: true

- id: historico
  use: file.csv_append
  with:
    path: data/serie.csv
    headers: [data, produto, preco]   # opcional
    rows:
      - data: "{{ today() }}"
        produto: "{{ vars.nome }}"
        preco: "{{ steps.preco.valor }}"
```

`file.csv_append` cria o cabeçalho na primeira gravação e nunca mais. É a forma mais simples de acumular série temporal: rode todo dia e em duas semanas você tem dados para plotar, sem banco nenhum.

### 6.6 `notify.*` — avisar

```yaml
# Log estruturado
- id: registrar
  use: notify.log
  with:
    message: "Processados {{ steps.total.valor }} itens"
    level: info                # debug | info | warning | error
    data:                      # campos extras no log estruturado
      workflow: "{{ run.workflow }}"

# Telegram
- id: avisar
  use: notify.telegram
  with:
    token: "{{ env.TELEGRAM_BOT_TOKEN }}"
    chat_id: "{{ env.TELEGRAM_CHAT_ID }}"
    text: "<b>Alerta</b>\nPreço: {{ steps.preco_texto }}"
    parse_mode: HTML           # HTML | Markdown | MarkdownV2 | none

# Discord
- id: canal
  use: notify.discord
  with:
    webhook_url: "{{ env.DISCORD_WEBHOOK }}"
    content: "Relatório pronto"

# Webhook genérico (Slack, n8n, seu backend)
- id: integrar
  use: notify.webhook
  with:
    url: "{{ env.SLACK_WEBHOOK }}"
    payload:
      text: "{{ steps.mensagem }}"

# E-mail
- id: email
  use: notify.email
  with:
    host: smtp.gmail.com
    port: 587
    username: "{{ env.SMTP_USER }}"
    password: "{{ env.SMTP_PASS }}"
    from: "automacao@empresa.com"
    to: ["time@empresa.com"]
    subject: "Relatório de {{ today() }}"
    body: "{{ steps.relatorio }}"
    html: false
```

**Configurando o Telegram em 3 passos:**

1. Fale com o [@BotFather](https://t.me/botfather) e envie `/newbot`. Ele devolve o token.
2. Mande qualquer mensagem para o seu bot.
3. Abra `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` e copie o `chat.id`.

Coloque os dois no `.env` e declare `env: [TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]` no workflow.

### 6.7 `shell.run` — comandos do sistema

```yaml
- id: backup
  use: shell.run
  with:
    command: ["pg_dump", "-h", "localhost", "-U", "app", "meu_banco"]
    cwd: /tmp
    timeout: 300
    check: true            # falha se o código de saída não for 0
    env:
      PGPASSWORD: "{{ env.DB_PASSWORD }}"
```

Saída: `{{ steps.backup.stdout }}`, `.stderr`, `.returncode`.

> **Segurança:** o padrão é lista de argumentos, executada **sem** shell. Existe `shell: true` para quando você precisa de pipe ou redirecionamento — nesse modo, nunca interpole entrada não confiável. Se o dashboard estiver acessível a outras pessoas, rode em container (o `Dockerfile` já usa usuário sem privilégio).

---

## 7. Linha de comando

```bash
fluxor --help
fluxor <comando> --help
```

| Comando | O que faz |
|---|---|
| `fluxor init [pasta]` | Cria uma pasta de workflows com exemplo comentado |
| `fluxor validate [arquivos...]` | Valida sem executar; sem argumento, valida a pasta configurada |
| `fluxor list` | Lista os workflows da pasta |
| `fluxor actions [nome]` | Catálogo de actions; com nome, detalha os parâmetros |
| `fluxor run <ref>` | Executa agora (aceita caminho do arquivo ou `name`) |
| `fluxor runs` | Histórico de execuções |
| `fluxor show <id>` | Detalhe de uma execução, passo a passo |
| `fluxor serve` | Sobe API + dashboard |
| `fluxor scheduler` | Roda só o agendador, em primeiro plano |
| `fluxor purge --days N` | Limpa histórico antigo do banco |

### Opções do `run`

```bash
fluxor run meu-workflow                          # pelo nome
fluxor run caminho/para/arquivo.yaml             # pelo caminho
fluxor run meu-workflow --var teto=10 --var ativo=true
fluxor run meu-workflow --dry-run                # sem efeito colateral
fluxor run meu-workflow --no-db                  # não grava no histórico
fluxor run meu-workflow --json                   # saída em JSON, para pipeline
```

**Exit codes:** `0` sucesso, `1` falha. É isso que permite encaixar o Fluxor num cron, num GitHub Action ou num `&&`:

```bash
fluxor run backup-diario && echo "backup ok" || notificar-plantao
```

### Filtros do `runs`

```bash
fluxor runs --limit 30
fluxor runs --workflow monitor-preco
fluxor runs --status failed
```

---

## 8. API REST e dashboard

```bash
fluxor serve                       # http://127.0.0.1:8000
fluxor serve --port 3000 --host 0.0.0.0
fluxor serve --scheduler           # com o agendador junto
fluxor serve --reload              # recarrega ao salvar (desenvolvimento)
```

- **Dashboard:** `http://localhost:8000`
- **Documentação interativa (Swagger):** `http://localhost:8000/docs`

### Endpoints

| Método | Rota | Para quê |
|---|---|---|
| `GET` | `/api/health` | Estado do serviço, workflows carregados, agendador |
| `GET` | `/api/actions` | Catálogo de actions com schema |
| `GET` | `/api/workflows` | Lista os workflows |
| `GET` | `/api/workflows/{nome}` | Detalha um workflow |
| `POST` | `/api/workflows/reload` | Relê os arquivos do disco |
| `POST` | `/api/workflows/{nome}/run` | Executa agora |
| `POST` | `/api/hooks/{nome}` | Gatilho externo (exige token) |
| `GET` | `/api/runs` | Histórico paginado |
| `GET` | `/api/runs/{id}` | Detalhe completo de uma execução |
| `GET` | `/api/stats` | Métricas agregadas do período |
| `GET` | `/api/scheduler/jobs` | Jobs agendados e próxima execução |

### Exemplos

```bash
# estado
curl http://localhost:8000/api/health

# executar com variáveis
curl -X POST http://localhost:8000/api/workflows/monitor-preco/run \
     -H "Content-Type: application/json" \
     -d '{"vars": {"teto": 40}, "dry_run": false}'

# histórico só de falhas
curl "http://localhost:8000/api/runs?status=failed&limit=10"

# métricas dos últimos 30 dias
curl "http://localhost:8000/api/stats?days=30"

# webhook
curl -X POST "http://localhost:8000/api/hooks/deploy-webhook?token=SEU_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"branch": "main", "autor": "neithan"}'
```

O corpo do POST no webhook chega ao workflow como `{{ vars.payload }}`.

> Uma execução que falha ainda devolve **HTTP 200**, com `"status": "failed"` no corpo. São coisas diferentes: o disparo funcionou; quem falhou foi o workflow. Erro 4xx/5xx fica reservado para problema na chamada em si.

### O dashboard

- **Cartões de métrica** — execuções no período, taxa de sucesso, duração média, workflows ativos.
- **Gráfico diário** — barras empilhadas de sucesso, parcial e falha nos últimos 14 dias.
- **Workflows** — cada um com botão **rodar**, que executa e já abre o resultado.
- **Execuções recentes** — clique em qualquer linha para abrir o painel lateral com todos os passos, saídas, tentativas e erros.
- **Recarregar YAML** — relê os arquivos do disco sem reiniciar o servidor.

Atualiza sozinho a cada 15 segundos, e pausa o polling quando a aba não está visível.

---

## 9. Agendamento

Marque o workflow:

```yaml
trigger:
  type: schedule
  cron: "0 9 * * 1-5"
  timezone: America/Sao_Paulo
```

E rode o agendador de uma das duas formas:

```bash
fluxor scheduler              # só o agendador, em primeiro plano
fluxor serve --scheduler      # junto com a API e o dashboard
```

Três comportamentos que evitam dor de cabeça:

- **Sem sobreposição** (`max_instances=1`) — se a execução das 9h ainda estiver rodando às 10h, a das 10h não começa por cima.
- **Sem enxurrada** (`coalesce=True`) — máquina desligada por 3 horas não dispara 3 execuções atrasadas de uma vez; dispara uma.
- **Tolerância a atraso** (`misfire_grace_time=300`) — atraso maior que 5 minutos faz a janela ser pulada em vez de executada fora de hora.

Veja o que está agendado e quando roda:

```bash
curl http://localhost:8000/api/scheduler/jobs
```

---

## 10. Configuração

Tudo por variável de ambiente, com prefixo `FLUXOR_`. Podem ficar num arquivo `.env` na raiz.

| Variável | Padrão | Para quê |
|---|---|---|
| `FLUXOR_WORKFLOWS_DIR` | `examples` | Pasta varrida em busca de `.yaml` |
| `FLUXOR_DATABASE_URL` | `sqlite+aiosqlite:///./fluxor.db` | Banco do histórico |
| `FLUXOR_HOST` | `127.0.0.1` | Endereço do servidor |
| `FLUXOR_PORT` | `8000` | Porta |
| `FLUXOR_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `FLUXOR_LOG_FORMAT` | `text` | `text` para humanos, `json` para produção |
| `FLUXOR_TIMEZONE` | `America/Sao_Paulo` | Fuso padrão do agendador |
| `FLUXOR_ENABLE_SCHEDULER` | `false` | Sobe o agendador junto com a API |
| `FLUXOR_HTTP_TIMEOUT` | `30` | Timeout padrão das actions HTTP |
| `FLUXOR_FOREACH_CONCURRENCY` | `5` | Itens em paralelo num `foreach` |
| `FLUXOR_MAX_OUTPUT_BYTES` | `64000` | Corte da saída ao gravar no banco |

### Trocando para PostgreSQL

```bash
pip install asyncpg
export FLUXOR_DATABASE_URL="postgresql+asyncpg://usuario:senha@localhost/fluxor"
```

Nenhuma outra mudança é necessária. O schema é criado na primeira execução.

---

## 11. Receitas práticas

### Monitorar uma API e alertar quando cair

```yaml
name: uptime-api
trigger: { type: schedule, cron: "*/5 * * * *" }
env: [TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]

vars:
  alvo: https://minha-api.com/health

steps:
  - id: checar
    use: http.get
    with:
      url: "{{ vars.alvo }}"
      timeout: 10
      raise_for_status: false     # queremos o status, não uma exceção

  - id: alertar
    use: notify.telegram
    when: "not steps.checar.ok"
    with:
      token: "{{ env.TELEGRAM_BOT_TOKEN }}"
      chat_id: "{{ env.TELEGRAM_CHAT_ID }}"
      text: "🔴 API fora do ar — HTTP {{ steps.checar.status }}"

  - id: registrar
    use: file.csv_append
    with:
      path: data/uptime.csv
      rows:
        - quando: "{{ now().isoformat() }}"
          status: "{{ steps.checar.status }}"
          ms: "{{ steps.checar.elapsed_ms }}"
```

### Backup diário com verificação

```yaml
name: backup-diario
trigger: { type: schedule, cron: "0 3 * * *" }
env: [DB_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]

steps:
  - id: dump
    use: shell.run
    with:
      command: ["pg_dump", "-h", "localhost", "-U", "app", "-f", "/backups/dump.sql", "app"]
      env: { PGPASSWORD: "{{ env.DB_PASSWORD }}" }
      timeout: 1800

  - id: tamanho
    use: shell.run
    with:
      command: ["stat", "-c", "%s", "/backups/dump.sql"]

  - id: verificar
    use: flow.assert
    with:
      that: "{{ steps.tamanho.stdout | to_int > 1000 }}"
      message: "backup suspeito: só {{ steps.tamanho.stdout }} bytes"

  - id: confirmar
    use: notify.telegram
    with:
      token: "{{ env.TELEGRAM_BOT_TOKEN }}"
      chat_id: "{{ env.TELEGRAM_CHAT_ID }}"
      text: "✅ Backup de {{ today() }}: {{ steps.tamanho.stdout }} bytes"

on_failure:
  - id: alarme
    use: notify.telegram
    with:
      token: "{{ env.TELEGRAM_BOT_TOKEN }}"
      chat_id: "{{ env.TELEGRAM_CHAT_ID }}"
      text: "🚨 BACKUP FALHOU: {{ error }}"
```

### Coletar de várias fontes e consolidar

```yaml
name: consolidar-vendas
trigger: { type: schedule, cron: "0 7 * * *" }
env: [API_TOKEN]

vars:
  lojas: [norte, sul, leste, oeste]

steps:
  - id: coletar
    use: http.get
    foreach: "{{ vars.lojas }}"
    with:
      url: "https://api.empresa.com/vendas/{{ item }}?data={{ today() }}"
      headers: { Authorization: "Bearer {{ env.API_TOKEN }}" }
    retry: { attempts: 3, backoff: exponential }

  - id: linhas
    use: transform.map
    with:
      items: "{{ vars.lojas }}"
      expr: '{{ {"data": today(), "loja": item, "total": steps.coletar[index].json.total} }}'

  - id: ranking
    use: transform.sort
    with:
      items: "{{ steps.linhas }}"
      key: "{{ item.total }}"
      reverse: true

  - id: planilha
    use: file.csv_append
    with:
      path: data/vendas.csv
      rows: "{{ steps.ranking }}"

  - id: relatorio
    use: transform.template
    with:
      template: |
        Vendas de {{ today() }}
        {% for loja in steps.ranking %}
        {{ loop.index }}. {{ loja.loja }}: {{ loja.total | brl }}
        {%- endfor %}
        Total: {{ steps.ranking | map(attribute='total') | sum | brl }}

  - id: enviar
    use: notify.log
    with: { message: "{{ steps.relatorio }}" }
```

### Disparar por evento externo

```yaml
name: on-deploy
trigger:
  type: webhook
  token: gere-um-token-longo-aqui

steps:
  - id: qual_branch
    use: flow.set
    with:
      values:
        branch: "{{ vars.payload.ref | default('desconhecida') }}"

  - id: so_producao
    use: notify.log
    when: "steps.qual_branch.branch == 'refs/heads/main'"
    with:
      message: "Deploy em produção detectado"
```

Aponte o webhook do GitHub para `https://seu-host/api/hooks/on-deploy?token=SEU_TOKEN`.

---

## 12. Deploy em produção

### Docker (mais simples)

```bash
docker compose up -d
docker compose logs -f
```

Ajuste no `docker-compose.yml`: monte a sua pasta de workflows em `/app/workflows` e mantenha o volume `/data` para o banco e os CSVs sobreviverem a recriações do container.

### VPS com systemd

```ini
# /etc/systemd/system/fluxor.service
[Unit]
Description=Fluxor — motor de automações
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=fluxor
WorkingDirectory=/opt/fluxor
EnvironmentFile=/opt/fluxor/.env
Environment="FLUXOR_LOG_FORMAT=json"
Environment="FLUXOR_ENABLE_SCHEDULER=true"
ExecStart=/opt/fluxor/.venv/bin/fluxor serve --host 0.0.0.0
Restart=always
RestartSec=10

# Endurecimento — o serviço não precisa de nada disso
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/fluxor/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fluxor
sudo journalctl -u fluxor -f
```

### Nginx na frente

```nginx
server {
    listen 443 ssl http2;
    server_name automacoes.seudominio.com;

    ssl_certificate     /etc/letsencrypt/live/automacoes.seudominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/automacoes.seudominio.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;   # workflows longos
    }
}
```

### Checklist de produção

- [ ] `FLUXOR_LOG_FORMAT=json` — para os logs caírem legíveis no Loki/Datadog/CloudWatch.
- [ ] Segredos por variável de ambiente ou gerenciador de segredos, **nunca** no YAML.
- [ ] O dashboard **não tem autenticação**. Deixe atrás de VPN, Basic Auth do Nginx ou um proxy autenticado.
- [ ] Tokens de webhook longos e aleatórios (`openssl rand -hex 32`).
- [ ] `fluxor purge --days 90` no cron, ou o banco cresce para sempre.
- [ ] Backup do arquivo `.db` (ou do Postgres).
- [ ] Monitore o `/api/health` com o seu monitor externo.

---

## 13. Problemas comuns

**`action 'x.y' não encontrada`**
Nome errado ou o plugin não está instalado. Rode `fluxor actions` para ver a lista exata.

**`'{{ steps.x }}' não existe no contexto`**
Três causas possíveis: (a) erro de digitação no `id`; (b) o passo `x` vem *depois* deste no arquivo; (c) o passo `x` foi pulado por `when` — e passo pulado não publica saída. Use `| default(...)` se a ausência for esperada.

**`parâmetros inválidos para 'x.y' -> campo: extra inputs are not permitted`**
Chave a mais no `with:`. Confira os nomes com `fluxor actions x.y`.

**O `when` não funciona como eu esperava**
Provavelmente você está comparando texto. Se o valor vem de uma API como string, converta: `"{{ steps.x.valor | to_number }} > 10"`.

**O workflow agendado não roda**
O agendador precisa estar de pé: `fluxor scheduler` ou `fluxor serve --scheduler`. Confirme o registro em `GET /api/scheduler/jobs` e confira o fuso — `cron: "0 9 * * *"` com `FLUXOR_TIMEZONE=UTC` dispara às 6h no horário de Brasília.

**`database is locked` (SQLite)**
O modo WAL já vem ligado, o que resolve a maioria dos casos. Se persistir com muita concorrência, migre para PostgreSQL — é só trocar a `FLUXOR_DATABASE_URL`.

**`parse.css` não acha nada**
O site provavelmente monta o conteúdo com JavaScript, e o `http.get` só enxerga o HTML inicial. Verifique com `curl` o que realmente chega. Nesses casos, procure a API que a própria página consome — quase sempre existe, e é mais estável que o HTML.

**Vejo o aviso `env_ausente`**
O workflow declarou uma variável em `env:` que não existe no ambiente. Preencha o `.env` ou remova a declaração.

**Como depurar um workflow passo a passo**

```bash
fluxor run meu-workflow --dry-run     # confere o que cada passo receberia
FLUXOR_LOG_LEVEL=DEBUG fluxor run meu-workflow
fluxor show <run-id>                  # todas as saídas, depois da execução
```

---

## 14. Estendendo o Fluxor

### Uma action nova

Crie um arquivo em `src/fluxor/actions/` — a descoberta é automática:

```python
from typing import ClassVar

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.exceptions import PermanentError
from fluxor.registry import register


class PlanilhaInput(ActionInput):
    planilha_id: str = Field(description="ID da planilha.")
    aba: str = Field(default="Página1", description="Nome da aba.")
    linhas: list[list[str]] = Field(description="Linhas a acrescentar.")


@register("sheets.append")
class SheetsAppend(Action):
    """Acrescenta linhas a uma planilha do Google Sheets."""

    summary = "Acrescenta linhas a uma planilha"
    Input: ClassVar[type[ActionInput]] = PlanilhaInput

    async def run(self, params: PlanilhaInput, ctx: RunContext) -> dict[str, int]:
        if not params.linhas:
            raise PermanentError("nenhuma linha para acrescentar")
        ...
        return {"linhas_gravadas": len(params.linhas)}
```

Ela aparece em `fluxor actions` na hora, com os parâmetros documentados.

### Usar o motor como biblioteca

```python
import asyncio

from fluxor import Engine, load_workflow

async def main() -> None:
    workflow = load_workflow("examples/hello-mundo.yaml")
    record = await Engine().execute(workflow, extra_vars={"nome": "Python"})

    print(record.status, record.duration_ms)
    for result in record.results:
        print(result.step_id, result.status, result.output)

asyncio.run(main())
```

Com histórico em banco:

```python
from fluxor import Engine
from fluxor.storage import Database, RunRepository

database = Database("sqlite+aiosqlite:///./meu.db")
await database.create_all()
engine = Engine(sink=RunRepository(database))
```

### Distribuir actions como pacote

```toml
[project.entry-points."fluxor.actions"]
minhas-actions = "meu_pacote.actions"
```

Quem instalar o seu pacote passa a ter as actions disponíveis, sem fork e sem configuração.

---

Dúvida que este guia não cobriu? [Abra uma issue](https://github.com/NeithanDev-Arch/fluxor/issues).
