# Plano — API de dados (api-tcc)

Status: **APROVADO em 25/10/2026, em implementação.** Parada obrigatória depois de `sql/esquema.sql` + guarda
de SQL; o resto (publicador, API, testes) só depois do "segue".

## Decisões aprovadas (25/10/2026)

- **Arquitetura:** o publicador escreve no `tcc_processador` por **conexão direta ao MySQL**, logo depois do
  processamento, sem passar pela API. A API serve só para a interface consultar: **só GET**, nenhuma rota de
  escrita (nem POST, PUT ou DELETE). O processamento-tcc não ganha nenhum arquivo: o publicador só **lê**
  `saida/` e `saida_completa/`.
- **Guia da interface:** é um documento online, fora do repositório. Fonte oficial = **seção 8 da
  ESPECIFICACAO.md** (contrato 1.0).
- **Arquivo de exemplo:** `env.exemplo` (sem o ponto, por causa do deny `.env.*`).
- **`?data=` = data da pasta** ("como o site estava naquela manhã"). **Toda resposta traz também
  `data_referencia`** (o pregão) e `data_publicacao` no envelope, e o `/docs` explica a diferença.
- **Os 6 pontos do diagnóstico:** aprovados. Ajuste: a trava "pacote vazio ou mais de 20% menos registros" vale
  **só para os versionados** (acoes, fundamentos_trimestrais, proventos), não para as fotos do dia, e
  `--forcar` libera quando a queda for real.
- Também existe `api-tcc/.claude/settings.json`, com o mesmo deny de `.env`, para quando o Cursor for aberto
  direto no api-tcc.

---

## 0. O que já foi feito (Passo 0 e diagnóstico)

- `Arquivos/.claude/settings.json` criado com deny para `.env` e `.env.*` (ler, editar, escrever, listar,
  buscar e citar em comando de terminal).
- O banco `tcc_processador` está **vazio** (MySQL 8.0.46). Conferido com `ferramentas/listar_tabelas.py`:
  sessão READ ONLY, só lista tabelas, não imprime credenciais.
- Criei `api-tcc/.venv` com `pymysql` e `python-dotenv`, só para rodar esse diagnóstico. Na implementação ele
  recebe o resto das dependências.

### Descobertas que mudam o desenho

1. **O "Guia" é um documento online, fora do repositório.** Fonte oficial: **seção 8 da ESPECIFICACAO.md
   (contrato 1.0)** e os próprios pacotes.
2. **A data da pasta não é a data do pregão.** `saida_completa/2026-10-23/` é a rotina da *manhã de 23/10*, e
   os registros diários dela têm `data_referencia = 2026-10-22`, o último pregão. O plano chama de:
   - **D** = data da publicação (nome da pasta = `execucao.data_referencia` = manhã da rotina);
   - **data_referencia** = data que vem dentro do registro (pregão).
   Na API, `?data=AAAA-MM-DD` é sempre **D** ("como estava na manhã daquele dia").
3. **O contrato não traz hash.** O publicador calcula o seu: SHA-256 do registro em JSON canônico, **sem**
   `versao_contrato`, `gerado_em` e `dados_ate`. Esses três campos mudam a cada execução e, se entrassem na
   conta, criariam uma "nova versão" falsa de toda ação, todo dia.
4. **Na carga completa, `removidos` vem sempre vazio** (spec 8.7). Por isso "removido" = chave que tem versão
   aberta no banco mas não aparece na completa de D. Também uso a lista `removidos` quando ela vier preenchida.
5. **Uma "folha" do contrato pode ter campos a mais** além de valor e status, por exemplo
   `{"valor":5.95,"status":"ok","texto":null,"setor":{...}}`. A validação vale para todo objeto que tenha
   `valor` **e** `status`.
6. **Tamanho da completa de 23/10:** 6,0 MB no total. series_diarias 3,1 MB (177 séries, 44.671 pontos),
   noticias_dia 1,3 MB, fundamentos 1,0 MB (664), resumo_acao 0,55 MB (85), proventos 0,35 MB (1.268),
   acoes 28 KB (85), mercado_dia 4 KB, execucao 3 KB.
7. **Top 5:** os pacotes `experimento_top` e `experimento_historico` não são gravados hoje (spec 8.5,
   `publicar_top_provisorio = false`). As tabelas existem, mas ficam vazias até o pacote aparecer.

---

## a) Fluxo e estrutura

```
 processamento-tcc (só leitura)                 api-tcc
 ┌───────────────────────────────┐   lê   ┌──────────────┐  grava (1 transação por D)  ┌──────────────────┐
 │ saida_completa/D/*.json       │ ─────► │  publicador  │ ──────────────────────────► │  MySQL           │
 │ saida/D/execucao.json         │        │  (seu PC)    │                             │  tcc_processador │
 └───────────────────────────────┘        └──────────────┘                             └────────┬─────────┘
                                                                                                │ só SELECT
                                                                                                │ (sessão READ ONLY)
                                         interface ◄── JSON igual ao dos pacotes ◄── ┌──────────┴──────┐
                                                                                     │  api (FastAPI)  │
                                                                                     └─────────────────┘
```

O publicador e a API **não importam um ao outro**. Os dois usam só o pacote pequeno `comum/`: configuração,
conexão, guarda de SQL, nomes de tabela e regras do contrato. Para subir na nuvem, basta levar `api/` + `comum/`.

