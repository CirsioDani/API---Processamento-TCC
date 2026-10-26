"""Planejador da publicação: compara a Entrada com o Estado e decide as operações (lógica pura, sem banco).

Contrato: docs/contratos_internos.md, seção "publicador/planejador.py (lógica pura)"; regras em
docs/plano_api.md, seções "c) Idempotência e preencher dia que falta" e "Séries: emenda dos pontos antigos".
"""
from comum import contrato, series
from publicador import emenda
from publicador.entrada import Entrada
from publicador.operacoes import (
    AjusteRegistrar,
    CatalogoGravar,
    Estado,
    FotoAtualizar,
    FotoInserir,
    Plano,
    PontoAtualizar,
    PontoInserir,
    PontoReabrir,
    PontoRemover,
    Versao,
    VersaoAntecipar,
    VersaoCorrigir,
    VersaoFechar,
    VersaoInserir,
    VersaoReabrir,
)

# Trava de segurança dos versionados: a completa não pode trazer mais de 20% menos registros que as versões abertas.
FRACAO_MINIMA_TRAVA = 0.8


class ErroPublicacao(Exception):
    """Publicação recusada pelo planejador (ex.: trava de segurança dos versionados)."""


def planejar(entrada: Entrada, estado: Estado, forcar: bool = False) -> Plano:
    d = entrada.data
    if entrada.completa is None:
        plano = Plano(
            data=d, modo="so_execucao", status_publicacao="so_execucao", data_referencia=None,
            metadados=_metadados(None, entrada.execucao),
        )
    else:
        plano = Plano(
            data=d, modo=_modo(d, estado), status_publicacao="completa",
            data_referencia=_data_referencia(entrada.completa),
            metadados=_metadados(entrada.completa, entrada.execucao),
        )
    registros_execucao = entrada.execucao.get("registros") or []
    if registros_execucao:
        plano.execucao_status = registros_execucao[-1].get("status")
    _execucao(entrada.execucao, estado, plano)
    if entrada.completa is not None:
        _fotos(entrada.completa, estado, plano)
        _series(entrada.completa, estado, plano)
        _versionadas(entrada.completa, estado, plano, forcar)
    return plano


def aplicar_em_memoria(estado: Estado, plano: Plano) -> None:
    """Aplica as operações do plano no Estado, como o gravador faria no banco (para testes de vários dias)."""
    for op in plano.operacoes:
        if isinstance(op, (FotoInserir, FotoAtualizar)):
            estado.fotos.setdefault(op.pacote, {})[tuple(op.chave.values())] = (op.hash, op.registro)
        elif isinstance(op, (PontoInserir, PontoAtualizar)):
            estado.pontos.setdefault(op.ticker, {})[op.ponto.data] = op.ponto
        elif isinstance(op, PontoRemover):
            removido = estado.pontos.get(op.ticker, {}).pop(op.data, None)
            if removido is not None:
                estado.pontos_removidos.setdefault(op.ticker, {})[op.data] = removido
        elif isinstance(op, PontoReabrir):
            estado.pontos_removidos.get(op.ticker, {}).pop(op.ponto.data, None)
            estado.pontos.setdefault(op.ticker, {})[op.ponto.data] = op.ponto
        elif isinstance(op, CatalogoGravar):
            estado.catalogo[op.ticker] = {"tipo": op.tipo, "nome": op.nome, "campos": list(op.campos)}
        elif isinstance(op, AjusteRegistrar):
            if op.ajuste.classificacao == "recusado":
                estado.recusas.setdefault(op.ticker, {})[op.ajuste.campo] = op.ajuste.ate
        elif isinstance(op, VersaoInserir):
            chave_t = tuple(op.chave.values())
            lista = estado.versoes.setdefault(op.pacote, {}).setdefault(chave_t, [])
            lista.append(Versao(_proximo_id(estado, op.pacote), chave_t, op.valido_desde, op.valido_ate,
                                op.hash, op.registro))
            lista.sort(key=lambda v: v.valido_desde)
        elif isinstance(op, VersaoFechar):
            _versao(estado, op.pacote, op.chave, op.id).valido_ate = op.valido_ate
        elif isinstance(op, VersaoCorrigir):
            versao = _versao(estado, op.pacote, op.chave, op.id)
            versao.hash = op.hash
            versao.registro = op.registro
        elif isinstance(op, VersaoReabrir):
            _versao(estado, op.pacote, op.chave, op.id).valido_ate = None
        elif isinstance(op, VersaoAntecipar):
            versao = _versao(estado, op.pacote, op.chave, op.id)
            versao.valido_desde = op.valido_desde
            estado.versoes[op.pacote][tuple(op.chave.values())].sort(key=lambda v: v.valido_desde)

    # "completa" nunca é rebaixado por uma publicação só da execução ou rejeitada.
    if estado.publicacoes.get(plano.data) != "completa":
        estado.publicacoes[plano.data] = plano.status_publicacao


