"""Endpoints da API (só GET). Ver docs/plano_api.md, seção d."""
import secrets
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import AfterValidator, BaseModel

from api.repositorio import Repositorio
from comum import banco, contrato, series
from comum.config import Config

PADRAO_DATA = r"^\d{4}-\d{2}-\d{2}$"
PADRAO_CAMPO = r"^[a-z0-9_]+(\.[a-z0-9_]+)*$"
DESC_DATA = (
    "Data da publicação (pasta/manhã da rotina), AAAA-MM-DD. Mostra os dados como estavam naquela manhã. "
    "Não é o pregão: o pregão vem em data_referencia."
)


def _data_valida(valor: str | None) -> str | None:
    """O padrão AAAA-MM-DD não pega datas inexistentes (2026-13-45): confere no calendário."""
    if valor is not None:
        try:
            date.fromisoformat(valor)
        except ValueError:
            raise ValueError("data inexistente no calendário") from None
    return valor




class Erro(BaseModel):
    """Corpo de toda resposta de erro da API."""
    detail: str


# Respostas de erro documentadas no /docs para todos os endpoints que exigem chave.
ERROS = {
    401: {"model": Erro, "description": "Chave de API ausente ou inválida (cabeçalho X-API-Key).",
          "content": {"application/json": {"example": {"detail": "chave de API ausente ou inválida"}}}},
    404: {"model": Erro, "description": "Ticker inexistente ou nada publicado até a data pedida.",
          "content": {"application/json": {"example": {"detail": "ticker XPTO4 não encontrado"}}}},
}
DESC_JANELA = (
    "Quantos pregões mostrar, até o fim da série: 1m=21, 3m=63, 6m=126, 1a=253, 2a=506, max=tudo. "
    "inicio_janela é o início da janela no calendário de pregões; se a série começa depois, ela é mais curta "
    "que a janela (histórico insuficiente)."
)


def _envelope(pacote: str, registros: list, data_publicacao, data_referencia, meta: dict) -> dict:
    return {
        "pacote": pacote,
        "versao_contrato": meta["versao_contrato"],
        "chave": list(meta["chave"]),
        "gerado_em": meta["gerado_em"],
        "dados_ate": meta["dados_ate"],
        "data_publicacao": data_publicacao,
        "data_referencia": data_referencia,
        "registros": registros,
        "removidos": [],
    }


def _meta_versionada(pub: dict, pacote: str) -> dict:
    """Metadados do pacote guardados na publicação (fallback: contrato 1.0, chave do contrato, sem data)."""
    meta = (pub["metadados"] or {}).get(pacote) or {}
    return {
        "versao_contrato": meta.get("versao_contrato", contrato.VERSAO),
        "chave": meta.get("chave", contrato.PACOTES[pacote].chave),
        "gerado_em": meta.get("gerado_em"),
        "dados_ate": meta.get("dados_ate"),
    }


def _meta_registro(pacote: str, registro: dict) -> dict:
    """Fotos: versão, gerado_em e dados_ate vêm do próprio registro."""
    return {
        "versao_contrato": registro.get("versao_contrato", contrato.VERSAO),
        "chave": contrato.PACOTES[pacote].chave,
        "gerado_em": registro.get("gerado_em"),
        "dados_ate": registro.get("dados_ate"),
    }


def _publicacao(repo: Repositorio, data: str | None) -> dict:
    pub = repo.publicacao_ate(data) if data else repo.ultima_publicacao()
    if pub is None:
        raise HTTPException(status_code=404, detail=f"nada publicado até {data}" if data else "nada publicado ainda")
    return pub


def _resposta_foto(pacote: str, achado, mensagem_404: str) -> dict:
    if achado is None:
        raise HTTPException(status_code=404, detail=mensagem_404)
    registro, data_publicacao, data_referencia = achado
    return _envelope(pacote, [registro], data_publicacao, data_referencia, _meta_registro(pacote, registro))


def _resposta_dia(pacote: str, achado, data: str | None) -> dict:
    """Pacote diário com todos os tickers do pregão alvo (sem ticker na URL)."""
    if achado is None:
        raise HTTPException(status_code=404, detail=f"nada publicado até {data}" if data else "nada publicado ainda")
    registros, data_publicacao, data_referencia = achado
    return _envelope(pacote, registros, data_publicacao, data_referencia, _meta_registro(pacote, registros[0]))


def _experimento_top(repo: Repositorio, data: str | None) -> dict:
    return _resposta_foto("experimento_top", repo.foto("experimento_top", data, None),
                          "pacote experimento_top ainda não publicado")


def _experimento_historico(repo: Repositorio, data: str | None) -> dict:
    return _resposta_foto("experimento_historico", repo.foto("experimento_historico", data, None),
                          "pacote experimento_historico ainda não publicado")