```
api-tcc/
├── CLAUDE.md                     regras deste projeto (todas as deste pedido)
├── .claude/settings.json         deny .env / .env.*
├── .gitignore                    .env, .env.*, __pycache__/, .venv/, .pytest_cache/
├── env.exemplo                   só os NOMES das variáveis (ver nota abaixo)
├── .env                          já existe (seu), o código lê e eu não
├── requirements-publicador.txt   pymysql, python-dotenv
├── requirements-api.txt          fastapi, uvicorn, pymysql, python-dotenv
├── requirements-dev.txt          -r dos dois acima + pytest, httpx
├── pytest.ini
├── sql/esquema.sql               fonte única das tabelas ({p} = prefixo das tabelas)
├── comum/
│   ├── config.py                 lê o .env, recusa NOME_BANCO ≠ tcc_processador, usuários com fallback
│   ├── guarda_sql.py             lista de comandos permitidos e proibição de outros schemas
│   ├── banco.py                  conexao_escrita() / conexao_leitura() (READ ONLY); todo execute passa pela guarda
│   ├── tabelas.py                nomes das tabelas = prefixo + nome-base
│   ├── contrato.py               pacotes, chaves, status válidos, hash canônico
│   └── series.py                 formato compacto ⇄ pontos (ida e volta)
├── publicador/
│   ├── __main__.py               CLI: --criar-tabelas | --data D | --conferir
│   ├── leitor.py                 lê os arquivos de D (caminho vindo da config)
│   ├── validador.py              valida o contrato 1.0 antes de qualquer gravação
│   ├── planejador.py             lógica PURA: estado atual + pacotes → lista de ações (testável sem banco)
│   ├── gravador.py               aplica as ações numa transação única
│   └── resumo.py                 "23/10/2026 publicado: 85 resumos, 44.671 pontos novos, ..."
├── api/
│   ├── main.py                   app FastAPI, prefixo /v1, CORS, /docs
│   ├── seguranca.py              X-API-Key
│   ├── repositorio.py            os SELECTs
│   └── rotas.py                  endpoints, só GET
├── ferramentas/
│   └── gerar_dados_teste.py      copia um pedaço pequeno dos pacotes reais para tests/dados/
├── tests/
│   ├── unit/                     sem banco (a maioria)
│   ├── integracao/               banco real, só tabelas teste_*, pulados sem PROC_API_TESTES_COM_BANCO=1
│   └── dados/                    2026-10-23/ (recorte real) e 2026-10-26/ (derivado, ver e)
└── docs/plano_api.md             este arquivo
```

**Nota sobre `env.exemplo`:** o deny `**/.env.*` também pegaria `.env.exemplo` (no Claude Code o deny sempre
vence o allow). Por isso o nome é `env.exemplo`, sem o ponto inicial (aprovado).

`ferramentas/listar_tabelas.py` usa `information_schema`, que a guarda vai proibir. Ele **sai** na implementação.
No lugar dele entra `python -m publicador --conferir`, que mostra a contagem de cada tabela via `SELECT COUNT(*)`
e passa pela guarda.

### Variáveis (nomes; valores ficam no .env)

| Variável | Uso |
|---|---|
| `IP_BANCO`, `PORTA_BANCO`, `NOME_BANCO` | conexão; NOME_BANCO tem que ser exatamente `tcc_processador` |
| `USER_BANCO`, `SENHA_BANCO` | fallback dos dois perfis |
| `USER_ESCRITA`, `SENHA_ESCRITA` | publicador (se não existir → USER_BANCO) |
| `USER_LEITURA`, `SENHA_LEITURA` | API (se não existir → USER_BANCO) |
| `PROC_DIR_PROCESSAMENTO` | caminho do processamento-tcc (padrão: `../processamento-tcc`) |
| `PROC_API_CHAVES` | chaves aceitas no X-API-Key, separadas por vírgula |
| `PROC_API_CORS_ORIGENS` | origens permitidas, separadas por vírgula (padrão: nenhuma) |
| `PROC_API_PREFIXO_TABELAS` | vazio = tabelas reais; os testes forçam `teste_` |
| `PROC_API_TESTES_COM_BANCO` | `1` liga os testes de integração |

### Guarda de banco (comum/, com teste para cada regra)

1. `config.py` recusa iniciar se `NOME_BANCO != "tcc_processador"`. Toda conexão usa `database=NOME_BANCO` e,
   logo depois de conectar, confere `SELECT DATABASE()`.
2. `guarda_sql.verificar(sql)` roda antes de **todo** `execute`. O SQL passa se:
   - for um único comando (`;` no meio = recusa) e não tiver comentário executável do MySQL (`/*! ... */`);
   - depois de tirar strings e comentários, começar com `SELECT`, `INSERT`, `UPDATE`,
     `CREATE TABLE IF NOT EXISTS` ou `CREATE INDEX`;
   - não citar `tcc_scraper` em lugar nenhum (nem dentro de string), nem `information_schema`, `mysql`,
     `performance_schema` ou `sys`;
   - não tiver nome qualificado `x.y` com `x` diferente de `tcc_processador`. Para isso, o código do projeto
     não usa `apelido.coluna`;
   - não tiver `DROP`, `ALTER`, `TRUNCATE`, `DELETE`, `GRANT`, `REVOKE`, `USE`, `RENAME`, `LOAD`, `CALL`,
     `INTO OUTFILE` ou `SET`.
   Exceções fixas, fora da guarda: `SET SESSION TRANSACTION READ ONLY`, enviado só pela conexão de leitura, e
   início, commit e rollback de transação, feitos pelos métodos do driver.
3. **Modo teste** (prefixo `teste_`): a guarda também recusa qualquer nome de tabela, depois de
   FROM/JOIN/INTO/UPDATE/TABLE/ON, que não comece com `teste_`. A limpeza dos testes tem função própria:
   `DROP TABLE IF EXISTS` só para nomes `^teste_[a-z_]+$`, e só com o prefixo de teste ativo. Fora do modo
   teste, essa função se recusa a rodar.