# ---------------------------------------------------------------- modo, metadados e helpers

def _modo(d: str, estado: Estado) -> str:
    ultima = _ultima_completa(estado)
    if ultima is None or d > ultima:
        return "normal"
    if d == ultima:
        return "republicacao"
    return "buraco"


def _ultima_completa(estado: Estado) -> str | None:
    return max((d for d, s in estado.publicacoes.items() if s == "completa"), default=None)


def _proxima_completa(estado: Estado, d: str) -> str | None:
    proximas = sorted(x for x, s in estado.publicacoes.items() if s == "completa" and x > d)
    return proximas[0] if proximas else None


def _data_referencia(completa: dict) -> str | None:
    for pacote in ("mercado_dia", "resumo_acao", "noticias_dia"):
        registros = completa.get(pacote, {}).get("registros") or []
        if registros:
            return registros[0]["data_referencia"]
    # sem os pacotes diários: o pregão é a última data das séries (senão a API não acharia os pontos)
    ultimas = [s["datas"][-1] for s in completa.get("series_diarias", {}).get("registros") or [] if s.get("datas")]
    return max(ultimas) if ultimas else None


def _metadados(completa: dict | None, execucao: dict) -> dict:
    meta = {"execucao": _meta_envelope(execucao)}
    for pacote, envelope in (completa or {}).items():
        meta[pacote] = _meta_envelope(envelope)
    if completa and "series_diarias" in completa:
        meta["series_diarias"]["inicio_janela"] = _inicio_janela_series(completa["series_diarias"])
    return meta


def _inicio_janela_series(envelope: dict) -> str | None:
    """Menor inicio_janela das séries da completa (todas têm o mesmo na prática); None sem séries."""
    inicios = [r["inicio_janela"] for r in envelope.get("registros") or [] if r.get("inicio_janela")]
    return min(inicios) if inicios else None


def _meta_envelope(envelope: dict) -> dict:
    return {
        "versao_contrato": envelope["versao_contrato"],
        "chave": envelope["chave"],
        "gerado_em": envelope["gerado_em"],
        "dados_ate": envelope["dados_ate"],
    }


def _dict_chave(pacote: str, chave_t: tuple) -> dict:
    return dict(zip(contrato.PACOTES[pacote].chave, chave_t))


def _chave_tupla(pacote: str, campos: dict) -> tuple:
    return tuple(campos[c] for c in contrato.PACOTES[pacote].chave)


def _proximo_id(estado: Estado, pacote: str) -> int:
    ids = [v.id for lista in estado.versoes.get(pacote, {}).values() for v in lista if v.id is not None]
    return max(ids) + 1 if ids else 1


def _versao(estado: Estado, pacote: str, chave: dict, id_: int) -> Versao:
    for versao in estado.versoes[pacote][tuple(chave.values())]:
        if versao.id == id_:
            return versao
    raise KeyError(f"versão {id_} de {pacote} não encontrada no estado")


# ---------------------------------------------------------------- execução e fotos

def _execucao(execucao: dict, estado: Estado, plano: Plano) -> None:
    for registro in execucao["registros"]:
        _foto("execucao", registro, estado, plano, buraco=False)


def _fotos(completa: dict, estado: Estado, plano: Plano) -> None:
    buraco = plano.modo == "buraco"
    for pacote in contrato.FOTOS_DA_COMPLETA:
        if pacote not in completa:
            continue
        diferentes = sum(_foto(pacote, r, estado, plano, buraco) for r in completa[pacote]["registros"])
        if diferentes:
            plano.avisos.append(
                f"{pacote}: {diferentes} registro(s) diferente(s) do já gravado (modo buraco: não alterados)"
            )


def _foto(pacote: str, registro: dict, estado: Estado, plano: Plano, buraco: bool) -> int:
    """Devolve 1 quando, no modo buraco, o registro difere do gravado (vira aviso); senão 0."""
    chave_t = contrato.chave_de(pacote, registro)
    h = contrato.hash_registro(registro)
    atual = estado.fotos.get(pacote, {}).get(chave_t)
    if atual is None:
        plano.operacoes.append(FotoInserir(pacote, _dict_chave(pacote, chave_t), h, registro))
        return 0
    if atual[0] == h:
        return 0
    if buraco:
        return 1
    plano.operacoes.append(FotoAtualizar(pacote, _dict_chave(pacote, chave_t), h, registro, atual[0], atual[1]))
    return 0


