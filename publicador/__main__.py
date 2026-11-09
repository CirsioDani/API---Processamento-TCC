"""Publicador. Uso: python -m publicador (--criar-tabelas | --data AAAA-MM-DD [--forcar] | --conferir |
--liberar-recusas AAAA-MM-DD [--aplicar] [--motivo TEXTO]).

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
    grupo.add_argument("--liberar-recusas", metavar="AAAA-MM-DD",
                       help="libera as recusas gravadas nesta data (sem --aplicar, só simula)")
    parser.add_argument("--forcar", action="store_true",
                        help="libera a trava de queda nas versões (só com --data)")
    parser.add_argument("--aplicar", action="store_true",
                        help="grava a liberação (só com --liberar-recusas)")
    parser.add_argument("--motivo", metavar="TEXTO", default=None,
                        help="motivo gravado na liberação (só com --liberar-recusas; padrão: decisão de SETOR_SEM)")
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


def _decimal(valor: float | None, casas: int) -> str:
    return "-" if valor is None else f"{valor:.{casas}f}".replace(".", ",")


def _liberar(conexao: banco.Conexao, data: str, aplicar: bool, motivo: str | None) -> int:
    # Import tardio: liberacao depende de planejador, só necessário nesta opção.
    from publicador import liberacao

    recusas, tipos, pontos = gravador.carregar_recusas(conexao, data)
    agora = datetime.now().isoformat(timespec="seconds")
    try:
        libs, ignoradas = liberacao.planejar(recusas, tipos, pontos, motivo or liberacao.MOTIVO_PADRAO, agora)
    except liberacao.ErroLiberacao as erro:
        print(f"Liberação de {data} recusada: {erro}")
        return 1

    print(f"Recusas de {data}: {len(recusas)} lida(s), {len(libs)} liberável(is).")
    for lib in libs:
        r = lib.recusa
        print(f"  {r.ticker} {r.campo}: fator {_decimal(r.fator, 4)}, {len(lib.operacoes)} pontos, "
              f"{r.de} a {r.ate}, costura {_decimal(lib.costura_antes, 6)} para {_decimal(lib.costura_depois, 6)}")
        if lib.alerta:
            faixa = f"[{_decimal(liberacao.FATOR_ALERTA_MIN, 1)}; {_decimal(liberacao.FATOR_ALERTA_MAX, 1)}]"
            print(f"    ATENÇÃO: fator {_decimal(r.fator, 4)} fora de {faixa}")
    for r, razao in ignoradas:
        print(f"  ignorada: {r.ticker} {r.campo}: {razao}")

    if not aplicar:
        print("Simulação: nada gravado.")
        return 0
    gravador.gravar_liberacoes(conexao, libs)
    print(f"{len(libs)} recusas liberadas.")
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
    if args.aplicar and args.liberar_recusas is None:
        parser.error("--aplicar só vale junto com --liberar-recusas")
    if args.motivo is not None and args.liberar_recusas is None:
        parser.error("--motivo só vale junto com --liberar-recusas")
    if args.liberar_recusas is not None and not _data_valida(args.liberar_recusas):
        parser.error(f"data inválida: {args.liberar_recusas!r} (use AAAA-MM-DD)")

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
            if args.liberar_recusas is not None:
                return _liberar(conexao, args.liberar_recusas, args.aplicar, args.motivo)
            return _publicar(conexao, cfg, args.data, args.forcar)
    except banco.ErroBanco as erro:
        print(f"Erro no banco: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
