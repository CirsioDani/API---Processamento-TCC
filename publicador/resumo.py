"""Texto do resumo de uma publicação (o que o usuário lê no terminal depois de `--data`)."""
from comum import tabelas
from publicador.operacoes import CatalogoGravar, FotoAtualizar, FotoInserir, Plano, PontoAtualizar

FOTOS_RESUMO = ("resumo_acao", "noticias_dia", "mercado_dia", "execucao", "experimento_historico")
LARGURA_ROTULO = 30


def _numero(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _contar(n: int, um: str, varios: str) -> str:
    return f"{_numero(n)} {um if n == 1 else varios}"


def _data_br(iso: str) -> str:
    ano, mes, dia = iso.split("-")
    return f"{dia}/{mes}/{ano}"


def _tempo(segundos: float) -> str:
    return f"{segundos:.1f}".replace(".", ",") + " s"


def _linha(rotulo: str, texto: str) -> str:
    return f"  {rotulo:.<{LARGURA_ROTULO}} {texto}"


def _status_execucao(plano: Plano) -> str:
    """Status da rotina daquela manhã (vem do execucao.json); sem ele, o da operação gravada."""
    if plano.execucao_status:
        return plano.execucao_status
    for op in reversed(plano.operacoes):
        if isinstance(op, (FotoInserir, FotoAtualizar)) and op.pacote == "execucao":
            return str(op.registro.get("status", "?"))
    return "inalterada"


def _tem_pacote(plano: Plano, pacote: str) -> bool:
    return any(isinstance(op, (FotoInserir, FotoAtualizar)) and op.pacote == pacote for op in plano.operacoes)


def formatar(plano: Plano, segundos: float) -> str:
    contagens = plano.contagens()
    data = _data_br(plano.data)

    if plano.status_publicacao == "so_execucao":
        cabecalho = f"{data}: só a execução foi gravada (sem carga completa)"
    else:
        cabecalho = f"{data} publicado (modo {plano.modo}, execução: {_status_execucao(plano)}) em {_tempo(segundos)}"

    corpo: list[str] = []
    if not contagens:
        corpo.append("  nada novo (republicação idempotente)" if plano.modo == "republicacao" else "  nada novo")

    for pacote in FOTOS_RESUMO:
        n = contagens.get(pacote, 0)
        if n:
            corpo.append(_linha(pacote, _contar(n, "novo", "novos")))

    if plano.status_publicacao != "so_execucao":
        top = contagens.get("experimento_top", 0)
        if top:
            corpo.append(_linha("top 5 (experimento_top)", _contar(top, "novo", "novos")))
        elif not _tem_pacote(plano, "experimento_top") and "experimento_top" not in plano.metadados:
            corpo.append(_linha("top 5 (experimento_top)", "não publicado (pacote ausente)"))

        pontos = contagens.get("series_pontos", 0)
        series = sum(1 for op in plano.operacoes if isinstance(op, CatalogoGravar))
        if pontos or series:
            texto = f"{_contar(pontos, 'ponto novo', 'pontos novos')} ({_contar(series, 'série', 'séries')})"
            corpo.append(_linha("series_diarias", texto))

        # Só o que vira linha em correcoes: pontos emendados pelo fator e ruído numérico têm linha própria.
        pontos_corrigidos = sum(
            1 for op in plano.operacoes if isinstance(op, PontoAtualizar) and op.anterior is not None
        )
        corrigidos = (pontos_corrigidos + contagens.get("series_pontos_removidos", 0)
                      + contagens.get("series_pontos_reabertos", 0)) + sum(
            n for rotulo, n in contagens.items() if rotulo.endswith("_atualizadas")
        )
        if corrigidos:
            corpo.append(_linha("correções", f"{_numero(corrigidos)} (fotos e pontos de série)"))
        ruido = contagens.get("series_pontos_ruido", 0)
        if ruido:
            corpo.append(_linha("ruído numérico", f"{_contar(ruido, 'ponto', 'pontos')} com ruído numérico (< 1e-6)"))

        ajustes = contagens.get("series_ajustes", 0)
        if ajustes:
            corpo.append(_linha("ajustes de série", _numero(ajustes)))
        recusas = contagens.get("series_recusas", 0)
        if recusas:
            corpo.append(_linha("recusas", _numero(recusas)))

        for pacote in tabelas.VERSIONADAS:
            novas = contagens.get(pacote, 0)
            fechadas = contagens.get(f"{pacote}_fechadas", 0)
            corrigidas = contagens.get(f"{pacote}_corrigidas", 0)
            if novas or fechadas or corrigidas:
                texto = (f"{_numero(novas)} versões novas, {_numero(fechadas)} fechadas, "
                         f"{_numero(corrigidas)} corrigidas")
                corpo.append(_linha(pacote, texto))

    if plano.avisos:
        corpo.append("Avisos:")
        corpo.extend(f"  - {aviso}" for aviso in plano.avisos)

    return "\n".join([cabecalho, *corpo])