4. API: a conexão de leitura manda `SET SESSION TRANSACTION READ ONLY` antes de tudo. Uma escrita, mesmo que
   passasse pela guarda, seria recusada pelo próprio MySQL (erro 1792).
5. Nas tabelas reais eu não executo nada. Você roda `--criar-tabelas` e `--data`.

---

## b) Esquema SQL (MySQL 8, InnoDB, utf8mb4)

Três famílias de tabela:

- **Fotos do dia** (uma linha por chave + data_referencia, só acumulam): `resumo_acao`, `noticias_dia`,
  `mercado_dia`, `execucao`, `experimento_top`, `experimento_historico`.
- **Pontos de série** (um por série + data, só acumulam): `series_pontos` (+ `series_catalogo`).
- **Versionadas** (valido_desde / valido_ate): `acoes`, `fundamentos_trimestrais`, `proventos`.

E mais duas de apoio: `publicacoes` (uma linha por D) e `correcoes` (versões anteriores de fotos e pontos que
foram corrigidos).

```sql
-- {p} é trocado pelo prefixo ('' nas reais, 'teste_' nos testes)

CREATE TABLE IF NOT EXISTS {p}publicacoes (
  data_publicacao  DATE         NOT NULL PRIMARY KEY,           -- D
  status           VARCHAR(16)  NOT NULL,                       -- completa | so_execucao | rejeitada
  data_referencia  DATE         NULL,                           -- pregão dos dados desta publicação
  pasta_completa   VARCHAR(255) NULL,
  pasta_saida      VARCHAR(255) NULL,
  hashes_arquivos  JSON         NOT NULL,                       -- {"resumo_acao.json":"sha256...", ...}
  contagens_novas  JSON         NOT NULL,                       -- {"resumo_acao":85,"series_pontos":44671,...}
  execucao_status  VARCHAR(16)  NULL,                           -- status da rotina daquela manhã
  erros            JSON         NULL,                           -- motivo, se rejeitada
  inicio           DATETIME(3)  NOT NULL,
  fim              DATETIME(3)  NULL,
  vezes_publicada  INT          NOT NULL DEFAULT 1
);

-- FOTOS (mesmo molde; resumo_acao como exemplo)
CREATE TABLE IF NOT EXISTS {p}resumo_acao (
  ticker           VARCHAR(32) NOT NULL,
  data_referencia  DATE        NOT NULL,
  data_publicacao  DATE        NOT NULL,                        -- D que gravou esta linha
  hash             CHAR(64)    NOT NULL,
  registro         JSON        NOT NULL,                        -- o registro do pacote, inteiro
  PRIMARY KEY (ticker, data_referencia),
  KEY ix_resumo_pub (data_publicacao, ticker)
);
-- noticias_dia: idêntica (ticker, data_referencia)
-- mercado_dia, experimento_top, experimento_historico: PK (data_referencia), KEY (data_publicacao)
-- execucao: PK (data_referencia, rodada INT), coluna extra status VARCHAR(16), KEY (data_publicacao)

-- SÉRIES
CREATE TABLE IF NOT EXISTS {p}series_catalogo (
  ticker           VARCHAR(32)  NOT NULL PRIMARY KEY,           -- PETR4, CDI, IBOV, SETOR_SEM:PETR4
  tipo             VARCHAR(24)  NOT NULL,                       -- acao | etf | indice | taxa | setor_sem_empresa
  nome             VARCHAR(255) NOT NULL,
  campos           JSON         NOT NULL,                       -- ["fechamento","indice_retorno_total",...] na ordem do pacote
  primeira_publicacao DATE      NOT NULL,
  ultima_publicacao   DATE      NOT NULL
);
CREATE TABLE IF NOT EXISTS {p}series_pontos (
  ticker           VARCHAR(32) NOT NULL,
  data             DATE        NOT NULL,
  valores          JSON        NOT NULL,                        -- {"fechamento":57.83,"pl":5.95,...}
  status           JSON        NOT NULL,                        -- só os não-ok: {"pl":"indisponivel"}; {} se tudo ok
  hash             CHAR(64)    NOT NULL,
  data_publicacao  DATE        NOT NULL,                        -- D em que entrou
  PRIMARY KEY (ticker, data)
);

-- VERSIONADAS (acoes como exemplo)
CREATE TABLE IF NOT EXISTS {p}acoes (
  id               BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
  ticker           VARCHAR(32) NOT NULL,
  valido_desde     DATE        NOT NULL,                        -- D em que esta versão passou a valer
  valido_ate       DATE        NULL,                            -- D em que deixou de valer (NULL = atual)
  hash             CHAR(64)    NOT NULL,
  registro         JSON        NOT NULL,
  UNIQUE KEY uq_acoes (ticker, valido_desde),
  KEY ix_acoes_atual (ticker, valido_ate)
);
-- fundamentos_trimestrais: chave (ticker, data_fim_periodo DATE), UNIQUE (ticker, data_fim_periodo, valido_desde)
-- proventos: chave (ticker, tipo VARCHAR(16), data_com DATE), UNIQUE (ticker, tipo, data_com, valido_desde)

CREATE TABLE IF NOT EXISTS {p}correcoes (
  id               BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tabela           VARCHAR(48) NOT NULL,                        -- 'series_pontos', 'resumo_acao', ...
  chave            JSON        NOT NULL,                        -- {"ticker":"PETR4","data":"2026-03-10"}
  data_troca       DATE        NOT NULL,                        -- D que trouxe a versão nova
  hash_anterior    CHAR(64)    NOT NULL,
  conteudo_anterior JSON       NOT NULL,
  hash_novo        CHAR(64)    NOT NULL,
  KEY ix_corr (tabela, data_troca)
);
```