def criar_roteador(config: Config, fabrica) -> APIRouter:
    roteador = APIRouter(prefix="/v1")
    chave_recebida = APIKeyHeader(name="X-API-Key", auto_error=False)

    def exigir_chave(chave: str | None = Security(chave_recebida)) -> None:
        ok = False
        if chave:
            for esperada in config.chaves_api:
                ok = secrets.compare_digest(chave.encode(), esperada.encode()) or ok
        if not ok:
            raise HTTPException(status_code=401, detail="chave de API ausente ou inválida")

    def repositorio():
        with fabrica(config) as repo:
            yield repo

    seguro = [Depends(exigir_chave)]

    @roteador.get("/saude")
    def saude():
        try:
            with fabrica(config) as repo:
                repo.ping()
                pub = repo.ultima_publicacao()
        except banco.ErroBanco:
            return JSONResponse(status_code=503, content={"status": "erro", "banco": "fora"})
        return {
            "status": "ok",
            "banco": "ok",
            "ultima_publicacao": pub["data_publicacao"] if pub else None,
        }

    @roteador.get("/acoes", dependencies=seguro, responses=ERROS)
    def acoes(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        pub = _publicacao(repo, data)
        registros = repo.versoes("acoes", data)
        return _envelope("acoes", registros, pub["data_publicacao"], pub["data_referencia"],
                         _meta_versionada(pub, "acoes"))

    @roteador.get("/resumo_acao/{ticker}", dependencies=seguro, responses=ERROS)
    def resumo_acao(
        ticker: str,
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        tk = ticker.upper()
        return _resposta_foto("resumo_acao", repo.foto("resumo_acao", data, tk),
                              f"ticker {tk} não encontrado")

    @roteador.get("/resumo_acao/{ticker}/historico", dependencies=seguro, responses=ERROS)
    def historico(
        ticker: str,
        campo: str = Query(..., pattern=PADRAO_CAMPO, description="Campo do resumo, ex.: pl ou cotacao.fechamento."),
        inicio: Annotated[str | None, Query(pattern=PADRAO_DATA, description="Primeiro pregão (AAAA-MM-DD)."),
                          AfterValidator(_data_valida)] = None,
        fim: Annotated[str | None, Query(pattern=PADRAO_DATA, description="Último pregão (AAAA-MM-DD)."),
                          AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        tk = ticker.upper()
        linhas = repo.historico(tk, campo, inicio, fim)
        if not linhas:
            raise HTTPException(status_code=404, detail=f"ticker {tk} não encontrado")
        if all(v is None for _, v in linhas):
            raise HTTPException(status_code=404, detail=f"campo {campo} não existe no resumo_acao")
        if any(isinstance(v, dict) and "valor" not in v for _, v in linhas):
            raise HTTPException(status_code=422,
                                detail=f"campo {campo} não é um indicador (ex.: use cotacao.fechamento)")
        pontos = []
        for data_ref, v in linhas:
            if isinstance(v, dict):
                valor = v.get("valor")
                st = v.get("status", "ok" if valor is not None else "indisponivel")
            else:
                # dia em que o campo não existia no resumo: ausência = null + status, nunca "ok"
                valor, st = v, ("ok" if v is not None else "indisponivel")
            pontos.append(series.Ponto(
                data=data_ref,
                valores={campo: valor},
                status={campo: st} if st != "ok" else {},
            ))
        datas, valores, status = series.pontos_para_compacto(pontos, [campo])
        pub = repo.ultima_publicacao()
        return {
            "ticker": tk,
            "campo": campo,
            "datas": datas,
            "valores": valores[campo],
            "status": status.get(campo, []),
            "data_publicacao": pub["data_publicacao"] if pub else None,
            "data_referencia": datas[-1] if datas else None,
        }

    @roteador.get("/series_diarias/{ticker}", dependencies=seguro, responses=ERROS)
    def series_diarias(
        ticker: str,
        janela: Literal["1m", "3m", "6m", "1a", "2a", "max"] = Query("1a", description=DESC_JANELA),
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        tk = ticker.upper()
        pub = _publicacao(repo, data)
        achado = repo.serie(tk, pub["data_referencia"])
        if achado is None or not achado[1]:
            raise HTTPException(status_code=404, detail=f"ticker {tk} não encontrado")
        catalogo, pontos, recusas = achado
        meta = _meta_versionada(pub, "series_diarias")
        fim = pub["data_referencia"]
        inicio_completa = (pub["metadados"].get("series_diarias") or {}).get("inicio_janela")
        if janela == "1a" and inicio_completa:
            inicio_janela = inicio_completa
        else:
            inicio_janela = repo.inicio_da_janela(fim, series.JANELAS[janela])
        inicio_ultima_completa = None
        if janela in ("2a", "max"):
            inicio_ultima_completa = inicio_completa or repo.inicio_da_janela(fim, series.PREGOES_COMPLETA)
        try:
            if inicio_janela is None:
                raise ValueError("calendário sem pregões até a data")
            registro = series.montar_registro(meta, catalogo, pontos, janela, recusas,
                                              inicio_janela=inicio_janela,
                                              inicio_ultima_completa=inicio_ultima_completa)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"ticker {tk} sem pontos na janela {janela}") from None
        return _envelope("series_diarias", [registro], pub["data_publicacao"], pub["data_referencia"], meta)

    @roteador.get("/fundamentos_trimestrais/{ticker}", dependencies=seguro, responses=ERROS)
    def fundamentos_trimestrais(
        ticker: str,
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        tk = ticker.upper()
        pub = _publicacao(repo, data)
        if not repo.ticker_existe(tk):
            raise HTTPException(status_code=404, detail=f"ticker {tk} não encontrado")
        registros = repo.versoes("fundamentos_trimestrais", data, tk)
        return _envelope("fundamentos_trimestrais", registros, pub["data_publicacao"], pub["data_referencia"],
                         _meta_versionada(pub, "fundamentos_trimestrais"))

    @roteador.get("/proventos/{ticker}", dependencies=seguro, responses=ERROS)
    def proventos(
        ticker: str,
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        tk = ticker.upper()
        pub = _publicacao(repo, data)
        if not repo.ticker_existe(tk):
            raise HTTPException(status_code=404, detail=f"ticker {tk} não encontrado")
        registros = repo.versoes("proventos", data, tk)
        return _envelope("proventos", registros, pub["data_publicacao"], pub["data_referencia"],
                         _meta_versionada(pub, "proventos"))

    @roteador.get("/noticias_dia/{ticker}", dependencies=seguro, responses=ERROS)
    def noticias_dia(
        ticker: str,
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        tk = ticker.upper()
        return _resposta_foto("noticias_dia", repo.foto("noticias_dia", data, tk),
                              f"ticker {tk} não encontrado")

    @roteador.get("/mercado_dia", dependencies=seguro, responses=ERROS)
    def mercado_dia(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        return _resposta_foto("mercado_dia", repo.foto("mercado_dia", data, None), "nada publicado")

    @roteador.get("/resumo_acao", dependencies=seguro, responses=ERROS)
    def resumo_acao_do_dia(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        return _resposta_dia("resumo_acao", repo.fotos_do_dia("resumo_acao", data), data)

    @roteador.get("/noticias_dia", dependencies=seguro, responses=ERROS)
    def noticias_do_dia(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        return _resposta_dia("noticias_dia", repo.fotos_do_dia("noticias_dia", data), data)

    @roteador.get("/experimento/top", dependencies=seguro, responses=ERROS)
    def experimento_top(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        return _experimento_top(repo, data)

    @roteador.get("/experimento/historico", dependencies=seguro, responses=ERROS)
    def experimento_historico(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        return _experimento_historico(repo, data)

    # Nome antigo, mantido para quem já usa: mesma resposta, mas fora do /docs.
    @roteador.get("/experimento_top", dependencies=seguro, include_in_schema=False)
    def experimento_top_antigo(
        data: Annotated[str | None, Query(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)] = None,
        repo: Repositorio = Depends(repositorio),
    ):
        return _experimento_top(repo, data)

    # A rota fixa /ultima vem antes de /{data}, senão "ultima" cairia no parâmetro.
    @roteador.get("/execucao/ultima", dependencies=seguro, responses=ERROS)
    def execucao_ultima(repo: Repositorio = Depends(repositorio)):
        achado = repo.execucao_ultima()
        if achado is None:
            raise HTTPException(status_code=404, detail="nenhuma execução gravada")
        registro, data_publicacao = achado
        return _envelope("execucao", [registro], data_publicacao, registro["data_referencia"],
                         _meta_registro("execucao", registro))

    @roteador.get("/execucao/{data}", dependencies=seguro, responses=ERROS)
    def execucao_do_dia(
        data: Annotated[str, Path(pattern=PADRAO_DATA, description=DESC_DATA), AfterValidator(_data_valida)],
        repo: Repositorio = Depends(repositorio),
    ):
        achados = repo.execucoes(data)
        if not achados:
            raise HTTPException(status_code=404, detail=f"nenhuma execução em {data}")
        registros = [registro for registro, _ in achados]
        data_publicacao = max(pub for _, pub in achados)
        return _envelope("execucao", registros, data_publicacao, data, _meta_registro("execucao", registros[0]))

    return roteador