# ---------------------------------------------------------------- séries

def _series(completa: dict, estado: Estado, plano: Plano) -> None:
    if "series_diarias" not in completa:
        return
    buraco = plano.modo == "buraco"
    diferentes = 0
    for serie in completa["series_diarias"]["registros"]:
        if buraco:
            diferentes += _serie_buraco(serie, estado, plano)
        else:
            _serie_normal(serie, estado, plano)
    if diferentes:
        plano.avisos.append(
            f"series_diarias: {diferentes} ponto(s) diferente(s) do já gravado (modo buraco: não alterados)"
        )


def _serie_normal(serie: dict, estado: Estado, plano: Plano) -> None:
    ticker = serie["ticker"]
    plano.operacoes.append(CatalogoGravar(ticker, serie["tipo"], serie["nome"], list(serie["valores"].keys())))
    novos = series.compacto_para_pontos(serie)
    antigos = estado.pontos.get(ticker, {})
    res = emenda.analisar(ticker, serie["tipo"], antigos, novos)
    d0, ultima = novos[0].data, novos[-1].data
    datas_novas = {p.data for p in novos}
    removidos = estado.pontos_removidos.get(ticker, {})

    for novo in novos:
        antigo = antigos.get(novo.data)
        if antigo is None:
            removido = removidos.get(novo.data)
            if removido is not None:
                # A linha removida ainda ocupa a chave (ticker, data): reabre em vez de inserir.
                plano.operacoes.append(PontoReabrir(ticker, novo, series.hash_ponto(novo), removido,
                                                    series.hash_ponto(removido)))
            else:
                plano.operacoes.append(PontoInserir(ticker, novo, series.hash_ponto(novo)))
            continue
        h_novo, h_antigo = series.hash_ponto(novo), series.hash_ponto(antigo)
        if h_novo != h_antigo:
            anterior = _anterior(antigo, novo, res.datas_explicadas)
            ruido = anterior is not None and _so_ruido(antigo, novo, novo.data, res.datas_explicadas)
            if ruido:
                anterior = None
            plano.operacoes.append(PontoAtualizar(ticker, novo, h_novo, anterior, h_antigo, ruido))

    for data in sorted(antigos):
        antigo = antigos[data]
        if d0 <= data <= ultima and data not in datas_novas:
            plano.operacoes.append(PontoRemover(ticker, data, antigo, series.hash_ponto(antigo)))
        elif data < d0 and res.fatores_aplicar:
            emendado = _ponto_emendado(antigo, res.fatores_aplicar)
            h_antigo = series.hash_ponto(antigo)
            if series.hash_ponto(emendado) != h_antigo:
                plano.operacoes.append(
                    PontoAtualizar(ticker, emendado, series.hash_ponto(emendado), None, h_antigo)
                )

    for ajuste in res.ajustes:
        plano.operacoes.append(AjusteRegistrar(ticker, ajuste))
    plano.avisos.extend(res.avisos)


def _serie_buraco(serie: dict, estado: Estado, plano: Plano) -> int:
    """Só insere os pontos que faltam; devolve quantos pontos existentes diferem (viram aviso)."""
    ticker = serie["ticker"]
    plano.operacoes.append(CatalogoGravar(ticker, serie["tipo"], serie["nome"], list(serie["valores"].keys())))
    antigos = estado.pontos.get(ticker, {})
    removidos = estado.pontos_removidos.get(ticker, {})
    diferentes = 0
    for novo in series.compacto_para_pontos(serie):
        antigo = antigos.get(novo.data)
        if antigo is None and novo.data in removidos:
            continue  # removido por uma D posterior: o modo buraco não desfaz a remoção
        if antigo is None:
            plano.operacoes.append(PontoInserir(ticker, novo, series.hash_ponto(novo)))
        elif series.hash_ponto(antigo) != series.hash_ponto(novo):
            diferentes += 1
    return diferentes


def _anterior(antigo: series.Ponto, novo: series.Ponto, explicadas: dict) -> series.Ponto | None:
    """None quando todo campo que mudou (valor ou status) é explicado pelo fator da emenda; senão o antigo."""
    mudados = {c for c in set(antigo.valores) | set(novo.valores) if antigo.valores.get(c) != novo.valores.get(c)}
    mudados |= {c for c in set(antigo.status) | set(novo.status) if antigo.status.get(c) != novo.status.get(c)}
    if all(novo.data in explicadas.get(c, set()) for c in mudados):
        return None
    return antigo