Por que guardar o registro inteiro numa coluna JSON? A API devolve exatamente o que veio no arquivo, e uma
mudança de campo no contrato não exige mudar a tabela. As colunas soltas (`ticker`, datas, `hash`) existem só
para busca e índice.

Ressalva: o MySQL guarda o JSON num formato próprio e **devolve as chaves em outra ordem** (por tamanho do nome).
O conteúdo é idêntico: mesmos campos, mesmos valores, null continua null. Os testes comparam campo a campo. Ao
olhar no /docs, a ordem dos campos vai ser diferente da do arquivo.

### Tamanho estimado após 1 ano (~250 manhãs)

| Tabela | Linhas em 1 ano | Tamanho aprox. |
|---|---|---|
| noticias_dia | 85 × 250 = 21.250 | ~350 MB (≈ 15 KB por linha) |
| resumo_acao | 21.250 | ~150 MB (≈ 6,5 KB por linha) |
| series_pontos | 44.671 + 177 × 250 ≈ 89.000 | ~30 MB |
| fundamentos_trimestrais | 664 + ~400 versões novas | ~2–3 MB |
| proventos | 1.268 + ~300 | ~1 MB |
| mercado_dia, execucao, acoes, publicacoes, catalogo | centenas | < 3 MB |
| **Total** | | **≈ 0,5–0,7 GB** com índices |

Quase todo o espaço vai para `noticias_dia`. Se pesar no futuro, dá para guardar os itens das notícias só uma
vez por id (fica em "Para depois").

---

## c) Idempotência e "preencher dia que falta"

Seja **U** a última D com status `completa` em `publicacoes`. Ao rodar `--data D`:

| Caso | O que acontece |
|---|---|
| Não existe `saida/D/execucao.json` | erro "nada para publicar"; o banco não é tocado |
| Existe execucao, mas não existe `saida_completa/D/` (rotina falhou ou não gerou a completa) | grava **só** a execução (+ publicacoes com `so_execucao`); o resto fica como estava |
| Validação do contrato falhou | nada é gravado nos dados; `publicacoes` registra `rejeitada` + erros |
| D > U | publicação normal |
| D = U | republicação: compara hash por hash; o que é igual não muda |
| D < U e D **não** tem `completa` | **preenche o buraco** (regras restritas, abaixo) |
| D < U e D já tem `completa` | republicação idempotente; se o conteúdo vier diferente, recusa e pede `--forcar` |

Regras por família (publicação normal, D ≥ U):

- **Fotos:** chave nova → INSERT. Mesma chave, mesmo hash → nada. Mesma chave, hash diferente → a versão antiga
  vai para `correcoes` e a linha recebe a nova (UPDATE).
- **Pontos de série:** a completa traz ~253 pregões. Ponto novo → INSERT. Ponto igual → nada. Ponto antigo com
  valor diferente (correção da coleta) → anterior em `correcoes` (com `data_troca = D`) e nova versão no lugar.
  Pontos que saíram da janela de 253 **não são apagados**: é assim que o histórico passa de 1 ano.
- **Versionadas:** chave sem versão aberta → nova versão `[D, NULL)`. Versão aberta com o mesmo hash → nada. Hash
  diferente → fecha a atual (`valido_ate = D`) e abre outra `[D, NULL)`. Se a versão aberta foi criada nesta
  mesma D (republicação), ela é corrigida no lugar e a anterior vai para `correcoes`. Chave aberta que não veio na
  completa (ou veio em `removidos`) → fecha (`valido_ate = D`).
- **Trava de segurança (só versionados: acoes, fundamentos_trimestrais, proventos):** se um deles vier vazio ou
  com mais de 20% menos registros que as versões abertas, aborta antes de gravar, para um bug do processamento
  não "fechar" o universo inteiro. `--forcar` libera quando a queda for real. As fotos do dia não têm essa trava.
- **Validação cruzada:** o número de registros de cada pacote tem que bater com `pacotes_enviados` do
  `execucao.json` da completa.

Preencher buraco (D < U), sem mexer nos dias posteriores:

- fotos de D: INSERT só das chaves que não existem (são daquele dia, ninguém mais as tem);
- pontos de série: INSERT só dos pontos que faltam; ponto existente diferente **não** é alterado, porque a
  versão gravada depois é mais nova. Só entra no resumo como aviso;
- versionadas: nada muda. Para D, a resposta continua sendo a versão já gravada que vale em D. Exceção: se D
  for anterior à primeira versão de uma chave, insere uma versão fechada `[D, primeira.valido_desde)` quando o
  conteúdo difere, ou nada quando é igual;
- execução de D: grava ou atualiza (pela chave data_referencia + rodada).

**Exemplo.** Segunda 26/10 publicada. Terça 27/10 a coleta atrasou e a rotina falhou: só execução
(`so_execucao`). Quarta 28/10 publicada normal (U = 28/10). Na quinta você roda de novo a rotina de 27/10 e
`--data 2026-10-27`:
- D < U, mas 27/10 não tem `completa` → modo buraco;
- entram as 85 linhas de resumo_acao e de noticias_dia e o mercado_dia com `data_referencia = 26/10`;
- os pontos de série até 26/10 já vieram na completa de 28/10 → "0 pontos novos";
- acoes, fundamentos e proventos: intactos (a versão que vale em 27/10 já está gravada);
- `publicacoes[27/10]` passa de `so_execucao` para `completa`. Rodar o mesmo comando de novo → tudo "0 novos".

Tudo isso, menos a linha `rejeitada`, acontece numa **única transação** (`BEGIN … COMMIT`). Qualquer erro →
`ROLLBACK` e o banco fica como antes. As tabelas são criadas só por `--criar-tabelas`, porque no MySQL
`CREATE TABLE` encerra a transação aberta e não pode ficar no meio de uma publicação.

