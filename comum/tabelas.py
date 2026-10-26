"""Nomes das tabelas. O nome real é prefixo + nome-base; o prefixo vem da configuração."""

PREFIXO_REAL = ""
PREFIXO_TESTE = "teste_"
PREFIXOS_VALIDOS = (PREFIXO_REAL, PREFIXO_TESTE)

FOTOS = (
    "resumo_acao",
    "noticias_dia",
    "mercado_dia",
    "execucao",
    "experimento_top",
    "experimento_historico",
)
SERIES = ("series_catalogo", "series_pontos", "series_ajustes")
VERSIONADAS = ("acoes", "fundamentos_trimestrais", "proventos")
APOIO = ("publicacoes", "correcoes")

NOMES_BASE = APOIO + FOTOS + SERIES + VERSIONADAS


def validar_prefixo(prefixo: str) -> str:
    if prefixo not in PREFIXOS_VALIDOS:
        raise ValueError(f"Prefixo de tabelas inválido: {prefixo!r}. Use '' (reais) ou 'teste_'.")
    return prefixo


def nome(base: str, prefixo: str) -> str:
    if base not in NOMES_BASE:
        raise ValueError(f"Tabela desconhecida: {base!r}")
    return validar_prefixo(prefixo) + base


def nomes(prefixo: str) -> frozenset[str]:
    """Todas as tabelas que o código pode tocar com este prefixo."""
    validar_prefixo(prefixo)
    return frozenset(prefixo + base for base in NOMES_BASE)
