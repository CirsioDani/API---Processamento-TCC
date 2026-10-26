-- =====================================================================================================
-- Esquema do banco tcc_processador (API de dados do TCC)
--
-- Quem roda: o usuário, com `python -m publicador --criar-tabelas`. O publicador troca {p} pelo prefixo
-- ('' = tabelas reais; 'teste_' = testes de integração), separa os comandos e passa cada um pela guarda
-- de SQL antes de executar.
--
-- Regras deste arquivo:
--   * só CREATE TABLE IF NOT EXISTS: rodar de novo não muda nada (idempotente) e nunca apaga dados;
--   * nunca DROP, ALTER ou TRUNCATE;
--   * os índices ficam dentro do CREATE TABLE (o MySQL 8 não tem CREATE INDEX IF NOT EXISTS);
--   * comentários nunca contêm ponto e vírgula (o separador de comandos)
--   * registros e pontos ficam em TEXTO JSON exato (LONGTEXT/TEXT + CHECK JSON_VALID), não no tipo JSON:
--     o tipo JSON do MySQL devolve alguns números com o último dígito arredondado
--     (0.9636672439013743 volta 0.9636672439013744), e a API precisa devolver exatamente o arquivo.
--
-- Três famílias:
--   FOTOS DO DIA  (só acumulam; uma linha por chave + data_referencia): resumo_acao, noticias_dia,
--                 mercado_dia, execucao, experimento_top, experimento_historico
--   SÉRIES        (só acumulam; um ponto por série + data): series_catalogo, series_pontos,
--                 series_ajustes (fatores da emenda dos pontos antigos, ver docs/plano_api.md)
--   VERSIONADAS   (valido_desde / valido_ate): acoes, fundamentos_trimestrais, proventos
--   APOIO         publicacoes (uma linha por data publicada), correcoes (versões anteriores substituídas)
--
-- Datas:
--   data_publicacao = D = nome da pasta = manhã da rotina (é o ?data= da API)
--   data_referencia = pregão a que os dados se referem (vem de dentro do registro)
--   valido_desde / valido_ate = datas D de publicação
-- =====================================================================================================