O resumo impresso no fim fica assim:

```
23/10/2026 publicado (execução: ok, rodada 1) em 4,2 s
  resumo_acao ............ 85 novos
  noticias_dia ........... 85 novos
  mercado_dia ............ 1 novo
  series_diarias ......... 44.671 pontos novos (177 séries), 0 correções
  acoes .................. 85 versões novas, 0 fechadas
  fundamentos_trimestrais  664 versões novas, 0 fechadas
  proventos .............. 1.268 versões novas, 0 fechadas
  top 5 .................. não publicado (pacote ausente)
```

---

## d) Endpoints

Todos são GET, com prefixo `/v1` e cabeçalho `X-API-Key`, exceto `/v1/saude` e `/docs`.
Ticker em maiúsculas (a API converte `petr4` → `PETR4`).

| Endpoint | Retorna | Parâmetros |
|---|---|---|
| `/v1/saude` | `{"status":"ok","banco":"ok","ultima_publicacao":"2026-10-23"}` (sem chave) | — |
| `/v1/acoes` | envelope `acoes` com as 85 ações | `?data=D` |
| `/v1/resumo_acao/{ticker}` | envelope `resumo_acao` com 1 registro | `?data=D` |
| `/v1/resumo_acao/{ticker}/historico` | evolução de 1 campo, dia a dia | `?campo=pl` (aceita `cotacao.fechamento`, `rentabilidade.a1`…), `?inicio=`, `?fim=` |
| `/v1/series_diarias/{ticker}` | envelope com 1 série compacta | `?janela=1m\|3m\|6m\|1a\|2a\|max` (padrão `1a`), `?data=D` |
| `/v1/fundamentos_trimestrais/{ticker}` | os trimestres vigentes | `?data=D` |
| `/v1/proventos/{ticker}` | os proventos vigentes | `?data=D` |
| `/v1/noticias_dia/{ticker}` | notícias do dia | `?data=D` |
| `/v1/mercado_dia` | o registro do dia | `?data=D` |
| `/v1/execucao/ultima` | a última execução gravada (inclusive de manhã que falhou) | — |
| `/v1/execucao/{data}` | as execuções daquela D | — |
| `/v1/experimento_top` | top 5 do dia; **404 "pacote ainda não publicado"** enquanto estiver vazio | `?data=D` |

Regras:
- **Sem `?data`:** o mais recente. **Com `?data=D`:** fotos = a de maior `data_publicacao ≤ D`;
  versionadas = `valido_desde ≤ D < valido_ate` (ou `valido_ate` nulo); séries = pontos até a `data_referencia`
  daquela publicação, já com as correções aplicadas.
- **Janelas em pregões**, como no contrato: 1m = 21, 3m = 63, 6m = 126, 1a = 253, 2a = 506, max = tudo. Com isso,
  `janela=1a` devolve **a mesma série do arquivo**, com o mesmo `inicio_janela`.
- **Resposta = mesmo envelope do pacote** (`pacote, versao_contrato, chave, gerado_em, dados_ate, registros,
  removidos`), com `registros` filtrado pelo ticker e `removidos: []`, **mais dois campos no topo**:
  `data_publicacao` (a pasta/manhã cujos dados estão sendo mostrados; com `?data=` pode ser anterior à pedida se
  aquela manhã falhou) e `data_referencia` (o pregão desses dados). Campos a mais não quebram quem lê o
  envelope: para a interface, nada muda em relação aos arquivos. O `/docs` explica a diferença na descrição
  da API e em cada parâmetro `data`.
- `historico` usa o formato compacto das séries: `{"ticker","campo","datas":[…],"valores":[…],"status":{…}}`,
  com status em trechos e só os não-ok. O `campo` é validado por expressão regular e contra os campos que
  existem no registro, e vai para o SQL como parâmetro, nunca colado no texto.

**Códigos:** `200` ok · `401` sem chave ou chave errada (`{"detail":"chave de API ausente ou inválida"}`) ·
`404` ticker inexistente ou nada publicado até aquela data (`{"detail":"ticker XPTO4 não encontrado"}`) ·
`422` parâmetro inválido (`janela=7x`, data malformada; o FastAPI gera sozinho) · `405` qualquer método que não
seja GET · `503` banco fora do ar.

### Exemplo real — `GET /v1/resumo_acao/PETR4` (pasta 23/10; pregão 22/10)

Valores tirados de `saida_completa/2026-10-23/resumo_acao.json`. O registro completo tem mais campos
(volatilidade_20d, margens, ebitda, endividamento…), aqui encurtados com `…`:

```json
{
  "pacote": "resumo_acao",
  "versao_contrato": "1.0",
  "chave": ["data_referencia", "ticker"],
  "gerado_em": "2026-10-25T12:42:23-03:00",
  "dados_ate": "2026-10-23T08:00:00-03:00",
  "data_publicacao": "2026-10-23",
  "data_referencia": "2026-10-22",
  "registros": [
    {
      "versao_contrato": "1.0",
      "gerado_em": "2026-10-25T12:42:23-03:00",
      "dados_ate": "2026-10-23T08:00:00-03:00",
      "data_referencia": "2026-10-22",
      "ticker": "PETR4",
      "cotacao": {
        "fechamento":   { "valor": 57.83000183105469,    "status": "ok" },
        "variacao_dia": { "valor": 0.0013853130918559664, "status": "ok" }
      },
      "variacao_preco": { "m1": { "valor": 0.19607039619675848, "status": "ok" },
                          "a1": { "valor": 0.9636672439013743,  "status": "ok" }, "…": "…" },
      "pl":  { "valor": 5.956419503127698, "status": "ok", "texto": null,
               "setor": { "valor": 14.219568740040916, "status": "ok" } },
      "pvp": { "valor": 1.6534195981777642, "status": "ok",
               "setor": { "valor": 0.810243891644056, "status": "ok" } },
      "dividend_yield": { "valor": 0.051097198278371704, "status": "ok",
                          "setor": { "valor": 0.005857925459022691, "status": "ok" } },
      "beta": { "valor": 0.5036347397818852, "status": "ok",
                "texto": "0,50: move-se bem menos que o Ibovespa" },
      "balanco_referencia": { "data_fim_periodo": "2026-06-30", "data_entrega": "2026-08-06", "status": "ok" },
      "setor_comparacao_fraca": false,
      "…": "…"
    }
  ],
  "removidos": []
}
```

