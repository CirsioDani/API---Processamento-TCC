# api-tcc — regras do projeto

API de dados da interface do TCC. Duas peças que só se encontram no banco MySQL `tcc_processador`:
- **publicador/** lê os pacotes do processamento e grava no banco por conexão direta (roda no PC do usuário,
  depois da rotina do processamento);
- **api/** (FastAPI) só lê e serve a interface. **Só GET**, nenhuma rota de escrita. Não importa o publicador nem
  o processamento.

Os dois usam só `comum/` (config, conexão, guarda de SQL, tabelas, contrato, séries).

Documentos: `docs/plano_api.md` (plano aprovado e decisões) e `docs/contratos_internos.md` (interface de cada
módulo).

## Acesso (obrigatório)
- **Nunca abrir, ler, listar, buscar ou citar em comando nenhum `.env`.** O código lê o `.env` via python-dotenv;
  o agente não. O modelo das variáveis é `env.exemplo` (sem ponto, por causa do deny de `.env.*`).
- Escrever arquivos **só dentro de `api-tcc/`**.
- Do `processamento-tcc`, só **ler** `saida/`, `saida_completa/`, `docs/` e `ESPECIFICACAO.md` (seção 8 =
  contrato 1.0, fonte oficial; o "Guia da interface" é online). Nunca rodar, importar ou mudar nada lá.
- Não tocar na pasta da coleta nem em `extracao-dados-tcc-main/`.
- Sem commit, a não ser que o usuário peça.

## Banco (obrigatório)
- MySQL 8. Banco único: `tcc_processador` (vem de `NOME_BANCO`). O código recusa iniciar com outro nome.
  `tcc_scraper` (banco da coleta) é só leitura e **não é usado** aqui.
- Todo SQL passa por `comum/guarda_sql.verificar`:
  - só SELECT / INSERT INTO / UPDATE / CREATE TABLE IF NOT EXISTS / CREATE INDEX;
  - nenhum outro schema e nenhum nome qualificado `x.y` (então nada de `apelido.coluna`);
  - nada de DROP / DELETE / ALTER / GRANT / USE / REPLACE / aspas duplas / `@`;
  - lista fechada das 14 tabelas de `comum/tabelas.py`.
- A API conecta com `SET SESSION TRANSACTION READ ONLY` e chama a guarda com `somente_leitura=True`.
- **Nas tabelas reais (sem prefixo) o agente não executa nada**: nem `--criar-tabelas`, nem `--data`. Quem roda é o
  usuário.
- Testes de integração: só tabelas `teste_*` (`PROC_API_PREFIXO_TABELAS=teste_`, forçado pela fixture). Ficam
  pulados sem `PROC_API_TESTES_COM_BANCO=1`. O único DROP possível é `sql_apagar_tabela_de_teste` (só `teste_*`).
- Preparado para 2 usuários: `USER_ESCRITA`/`SENHA_ESCRITA` (publicador) e `USER_LEITURA`/`SENHA_LEITURA` (API),
  com fallback para `USER_BANCO`/`SENHA_BANCO`.

## Pegadinhas
- **D ≠ data_referencia.** D = nome da pasta = manhã da rotina = `?data=` da API. `data_referencia` = pregão que
  vem nos registros (a pasta 2026-10-23 traz o pregão 2026-10-22). Toda resposta da API traz os dois.
- O hash de registro **ignora** `gerado_em`, `dados_ate` e `versao_contrato` (mudam a cada execução).
- `CREATE TABLE` encerra a transação no MySQL: tabelas só por `--criar-tabelas`, nunca no meio de uma publicação.
  Os índices ficam dentro do CREATE TABLE (não existe CREATE INDEX IF NOT EXISTS).
- Registros e pontos ficam em **texto JSON exato** (LONGTEXT/TEXT + CHECK JSON_VALID), não no tipo JSON: o tipo
  JSON do MySQL arredonda o último dígito de alguns números. Não use JSON_EXTRACT para devolver valores à API
  (extraia em Python).
- Versionados: uma versão que não mudou guarda o `gerado_em`/`dados_ate` do dia em que nasceu.
- Uma publicação = uma transação. Republicar não duplica; D antiga roda no modo buraco (só insere o que falta).
- Trava de 20% só nos versionados (acoes, fundamentos_trimestrais, proventos); `--forcar` libera.
- Séries: o fechamento **não** é ajustado por proventos. A emenda dos pontos antigos (fator k pelo início da
  janela; uniforme/evento/irregular/recusado) está no plano. pl/pvp/dividend_yield/n_empresas antigos ficam
  como publicados.
- Ausência = `null` + status, nunca 0.
- Ponto de série com diferença relativa ≤ 1e-6 (mesmo status) = ruído numérico: grava o valor novo, sem linha em
  `correcoes`. Pontos e correções vão em lote (`gravador.ordenar_para_lotes`); não volte a UPDATE um a um.

## Deploy (Railway)
- Só a **API** vai para o Railway, pelo `Dockerfile` da raiz (python:3.12-slim, usuário sem root,
  `uvicorn api.servidor:app --host 0.0.0.0 --port ${PORT:-8000}`). O publicador continua no PC do usuário.
- A imagem leva só `requirements-api.txt`, `api/` e `comum/`. O `.dockerignore` é **lista de permissão** (`*` e
  depois `!` só nesses três): arquivo novo fora deles não entra na imagem; se a API passar a precisar dele, inclua
  nos dois arquivos. Nunca permita `.env`.
- `requirements-api.txt` tem versões **fixadas** (as da `.venv` em que a suíte passa) + `cryptography` (login
  `caching_sha2_password` do MySQL 8). Atualizou a `.venv`? Atualize os pinos e rode a suíte.
- No painel do Railway ficam só os valores das variáveis da API (lista em `docs/plano_api.md`, seção Deploy).
  Healthcheck: `/v1/saude` (público; 503 se o banco não responde). Toda outra rota exige `X-API-Key`
  (`test_toda_rota_fora_de_saude_exige_chave`).
- Conferir a API publicada: `python ferramentas/conferir_api.py --data AAAA-MM-DD --url https://<domínio>`, com a
  chave do Railway em `PROC_API_CHAVE_CONFERIR` (sem ela, usa a primeira de `PROC_API_CHAVES`).
- O agente não faz deploy nem mexe no painel: só prepara os arquivos.

## Comandos
- Testes: `./.venv/Scripts/python -m pytest -q` (integração: com `PROC_API_TESTES_COM_BANCO=1`).
- Publicador (usuário): `python -m publicador --criar-tabelas | --data AAAA-MM-DD [--forcar] | --conferir`.
- API (usuário): `python -m uvicorn api.servidor:app --host 127.0.0.1 --port 8000` → `/docs`.
