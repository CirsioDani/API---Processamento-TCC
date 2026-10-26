"""Publicador. Uso: python -m publicador (--criar-tabelas | --data AAAA-MM-DD [--forcar] | --conferir).

Saídas: 0 ok; 1 publicação recusada (pasta sem nada para publicar, validação ou planejamento);
2 configuração, banco ou uso. Nunca imprime credencial.
"""
import argparse
import re
import sys
import time
from datetime import datetime

from comum import banco, config, guarda_sql
from publicador import gravador, resumo

LIMITE_ERROS_IMPRESSOS = 30
_FORMATO_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m publicador",
        description="Publica no banco tcc_processador os pacotes do processamento-tcc.",
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--criar-tabelas", action="store_true",
                       help="cria as tabelas que faltarem (não apaga nada)")
    grupo.add_argument("--data", metavar="AAAA-MM-DD",
                       help="publica a pasta desta data")
    grupo.add_argument("--conferir", action="store_true",
                       help="mostra quantas linhas há em cada tabela")
    parser.add_argument("--forcar", action="store_true",
                        help="libera a trava de queda nas versões (só com --data)")
    return parser


def _data_valida(texto: str) -> bool:
    if not _FORMATO_DATA.fullmatch(texto):
        return False
    try:
        datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _criar(conexao: banco.Conexao) -> int:
    nomes = gravador.criar_tabelas(conexao)
    print(f"Tabelas prontas no banco {guarda_sql.BANCO_PERMITIDO} (prefixo '{conexao.prefixo}'):")
    for nome in nomes:
        print(f"  {nome}")
    return 0


def _conferir(conexao: banco.Conexao) -> int:
    contagens = gravador.contar_tabelas(conexao)
    print(f"Linhas por tabela no banco {guarda_sql.BANCO_PERMITIDO} (prefixo '{conexao.prefixo}'):")
    largura = max(len(nome) for nome in contagens)
    for nome, n in contagens.items():
        print(f"  {nome:<{largura}} {n:>10}")
    return 0


def _publicar(conexao: banco.Conexao, cfg: config.Config, data: str, forcar: bool) -> int:
    # Import tardio: planejador, leitor e validador podem ser carregados só quando há uma publicação.
    from publicador import leitor, planejador, validador

    inicio = datetime.now()
    t0 = time.perf_counter()
    try:
        entrada = leitor.ler(cfg.dir_processamento, data)
    except leitor.ErroEntrada as erro:
        print(f"Não publicado: {erro}")
        return 1

    erros = validador.validar(entrada)
    if erros:
        gravador.registrar_rejeicao(conexao, entrada, erros, inicio)
        print(f"Publicação de {data} recusada: {len(erros)} erro(s) de validação.")
        for erro in erros[:LIMITE_ERROS_IMPRESSOS]:
            print(f"  - {erro}")
        if len(erros) > LIMITE_ERROS_IMPRESSOS:
            print(f"  ... e mais {len(erros) - LIMITE_ERROS_IMPRESSOS}")
        return 1

    estado = gravador.carregar_estado(conexao, entrada)
    try:
        plano = planejador.planejar(entrada, estado, forcar=forcar)
    except planejador.ErroPublicacao as erro:
        gravador.registrar_rejeicao(conexao, entrada, [str(erro)], inicio)
        print(f"Publicação de {data} recusada: {erro}")
        return 1

    gravador.gravar(conexao, entrada, plano, inicio)
    print(resumo.formatar(plano, time.perf_counter() - t0))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _montar_parser()
    args = parser.parse_args(argv)
    if args.forcar and args.data is None:
        parser.error("--forcar só vale junto com --data")
    if args.data is not None and not _data_valida(args.data):
        parser.error(f"data inválida: {args.data!r} (use AAAA-MM-DD)")

    try:
        cfg = config.carregar()
    except config.ErroConfig as erro:
        print(f"Configuração inválida: {erro}", file=sys.stderr)
        return 2

    try:
        with banco.conectar(cfg, "escrita") as conexao:
            if args.criar_tabelas:
                return _criar(conexao)
            if args.conferir:
                return _conferir(conexao)
            return _publicar(conexao, cfg, args.data, args.forcar)
    except banco.ErroBanco as erro:
        print(f"Erro no banco: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