`GET /v1/series_diarias/PETR4?janela=1a` (fim da série):

```json
{ "pacote": "series_diarias", "versao_contrato": "1.0", "chave": ["ticker","data"], "…": "…",
  "registros": [ { "ticker": "PETR4", "tipo": "acao", "nome": "Petrobras",
    "substituir_serie": true, "inicio_janela": "2025-10-16",
    "datas": ["2025-10-16", "…", "2026-10-20", "2026-10-21", "2026-10-22"],
    "valores": { "fechamento": ["…", 58.209999084472656, 57.75, 57.83000183105469],
                 "pl": ["…", 5.9785694402638025, 5.95427447457871, 5.956419503127698], "…": "…" },
    "status": {} } ],
  "removidos": [] }
```

`GET /v1/resumo_acao/PETR4/historico?campo=pl` (depois de 2 publicações):

```json
{ "ticker": "PETR4", "campo": "pl",
  "datas": ["2026-10-22", "2026-10-23"], "valores": [5.956419503127698, 6.01], "status": {} }
```

`401`: `{"detail": "chave de API ausente ou inválida"}` · `404`: `{"detail": "ticker XPTO4 não encontrado"}`

---

## e) Testes

### Dados de teste
`ferramentas/gerar_dados_teste.py` (roda uma vez, lê só `saida_completa/2026-10-23` e `saida/2026-10-23/execucao.json`)
grava em `tests/dados/`:
- **2026-10-23/**: recorte real com 3 ações (PETR4, VALE3, ITUB4), as séries delas + CDI + IBOV +
  `SETOR_SEM:` das 3, os trimestres e proventos delas, as notícias delas, mercado_dia e as duas execucao.json
  (com `pacotes_enviados` ajustado ao recorte). Só dados públicos de mercado.
- **2026-10-26/**: derivado do anterior para simular o dia seguinte: `data_referencia` 23/10, +1 ponto em cada
  série, 1 ponto antigo de PETR4 corrigido, 1 trimestre de VALE3 alterado, 1 provento de ITUB4 sumido, PL de
  PETR4 diferente no resumo.
- **2026-10-27/**: só `saida/.../execucao.json` com `status: "falhou"`, sem completa.

### Unitários (sem banco), a maioria
- `config`: NOME_BANCO ≠ `tcc_processador` (incluindo `tcc_scraper`, `TCC_PROCESSADOR`, vazio) → recusa; fallback
  USER_ESCRITA/LEITURA → USER_BANCO; prefixo.
- `guarda_sql`, **"nunca outro schema"**: recusa `SELECT * FROM tcc_scraper.x`, `` `tcc_scraper`.`x` ``,
  `outro.tabela`, `'tcc_scraper'` dentro de string, `information_schema.tables`, `USE tcc_scraper`,
  `DROP DATABASE`, `DROP TABLE acoes`, `GRANT`, `DELETE`, `TRUNCATE`, `SELECT 1; DROP …`, `/*!50000 DROP */`,
  `apelido.coluna`. Aceita os 5 comandos permitidos e `tcc_processador.acoes`.
- Guarda no modo teste: recusa `SELECT … FROM acoes` e aceita `FROM teste_acoes`. A função de limpeza recusa
  rodar sem o prefixo de teste e recusa nomes fora de `^teste_`.
- `validador`: o recorte real passa. Casos alterados falham: status inválido, `ok` com valor null, valor com
  `indisponivel`, `versao_contrato` 2.0, chave duplicada, `datas` e `valores` de tamanhos diferentes, contagem ≠
  `pacotes_enviados`.
- `contrato.hash`: ignora `gerado_em`, `dados_ate` e `versao_contrato`; muda quando um valor muda.
- `series`: compacto → pontos → compacto devolve **exatamente** a série do arquivo (inclusive status em trechos);
  janelas 1m/6m/1a.
- `planejador` (lógica pura, com o estado atual em memória): cada linha da tabela de c), inclusive buraco,
  republicação, trava de 20% e "D < U já completa".
- API com repositório falso: 401 sem chave e com chave errada; 404 ticker inexistente; 404 top 5 vazio; null
  continua null (nunca 0); POST → 405; cabeçalho CORS só para origem configurada; `/docs` abre.

### Integração (banco real, só `teste_*`, pulados sem `PROC_API_TESTES_COM_BANCO=1`)
Uma fixture força `PROC_API_PREFIXO_TABELAS=teste_`, cria as tabelas `teste_*` e, no fim, apaga só as `teste_*`.
1. `--criar-tabelas` duas vezes → sem erro, mesmas tabelas.
2. Publicar 23/10 → contagens = registros dos arquivos; a API (TestClient no banco) devolve **cada registro igual
   ao do arquivo**, comparado campo a campo, para todos os pacotes e tickers do recorte.
3. Publicar 26/10 → fotos dobram, séries +1 ponto cada, 1 linha em `correcoes`, 1 versão nova de fundamentos,
   1 provento fechado; `?data=2026-10-23` ainda devolve o estado antigo; `historico?campo=pl` tem 2 pontos.
4. Republicar 26/10 e 23/10 → "0 novos" em tudo, contagens iguais.
5. 27/10 (falhou) → só execução; `/v1/execucao/ultima` mostra "falhou"; o resto igual.
6. Buraco: publicar 26/10 antes de 23/10 e depois 23/10 → nada de 26/10 muda.
7. Atomicidade: falha injetada no meio da gravação → contagens idênticas às de antes.
8. Conexão da API é READ ONLY: um INSERT por ela falha com erro 1792.
9. **Modo teste nunca toca tabela real:** um espião grava todo SQL executado no cenário inteiro e o teste
   confere que toda tabela citada começa com `teste_`. Também confere que o número de tabelas sem prefixo no
   banco é o mesmo antes e depois.

---

## f) Arquivos do projeto
`CLAUDE.md` próprio (regras de acesso, banco, arquitetura, pegadinhas: ordem das chaves do JSON, D ≠
data_referencia, hash sem metadados, DDL fora da transação), `.claude/settings.json` (o mesmo deny),
`.gitignore`, `env.exemplo`, `.venv` (já existe), os três requirements, `git init` sem commit.

## g) Guia para você (vai detalhado no fim da implementação, com o que deve aparecer em cada passo)
1. `.venv` + `pip install -r requirements-dev.txt` → termina sem erro vermelho.
2. `python -m pytest` → "N passed, M skipped" (os skipped são os de banco). Depois, com
   `PROC_API_TESTES_COM_BANCO=1`, tudo "passed".
3. `python -m publicador --criar-tabelas` → lista de tabelas "ok". No Cursor, na extensão de banco: conexão →
   `tcc_processador` → Tables → as 14 tabelas, todas vazias.
4. `python -m publicador --data 2026-10-23` → o resumo da seção c); `--conferir` mostra as contagens.
5. `uvicorn api.main:app --host 127.0.0.1 --port 8000` → abrir `http://127.0.0.1:8000/docs` → "Authorize" com
   a chave → `/v1/resumo_acao/{ticker}` → "Try it out" → PETR4 → Execute → comparar com o registro de PETR4 em
   `resumo_acao.json` (mesmos valores e mesma ordem: os registros ficam em texto JSON exato).
