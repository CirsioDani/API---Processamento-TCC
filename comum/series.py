"""Séries diárias: formato compacto do contrato ⇄ pontos guardados no banco."""
from dataclasses import dataclass

from comum import contrato

JANELAS = {"1m": 21, "3m": 63, "6m": 126, "1a": 253, "2a": 506, "max": None}
PREGOES_COMPLETA = 253


@dataclass(frozen=True)
class Ponto:
    data: str
    valores: dict  # campo -> número ou None (todos os campos da série)
    status: dict   # campo -> status, só os não-ok neste ponto; {} se tudo ok


def _validar_janela(janela: str) -> None:
    if janela not in JANELAS:
        raise ValueError(f"janela inválida: {janela!r}")


def compacto_para_pontos(serie: dict) -> list[Ponto]:
    """Um Ponto por data. Cada trecho de status vale para as datas com de <= data <= ate."""
    datas = serie["datas"]
    valores = serie["valores"]
    status = [dict() for _ in datas]
    for campo, trechos in serie.get("status", {}).items():
        for trecho in trechos:
            for i, data in enumerate(datas):
                if trecho["de"] <= data <= trecho["ate"]:
                    status[i][campo] = trecho["status"]
    return [
        Ponto(data=datas[i], valores={campo: valores[campo][i] for campo in valores}, status=status[i])
        for i in range(len(datas))
    ]


def pontos_para_compacto(pontos: list[Ponto], campos: list[str]) -> tuple[list[str], dict[str, list], dict[str, list[dict]]]:
    """Trechos de status são máximos: pontos consecutivos na lista com o mesmo status no campo."""
    datas = [p.data for p in pontos]
    valores = {campo: [p.valores[campo] for p in pontos] for campo in campos}
    status = {}
    for campo in campos:
        trechos = []
        atual = None  # trecho em construção; None quando o ponto é ok
        for p in pontos:
            st = p.status.get(campo)
            if st is None:
                atual = None
                continue
            if atual is not None and atual["status"] == st:
                atual["ate"] = p.data
            else:
                atual = {"de": p.data, "ate": p.data, "status": st}
                trechos.append(atual)
        if trechos:
            status[campo] = trechos
    return datas, valores, status


def hash_ponto(p: Ponto) -> str:
    return contrato.sha256_texto(contrato.json_canonico({"valores": p.valores, "status": p.status}))


def _aplicar_recusas(pontos: list[Ponto], recusas: dict[str, str]) -> list[Ponto]:
    """Pontos com data <= data_ate do campo recusado viram None com status 'indisponivel'."""
    novos = []
    for p in pontos:
        valores = dict(p.valores)
        status = dict(p.status)
        for campo, data_ate in recusas.items():
            if p.data <= data_ate:
                valores[campo] = None
                status[campo] = "indisponivel"
        novos.append(Ponto(data=p.data, valores=valores, status=status))
    return novos


def montar_registro(meta: dict, catalogo: dict, pontos: list[Ponto], janela: str,
                    recusas: dict[str, str] | None = None, *, inicio_janela: str,
                    inicio_ultima_completa: str | None = None) -> dict:
    """Pontos com data >= inicio_janela (início da janela no calendário de pregões).

    Sem nenhum ponto a partir de inicio_janela: ValueError (a rota devolve 404)."""
    _validar_janela(janela)
    if not pontos:
        raise ValueError("série sem pontos")
    pontos = _aplicar_recusas(pontos, recusas or {})
    recorte = [p for p in pontos if p.data >= inicio_janela]
    if not recorte:
        raise ValueError("série sem pontos na janela")
    datas, valores, status = pontos_para_compacto(recorte, catalogo["campos"])
    registro = {k: meta[k] for k in contrato.METADADOS}
    registro.update(
        ticker=catalogo["ticker"],
        tipo=catalogo["tipo"],
        nome=catalogo["nome"],
        substituir_serie=True,
        inicio_janela=inicio_janela,
    )
    if janela in ("2a", "max") and inicio_ultima_completa is not None:
        registro["inicio_ultima_completa"] = inicio_ultima_completa
    registro.update(datas=datas, valores=valores, status=status)
    return registro
