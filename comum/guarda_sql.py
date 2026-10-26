"""Guarda de SQL: todo comando passa por `verificar` antes de chegar ao MySQL.

Regras (cada uma tem teste em tests/unit/test_guarda_sql.py):
  1. Um único comando por chamada. Nada de comentário executável do MySQL (/*! */ ou /*+ */).
  2. Só começa com SELECT, INSERT INTO, UPDATE, CREATE TABLE IF NOT EXISTS ou CREATE [UNIQUE] INDEX.
     Com somente_leitura=True (a API), só SELECT.
  3. Nunca cita outro banco: `tcc_scraper` é recusado em qualquer lugar do texto (inclusive dentro de string).
     Os schemas do sistema (information_schema, mysql, performance_schema, sys) também. Qualquer nome
     qualificado `x.y` com x diferente de tcc_processador é recusado. Por isso o código do projeto não usa
     `apelido.coluna`.
  4. Palavras proibidas mesmo dentro de um comando permitido: DROP, ALTER, TRUNCATE, DELETE, GRANT, REVOKE,
     USE, RENAME, LOAD, CALL, INTO OUTFILE etc.
  5. Lista fechada de tabelas: só as do esquema (comum/tabelas.py), com o prefixo ativo. No modo teste
     (prefixo 'teste_') isso impede tocar qualquer tabela real, e um nome-base sem prefixo também é recusado.

Valores sempre entram como parâmetros (%s) do driver, nunca colados no texto. A guarda analisa o texto do
comando antes da troca dos parâmetros. Literais escritos no próprio código (caminhos JSON, COMMENT) são
permitidos só com aspas simples e sem barra invertida, para a análise não depender do sql_mode do servidor.

Fora da guarda existem só duas exceções fixas:
  * `SET SESSION TRANSACTION READ ONLY`, enviado pela conexão de leitura (comum/banco.py) ao conectar;
  * a limpeza dos testes, cujo comando só pode ser montado por `sql_apagar_tabela_de_teste` (abaixo).
"""
import re

from comum import tabelas

BANCO_PERMITIDO = "tcc_processador"


class SQLRecusado(Exception):
    """Comando bloqueado pela guarda. Nunca chega ao banco."""


_COMANDOS_PERMITIDOS = (
    re.compile(r"^SELECT\b"),
    re.compile(r"^INSERT INTO\b"),
    re.compile(r"^UPDATE\b"),
    re.compile(r"^CREATE TABLE IF NOT EXISTS\b"),
    re.compile(r"^CREATE (UNIQUE )?INDEX\b"),
)
_COMANDO_LEITURA = re.compile(r"^SELECT\b")

_PALAVRAS_PROIBIDAS = re.compile(
    r"\b(DROP|ALTER|TRUNCATE|DELETE|GRANT|REVOKE|USE|RENAME|LOAD|LOAD_FILE|CALL|OUTFILE|DUMPFILE|"
    r"HANDLER|LOCK|UNLOCK|KILL|SHUTDOWN|PREPARE|EXECUTE|DEALLOCATE|REPLACE|FLUSH|RESET|INSTALL|"
    r"UNINSTALL|IMPORT|CLONE|RESTART|SLEEP|BENCHMARK|DATABASE|SCHEMA)\b(?!\s*\(\s*\))",
    re.IGNORECASE,
)
# A exceção (?!\(\)) libera só as funções sem argumento DATABASE() e SCHEMA(), usadas para conferir a conexão.

_OUTROS_BANCOS = re.compile(r"tcc_scraper", re.IGNORECASE)
_SCHEMAS_SISTEMA = re.compile(r"\b(information_schema|performance_schema|mysql|sys)\b", re.IGNORECASE)
_NOME_QUALIFICADO = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*|\*)")
_JUNCAO_POR_VIRGULA = re.compile(r"\bFROM\s+[\w$]+(\s+(AS\s+)?[\w$]+)?\s*,", re.IGNORECASE)

_TABELA_APOS = (
    re.compile(r"\b(?:FROM|JOIN|INTO)\s+([\w$]+)", re.IGNORECASE),
    re.compile(r"(?<!KEY )(?<!FOR )\bUPDATE\s+([\w$]+)", re.IGNORECASE),
    re.compile(r"\bTABLE\s+(?:IF NOT EXISTS\s+)?([\w$]+)", re.IGNORECASE),
    re.compile(r"\bINDEX\s+[\w$]+\s+ON\s+([\w$]+)", re.IGNORECASE),
)
_PALAVRA = re.compile(r"[\w$]+")
_TABELA_DE_TESTE = re.compile(r"^teste_[a-z_]+$")