6. Quando houver a completa de outro dia: `--data <dia>` → `historico?campo=pl` com 2 pontos e `--conferir`
   com as fotos dobradas.

## h) Para depois (só a lista)
- Chamar o publicador ao fim da rotina do processamento.
- Cartão "publicação" no painel (lê `publicacoes`).
- Onde hospedar a API (e trocar para os usuários ESCRITA/LEITURA no MySQL).
- Atualizar o Guia da interface com a URL, a chave e `?data` / `historico`.
- Se o banco crescer: guardar os itens de notícia uma vez por id.

---

## Ordem de implementação (para os implementadores)
1. Esqueleto + `comum/` (config, guarda, tabelas, contrato, series) + testes unitários deles.
2. `ferramentas/gerar_dados_teste.py` + geração de `tests/dados/`.
3. Em paralelo: (a) publicador (leitor, validador, planejador, gravador, resumo, CLI) + unitários;
   (b) api (repositório, rotas, segurança) + unitários com repositório falso.
4. `sql/esquema.sql` + testes de integração (`teste_*`), rodados por mim com `PROC_API_TESTES_COM_BANCO=1`.
5. CLAUDE.md, settings, .gitignore, env.exemplo, `git init`; remover `ferramentas/listar_tabelas.py`.
6. Verificação final: suíte inteira e resumo para você.

## Séries: emenda dos pontos antigos (aprovado em 25/10/2026)

**Fatos (conferidos na completa de 23/10):** o `fechamento` **não** é ajustado por proventos. A razão
`indice_retorno_total ÷ fechamento` salta 260 vezes no ano, e 249 desses saltos caem no pregão seguinte a uma
data-com. Dividendo não muda nenhum ponto antigo (spec 8.3). O que muda para trás:
- desdobramento, grupamento e bonificação: `fechamento` e `volume`, por um fator, antes do evento;
- mudança da âncora da base 100: `indice_retorno_total` inteiro, por um fator;
- mudança de composição do setor: `SETOR_SEM`, sem fator único;
- balanço reapresentado: `pl` e `pvp`;
- provento atrasado: `dividend_yield`.

| Tipo | Campos proporcionais (recebem emenda) | Só aviso | Mantidos como publicados |
|---|---|---|---|
| acao, etf | fechamento, volume, indice_retorno_total | — | pl, pvp, dividend_yield |
| indice | indice_retorno_total | fechamento (valor oficial) | — |
| taxa | indice_retorno_total | — | — |
| setor_sem_empresa | indice_retorno_total | — | n_empresas |

**Regra (a cada completa, só nos modos normal e republicação; nunca no modo buraco):**
1. d0 = 1ª data da janela nova. Antigos = pontos do banco com data < d0. Sobreposição = datas presentes nos dois.
2. Para cada campo proporcional: r = novo ÷ antigo em cada data da sobreposição em que os dois valores existem e o
   antigo ≠ 0.
3. **Fator da emenda k** = r dos 5 primeiros pontos válidos entre as 21 primeiras datas da sobreposição (mínimo 3
   pontos). Eles precisam concordar com tolerância relativa de 1e-6.
4. |k − 1| ≤ 1e-6 → sem emenda (caso normal, inclusive em dia de dividendo).
5. k fora de [0,01; 100], pontos que não concordam ou menos de 3 pontos com r ≠ 1 na sobreposição →
   **`recusado`**. Os antigos não são tocados, o resumo traz um aviso e a API serve os antigos daquele campo
   (data ≤ `ate` da recusa mais recente) como `null` + `indisponivel`. No banco, nada é apagado.