-- -----------------------------------------------------------------------------------------------------
-- APOIO
-- -----------------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {p}publicacoes (
  data_publicacao   DATE         NOT NULL,
  status            VARCHAR(16)  NOT NULL                COMMENT 'completa | so_execucao | rejeitada',
  data_referencia   DATE         NULL                    COMMENT 'pregão dos dados desta publicação',
  pasta_completa    VARCHAR(255) NULL,
  pasta_saida       VARCHAR(255) NULL,
  hashes_arquivos   JSON         NOT NULL                COMMENT 'sha256 de cada arquivo lido',
  contagens_novas   JSON         NOT NULL                COMMENT 'quantos registros novos por tabela',
  execucao_status   VARCHAR(16)  NULL                    COMMENT 'status da rotina daquela manhã',
  erros             JSON         NULL                    COMMENT 'motivos, quando rejeitada',
  metadados         JSON         NULL                    COMMENT 'por pacote: versao_contrato, chave, gerado_em, dados_ate',
  inicio            DATETIME(3)  NOT NULL,
  fim               DATETIME(3)  NULL,
  vezes_publicada   INT          NOT NULL DEFAULT 1,
  PRIMARY KEY (data_publicacao),
  KEY ix_publicacoes_status (status, data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}correcoes (
  id                BIGINT       NOT NULL AUTO_INCREMENT,
  tabela            VARCHAR(48)  NOT NULL                COMMENT 'tabela de origem, sem prefixo',
  chave             JSON         NOT NULL                COMMENT 'chave natural do registro corrigido',
  data_troca        DATE         NOT NULL                COMMENT 'D que trouxe a versão nova',
  hash_anterior     CHAR(64)     NOT NULL,
  conteudo_anterior LONGTEXT     NOT NULL CHECK (JSON_VALID(conteudo_anterior)),
  hash_novo         CHAR(64)     NOT NULL,
  PRIMARY KEY (id),
  KEY ix_correcoes_tabela (tabela, data_troca)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- -----------------------------------------------------------------------------------------------------
-- FOTOS DO DIA
-- -----------------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {p}resumo_acao (
  ticker            VARCHAR(32)  NOT NULL,
  data_referencia   DATE         NOT NULL,
  data_publicacao   DATE         NOT NULL                COMMENT 'D que gravou esta linha',
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL                COMMENT 'registro do pacote, inteiro (texto JSON exato)' CHECK (JSON_VALID(registro)),
  PRIMARY KEY (ticker, data_referencia),
  KEY ix_resumo_acao_pub (ticker, data_publicacao),
  KEY ix_resumo_acao_dia (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}noticias_dia (
  ticker            VARCHAR(32)  NOT NULL,
  data_referencia   DATE         NOT NULL,
  data_publicacao   DATE         NOT NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (ticker, data_referencia),
  KEY ix_noticias_dia_pub (ticker, data_publicacao),
  KEY ix_noticias_dia_dia (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}mercado_dia (
  data_referencia   DATE         NOT NULL,
  data_publicacao   DATE         NOT NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (data_referencia),
  KEY ix_mercado_dia_pub (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}execucao (
  data_referencia   DATE         NOT NULL                COMMENT 'no pacote execucao, é a própria D',
  rodada            INT          NOT NULL,
  status            VARCHAR(16)  NOT NULL                COMMENT 'ok | em_andamento | parcial | falhou',
  data_publicacao   DATE         NOT NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (data_referencia, rodada),
  KEY ix_execucao_pub (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}experimento_top (
  data_referencia   DATE         NOT NULL,
  data_publicacao   DATE         NOT NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (data_referencia),
  KEY ix_experimento_top_pub (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}experimento_historico (
  data_referencia   DATE         NOT NULL,
  data_publicacao   DATE         NOT NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (data_referencia),
  KEY ix_experimento_historico_pub (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- -----------------------------------------------------------------------------------------------------
-- SÉRIES
-- -----------------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {p}series_catalogo (
  ticker              VARCHAR(32)  NOT NULL              COMMENT 'PETR4, CDI, IBOV, SETOR_SEM:PETR4',
  tipo                VARCHAR(24)  NOT NULL              COMMENT 'acao | etf | indice | taxa | setor_sem_empresa',
  nome                VARCHAR(255) NOT NULL,
  campos              JSON         NOT NULL              COMMENT 'nomes dos campos de valores, na ordem do pacote',
  primeira_publicacao DATE         NOT NULL,
  ultima_publicacao   DATE         NOT NULL,
  PRIMARY KEY (ticker)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}series_pontos (
  ticker            VARCHAR(32)  NOT NULL,
  data              DATE         NOT NULL,
  valores           TEXT         NOT NULL                COMMENT 'valor de cada campo nesta data' CHECK (JSON_VALID(valores)),
  status            TEXT         NOT NULL                COMMENT 'só os campos não-ok, vazio se tudo ok' CHECK (JSON_VALID(status)),
  hash              CHAR(64)     NOT NULL,
  data_publicacao   DATE         NOT NULL                COMMENT 'D em que o ponto entrou',
  removido_em       DATE         NULL                    COMMENT 'D em que a completa deixou de trazer o ponto',
  PRIMARY KEY (ticker, data),
  KEY ix_series_pontos_pub (data_publicacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}series_ajustes (
  id                BIGINT       NOT NULL AUTO_INCREMENT,
  ticker            VARCHAR(32)  NOT NULL,
  campo             VARCHAR(48)  NOT NULL,
  data_publicacao   DATE         NOT NULL                COMMENT 'D que trouxe a mudança',
  fator             DOUBLE       NULL                    COMMENT 'k = novo / antigo (nulo se não calculável)',
  de                DATE         NULL                    COMMENT '1º ponto afetado',
  ate               DATE         NULL                    COMMENT 'último ponto afetado (na recusa: último antigo)',
  pontos            INT          NOT NULL,
  origem            VARCHAR(8)   NOT NULL                COMMENT 'emenda | janela',
  classificacao     VARCHAR(12)  NOT NULL                COMMENT 'uniforme | evento | irregular | recusado',
  aplicado          TINYINT(1)   NOT NULL,
  detalhe           JSON         NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_series_ajustes (ticker, campo, data_publicacao),
  KEY ix_series_ajustes_class (ticker, classificacao, campo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- -----------------------------------------------------------------------------------------------------
-- VERSIONADAS  (valido_ate NULL = versão atual)
-- -----------------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS {p}acoes (
  id                BIGINT       NOT NULL AUTO_INCREMENT,
  ticker            VARCHAR(32)  NOT NULL,
  valido_desde      DATE         NOT NULL,
  valido_ate        DATE         NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (id),
  UNIQUE KEY uq_acoes (ticker, valido_desde),
  KEY ix_acoes_atual (valido_ate, ticker)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}fundamentos_trimestrais (
  id                BIGINT       NOT NULL AUTO_INCREMENT,
  ticker            VARCHAR(32)  NOT NULL,
  data_fim_periodo  DATE         NOT NULL,
  valido_desde      DATE         NOT NULL,
  valido_ate        DATE         NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (id),
  UNIQUE KEY uq_fundamentos (ticker, data_fim_periodo, valido_desde),
  KEY ix_fundamentos_atual (valido_ate, ticker)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS {p}proventos (
  id                BIGINT       NOT NULL AUTO_INCREMENT,
  ticker            VARCHAR(32)  NOT NULL,
  tipo              VARCHAR(16)  NOT NULL                COMMENT 'JCP | DIVIDENDO | ...',
  data_com          DATE         NOT NULL,
  valido_desde      DATE         NOT NULL,
  valido_ate        DATE         NULL,
  hash              CHAR(64)     NOT NULL,
  registro          LONGTEXT     NOT NULL CHECK (JSON_VALID(registro)),
  PRIMARY KEY (id),
  UNIQUE KEY uq_proventos (ticker, tipo, data_com, valido_desde),
  KEY ix_proventos_atual (valido_ate, ticker)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