def verificar(sql: str, prefixo: str, somente_leitura: bool = False) -> None:
    """Recusa (SQLRecusado) qualquer comando fora das regras. Não devolve nada se o comando passar."""
    tabelas.validar_prefixo(prefixo)

    if _OUTROS_BANCOS.search(sql):
        raise SQLRecusado("o comando cita o banco tcc_scraper")

    texto = _limpar(sql).strip()
    while texto.endswith(";"):
        texto = texto[:-1].rstrip()
    if ";" in texto:
        raise SQLRecusado("mais de um comando na mesma chamada")
    if "@" in texto:
        raise SQLRecusado("variáveis (@ e @@) não são permitidas")

    normal = " ".join(texto.split())
    maiusculo = normal.upper()
    if not normal:
        raise SQLRecusado("comando vazio")

    permitidos = (_COMANDO_LEITURA,) if somente_leitura else _COMANDOS_PERMITIDOS
    if not any(padrao.match(maiusculo) for padrao in permitidos):
        lista = "SELECT" if somente_leitura else (
            "SELECT, INSERT, UPDATE, CREATE TABLE IF NOT EXISTS, CREATE INDEX")
        raise SQLRecusado(f"comando não permitido (permitidos: {lista}): {normal[:60]!r}")

    proibida = _PALAVRAS_PROIBIDAS.search(normal)
    if proibida:
        raise SQLRecusado(f"palavra proibida: {proibida.group(1).upper()}")

    sistema = _SCHEMAS_SISTEMA.search(normal)
    if sistema:
        raise SQLRecusado(f"o comando cita o schema do sistema {sistema.group(1)}")

    for qualificado in _NOME_QUALIFICADO.finditer(normal):
        if qualificado.group(1).lower() != BANCO_PERMITIDO:
            raise SQLRecusado(
                f"nome qualificado {qualificado.group(0)!r}: só '{BANCO_PERMITIDO}.tabela' é aceito")
    normal = re.sub(rf"\b{BANCO_PERMITIDO}\s*\.\s*", "", normal, flags=re.IGNORECASE)

    if _JUNCAO_POR_VIRGULA.search(normal):
        raise SQLRecusado("junção por vírgula no FROM não é permitida (use JOIN)")

    permitidas = tabelas.nomes(prefixo)
    for padrao in _TABELA_APOS:
        for achado in padrao.finditer(normal):
            tabela = achado.group(1).lower()
            if tabela not in permitidas:
                raise SQLRecusado(f"tabela fora da lista permitida (prefixo {prefixo!r}): {tabela}")

    if prefixo == tabelas.PREFIXO_TESTE:
        for palavra in _PALAVRA.findall(normal):
            if palavra.lower() in tabelas.NOMES_BASE:
                raise SQLRecusado(f"modo teste: tabela real citada sem o prefixo: {palavra}")


def sql_apagar_tabela_de_teste(nome_tabela: str, prefixo: str) -> str:
    """Única forma de montar um DROP: só no modo teste e só para tabelas teste_* do esquema."""
    if prefixo != tabelas.PREFIXO_TESTE:
        raise SQLRecusado("a limpeza de tabelas só existe no modo teste (prefixo 'teste_')")
    if not _TABELA_DE_TESTE.fullmatch(nome_tabela) or nome_tabela not in tabelas.nomes(prefixo):
        raise SQLRecusado(f"limpeza recusada: {nome_tabela!r} não é uma tabela teste_* do esquema")
    return f"DROP TABLE IF EXISTS `{nome_tabela}`"


def _limpar(sql: str) -> str:
    """Troca comentários e literais por espaço/'?' e tira as crases dos identificadores.

    Segue as regras do MySQL: '-- ' só é comentário se vier seguido de espaço ou fim de linha. Assim nada
    que o servidor executaria fica escondido da análise.
    """
    saida = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("/*", i):
            if sql.startswith(("/*!", "/*+"), i):
                raise SQLRecusado("comentário executável do MySQL (/*! */ ou /*+ */) não é permitido")
            fim = sql.find("*/", i + 2)
            if fim < 0:
                raise SQLRecusado("comentário /* sem fechamento")
            saida.append(" ")
            i = fim + 2
        elif sql.startswith("--", i) and (i + 2 >= n or sql[i + 2] in " \t\r\n"):
            fim = sql.find("\n", i)
            saida.append(" ")
            i = n if fim < 0 else fim + 1
        elif c == "#":
            fim = sql.find("\n", i)
            saida.append(" ")
            i = n if fim < 0 else fim + 1
        elif c == '"':
            raise SQLRecusado('aspas duplas não são permitidas (use aspas simples para texto)')
        elif c == "'":
            j = i + 1
            while True:
                if j >= n:
                    raise SQLRecusado("texto entre aspas sem fechamento")
                if sql[j] == "\\":
                    raise SQLRecusado("barra invertida dentro de texto não é permitida")
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            saida.append(" '?' ")
            i = j + 1
        elif c == "`":
            fim = sql.find("`", i + 1)
            if fim < 0:
                raise SQLRecusado("identificador com crase sem fechamento")
            identificador = sql[i + 1:fim]
            if not re.fullmatch(r"[\w$]+", identificador):
                raise SQLRecusado(f"identificador com crase inválido: {identificador!r}")
            saida.append(identificador)
            i = fim + 1
        else:
            saida.append(c)
            i += 1
    return "".join(saida)