6. Senão, classifica pela sobreposição inteira:
   - **`uniforme`**: r ≈ k em tudo (mudança de âncora);
   - **`evento`**: r ≈ k antes de uma data S e r ≈ 1 a partir de S (desdobramento);
   - **`irregular`**: o resto (SETOR_SEM com composição nova).
   Nos três casos, os antigos são multiplicados por k.
7. "Só aviso" (fechamento de índice): classifica do mesmo jeito, mas `aplicado = false`, não ajusta e não
   recusa (os antigos continuam servidos).
8. Os pontos da janela sempre recebem os valores novos (o processamento é a verdade dentro da janela).
   - **Com registro ponto a ponto em `correcoes`**: só quando algum campo mudou sem ser explicado pelo fator, ou
     seja, fora do trecho r ≈ k de um campo `uniforme` ou `evento`.
   - **Sem registro ponto a ponto**: os pontos antigos multiplicados pela emenda; o fator registrado em
     `series_ajustes` permite recuperar o valor anterior.
9. **Uma linha em `series_ajustes` por (série, campo, D)** que tenha k ≠ 1. Ela cobre a janela e os antigos:
   `fator`, `de`, `ate`, `pontos`, `origem` (`emenda` se mexeu em antigos; `janela` se só na janela),
   `classificacao`, `aplicado`, `detalhe` (detalhamento janela/antigos, data S do evento, razões da emenda).
10. **Idempotência:** depois de publicar D, a janela do banco é igual à da completa. Republicar D dá k = 1 e não
    aplica nada de novo.
11. Pontos dentro de [d0, última data] que estão no banco mas não vieram na completa → `removido_em = D` (com
    linha em `correcoes`). A API os ignora. Nada é apagado.

**API:** `janela` até `1a` = idêntica à última completa. `2a` e `max` incluem os antigos emendados e o campo
extra `inicio_ultima_completa` (data do 253º ponto contado do fim).

**Volume esperado de `series_ajustes`:**
- dia de dividendo: 0 linhas;
- desdobramento: 2 linhas (fechamento e volume);
- mudança de âncora: 1 linha por série;
- composição nova de um setor: 1 linha (+ correções ponto a ponto da janela).

**Testes obrigatórios (dados sintéticos pequenos):**
1. dia de dividendo: nenhum antigo muda, `series_ajustes` vazio;
2. desdobramento 1:2 na janela: `evento`, antigos × k e `2a` sem degrau na emenda;
3. mudança de âncora: `uniforme`, 1 linha por série;
4. fator absurdo (AZUL3): `recusado`, aviso, antigos `indisponivel` na API e intactos no banco;
5. SETOR_SEM com composição nova: `irregular`, emenda aplicada;
6. republicar o mesmo dia não aplica o fator duas vezes.

## Mudanças no esquema depois da aprovação (25/10/2026)
- **Nova tabela `series_ajustes`** (14ª), também na lista fechada da guarda.
- `publicacoes.metadados` (JSON): por pacote, `{versao_contrato, chave, gerado_em, dados_ate}` do envelope. A
  API remonta o envelope de séries e versionadas a partir dele.
- `series_pontos.removido_em` (DATE NULL): ponto retirado pela completa sem apagar a linha.
- **`registro`, `valores`, `status` (pontos) e `conteudo_anterior` viraram texto JSON exato** (`LONGTEXT`/`TEXT` +
  `CHECK (JSON_VALID(...))`), não o tipo JSON. Motivo, descoberto no teste de integração: o tipo JSON do MySQL
  8.0.46 devolve alguns números com o último dígito arredondado (`0.9636672439013743` → `…744`). Isso quebrava
  "a API devolve exatamente o arquivo" e a idempotência das séries (o hash recalculado mudava). Efeitos: a API
  devolve o registro idêntico ao arquivo, **inclusive na ordem das chaves**; o histórico extrai o campo em
  Python, sem JSON_EXTRACT. As colunas sem números de dados (hashes, contagens, metadados, erros, chave, campos,
  detalhe) continuam JSON.

## Simplificação do modo buraco
Toda D < U (com ou sem `completa` anterior) roda no **modo buraco**: só insere o que falta e nunca altera ou
fecha nada. Diferenças viram aviso no resumo. Isso torna "republicar uma data antiga" idempotente por construção,
sem precisar de `--forcar`. O `--forcar` fica só para a trava dos versionados. Nas versionadas, o modo buraco:
- chave nunca vista → versão fechada `[D, próxima D completa)`;
- D anterior à 1ª versão da chave, conteúdo diferente → versão fechada `[D, 1ª.valido_desde)`;
- D anterior à 1ª versão da chave, conteúdo igual → a 1ª versão passa a valer desde D (só antecipa o
  `valido_desde`; a resposta dos dias seguintes não muda);
- qualquer outro caso → nada.

## Detalhes fixados na implementação da guarda
- `PROC_API_PREFIXO_TABELAS` só aceita `""` (tabelas reais) ou `"teste_"`. Qualquer outro valor → recusa iniciar.
- A guarda tem lista fechada de tabelas: só aceita as 14 tabelas do esquema, com o prefixo ativo. No modo teste,
  isso já impede tocar uma tabela real.
- Os índices ficam dentro do `CREATE TABLE IF NOT EXISTS` (o MySQL 8 não tem `CREATE INDEX IF NOT EXISTS`), e é
  isso que deixa o `--criar-tabelas` idempotente. `CREATE INDEX` continua permitido pela guarda, mas não é usado.