def _so_ruido(antigo: series.Ponto, novo: series.Ponto, data: str, explicadas: dict) -> bool:
    """True quando o status é o mesmo e todo valor que mudou (e não é explicado pela emenda) difere no máximo
    emenda.TOLERANCIA em termos relativos: ruído numérico do processamento, não correção."""
    if antigo.status != novo.status:
        return False
    for campo in set(antigo.valores) | set(novo.valores):
        a, b = antigo.valores.get(campo), novo.valores.get(campo)
        if a == b or data in explicadas.get(campo, set()):
            continue
        if not (_numero(a) and _numero(b)) or abs(a - b) > emenda.TOLERANCIA * abs(b):
            return False
    return True


def _numero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _ponto_emendado(antigo: series.Ponto, fatores: dict) -> series.Ponto:
    valores = dict(antigo.valores)
    for campo, k in fatores.items():
        if valores.get(campo) is not None:
            valores[campo] = valores[campo] * k
    return series.Ponto(data=antigo.data, valores=valores, status=dict(antigo.status))


# ---------------------------------------------------------------- versionadas

def _versionadas(completa: dict, estado: Estado, plano: Plano, forcar: bool) -> None:
    for pacote in contrato.VERSIONADOS:
        if pacote not in completa:
            continue
        if plano.modo == "buraco":
            _versionada_buraco(pacote, completa[pacote]["registros"], estado, plano)
        else:
            _versionada_normal(pacote, completa[pacote], estado, plano, forcar)


def _versionada_normal(pacote: str, envelope: dict, estado: Estado, plano: Plano, forcar: bool) -> None:
    d = plano.data
    registros = envelope["registros"]
    versoes = estado.versoes.get(pacote, {})
    abertas = {chave: v for chave, lista in versoes.items() for v in lista if v.valido_ate is None}
    if abertas and not forcar and len(registros) < FRACAO_MINIMA_TRAVA * len(abertas):
        raise ErroPublicacao(
            f"{pacote}: a completa traz {len(registros)} registros, mas há {len(abertas)} versões abertas "
            "(queda acima de 20%). Se a queda for real, rode de novo com --forcar."
        )

    removidos = {_chave_tupla(pacote, r) for r in envelope.get("removidos", [])}
    presentes = set()
    for registro in registros:
        chave_t = contrato.chave_de(pacote, registro)
        if chave_t in removidos:
            continue
        presentes.add(chave_t)
        _versao_normal(pacote, chave_t, registro, versoes, plano, d)

    for chave_t, aberta in abertas.items():
        if chave_t not in presentes:
            plano.operacoes.append(VersaoFechar(pacote, aberta.id, _dict_chave(pacote, chave_t), d))


def _versao_normal(pacote: str, chave_t: tuple, registro: dict, versoes: dict, plano: Plano, d: str) -> None:
    chave = _dict_chave(pacote, chave_t)
    h = contrato.hash_registro(registro)
    lista = versoes.get(chave_t, [])
    aberta = next((v for v in lista if v.valido_ate is None), None)

    if aberta is None:
        ultima = lista[-1] if lista else None
        if ultima is not None and ultima.valido_ate == d:
            if ultima.hash == h:
                plano.operacoes.append(VersaoReabrir(pacote, ultima.id, chave))
                return
            if ultima.valido_desde == d:
                plano.operacoes.append(VersaoCorrigir(pacote, ultima.id, chave, h, registro,
                                                      ultima.hash, ultima.registro))
                plano.operacoes.append(VersaoReabrir(pacote, ultima.id, chave))
                return
        plano.operacoes.append(VersaoInserir(pacote, chave, d, None, h, registro))
        return

    if aberta.hash == h:
        return
    if aberta.valido_desde == d:
        plano.operacoes.append(VersaoCorrigir(pacote, aberta.id, chave, h, registro, aberta.hash, aberta.registro))
        return
    plano.operacoes.append(VersaoFechar(pacote, aberta.id, chave, d))
    plano.operacoes.append(VersaoInserir(pacote, chave, d, None, h, registro))


def _versionada_buraco(pacote: str, registros: list, estado: Estado, plano: Plano) -> None:
    d = plano.data
    versoes = estado.versoes.get(pacote, {})
    proxima = _proxima_completa(estado, d)
    for registro in registros:
        chave_t = contrato.chave_de(pacote, registro)
        chave = _dict_chave(pacote, chave_t)
        h = contrato.hash_registro(registro)
        lista = versoes.get(chave_t)
        if not lista:
            plano.operacoes.append(VersaoInserir(pacote, chave, d, proxima, h, registro))
            continue
        primeira = lista[0]
        if d >= primeira.valido_desde:
            continue
        if primeira.hash == h:
            plano.operacoes.append(VersaoAntecipar(pacote, primeira.id, chave, d))
        else:
            plano.operacoes.append(VersaoInserir(pacote, chave, d, primeira.valido_desde, h, registro))
