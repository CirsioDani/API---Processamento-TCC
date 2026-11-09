"""Emenda dos pontos antigos de uma série (lógica pura, sem I/O).

Compara a completa nova com os pontos já guardados e decide se a diferença é uma
correção de histórico (desdobramento, mudança de âncora, composição de setor) que
precisa ser emendada nos pontos antigos. Contrato: docs/contratos_internos.md,
seção "publicador/emenda.py (lógica pura)"; regra completa em docs/plano_api.md,
seção "Séries: emenda dos pontos antigos".

setor_sem_empresa encadeia no 1º ponto da sobreposição e nunca recusa; fator fora de [0,5; 2,0] gera aviso ATENÇÃO.
"""
from dataclasses import dataclass

from comum.series import Ponto

TOLERANCIA = 1e-6
FATOR_MIN = 0.01
FATOR_MAX = 100.0
PONTOS_EMENDA = 5
BUSCA_EMENDA = 21
MINIMO_PONTOS = 3

CAMPOS_PROPORCIONAIS = {
    "acao": ("fechamento", "volume", "indice_retorno_total"),
    "etf": ("fechamento", "volume", "indice_retorno_total"),
    "indice": ("indice_retorno_total",),
    "taxa": ("indice_retorno_total",),
    "setor_sem_empresa": ("indice_retorno_total",),
}
CAMPOS_SO_AVISO = {"indice": ("fechamento",)}

ORIGEM_EMENDA = "emenda"
ORIGEM_JANELA = "janela"
MOTIVO_SO_AVISO = "campo oficial: só aviso"

TIPOS_ENCADEADOS = ("setor_sem_empresa",)
FATOR_ALERTA_MIN = 0.5
FATOR_ALERTA_MAX = 2.0
MOTIVO_ENCADEADO = "setor_sem_empresa: histórico substituído; antigos encadeados no 1º ponto da sobreposição"
MOTIVO_SEM_COSTURA = "setor_sem_empresa: sem ponto na sobreposição para encadear"


@dataclass(frozen=True)
class Ajuste:
    campo: str
    fator: float | None
    de: str | None
    ate: str | None
    pontos: int
    origem: str          # "emenda" | "janela"
    classificacao: str   # "uniforme" | "evento" | "irregular" | "recusado"
    aplicado: bool
    detalhe: dict


@dataclass
class ResultadoEmenda:
    ajustes: list[Ajuste]
    fatores_aplicar: dict[str, float]        # campo -> k a multiplicar nos antigos (data < data_corte)
    data_corte: str                          # d0
    datas_explicadas: dict[str, set[str]]    # campo -> datas da janela cuja mudança nesse campo o fator explica
    avisos: list[str]


def analisar(ticker: str, tipo: str, antigos: dict[str, Ponto], novos: list[Ponto]) -> ResultadoEmenda:
    proporcionais = CAMPOS_PROPORCIONAIS.get(tipo, ())
    so_aviso = CAMPOS_SO_AVISO.get(tipo, ())
    resultado = ResultadoEmenda(
        ajustes=[],
        fatores_aplicar={},
        data_corte="",
        datas_explicadas={campo: set() for campo in proporcionais},
        avisos=[],
    )
    if not novos:
        return resultado

    d0 = novos[0].data
    resultado.data_corte = d0
    novos_por_data = {p.data: p for p in novos}
    sobreposicao = [p.data for p in novos if p.data in antigos]
    janela_busca = set(sobreposicao[:BUSCA_EMENDA])

    for campo in proporcionais + so_aviso:
        razoes = _razoes(campo, sobreposicao, antigos, novos_por_data)
        if all(_iguais(r, 1.0) for _, r in razoes):
            continue
        antigas = _antigos_com_valor(campo, antigos, d0)
        costura = [(d, r) for d, r in razoes if d in janela_busca][:PONTOS_EMENDA]
        proporcional = campo in proporcionais

        if proporcional:
            _analisar_proporcional(ticker, campo, razoes, costura, antigas, resultado,
                                   encadear=tipo in TIPOS_ENCADEADOS)
        else:
            lacuna = _lacuna_preenchida(campo, sobreposicao, antigos, novos_por_data)
            _analisar_so_aviso(ticker, campo, razoes, costura, antigas, resultado, lacuna)

    return resultado


def _analisar_proporcional(ticker, campo, razoes, costura, antigas, resultado, encadear=False):
    k, motivo = _fator_da_costura(costura)
    encadeado = False
    if encadear and motivo is not None:
        if not costura or not costura[0][1] > 0:
            if antigas:
                resultado.ajustes.append(Ajuste(
                    campo=campo, fator=None, de=antigas[0], ate=antigas[-1], pontos=len(antigas),
                    origem=ORIGEM_EMENDA, classificacao="irregular", aplicado=False,
                    detalhe={"motivo": MOTIVO_SEM_COSTURA, "antigos": _faixa(antigas), "razoes_costura": []},
                ))
                resultado.avisos.append(f"série {ticker}, campo {campo}: sem ponto para encadear — antigos mantidos como publicados")
            return
        k, motivo, encadeado = costura[0][1], None, True
    if motivo is None and not encadear and not (FATOR_MIN <= k <= FATOR_MAX):
        motivo = f"fator fora de [{FATOR_MIN}, {FATOR_MAX}]"
    if motivo is not None:
        if antigas:
            resultado.ajustes.append(Ajuste(
                campo=campo, fator=k, de=antigas[0], ate=antigas[-1], pontos=len(antigas),
                origem=ORIGEM_EMENDA, classificacao="recusado", aplicado=False,
                detalhe={"motivo": motivo, "razoes_costura": _razoes_dict(costura)},
            ))
            resultado.avisos.append(
                f"série {ticker}, campo {campo}: emenda recusada (fator {_fmt_fator(k)}) "
                "— pontos antigos ficam indisponíveis na API"
            )
        return
    if _iguais(k, 1.0):
        return  # mudança só no meio da janela: vai ponto a ponto, nada é explicado

    classificacao, explicadas, data_evento = _classificar(razoes, k)
    if not antigas and not explicadas:
        return

    resultado.datas_explicadas[campo] = set(explicadas)
    if antigas:
        resultado.fatores_aplicar[campo] = k
    detalhe = {
        "antigos": _faixa(antigas),
        "janela": _faixa(explicadas),
        "razoes_costura": _razoes_dict(costura),
    }
    if data_evento is not None:
        detalhe["data_evento"] = data_evento
    if encadeado:
        detalhe["motivo"] = MOTIVO_ENCADEADO
    resultado.ajustes.append(Ajuste(
        campo=campo,
        fator=k,
        de=antigas[0] if antigas else explicadas[0],
        ate=explicadas[-1] if explicadas else antigas[-1],
        pontos=len(antigas) + len(explicadas),
        origem=ORIGEM_EMENDA if antigas else ORIGEM_JANELA,
        classificacao=classificacao,
        aplicado=True,
        detalhe=detalhe,
    ))
    if encadear:
        _avisos_setor(ticker, campo, k, encadeado, resultado)


def _avisos_setor(ticker, campo, k, encadeado, resultado):
    """Avisos de setor_sem_empresa para um fator aplicado: nunca recusa, só avisa."""
    if encadeado:
        resultado.avisos.append(
            f"série {ticker}, campo {campo}: histórico substituído (fator de encadeamento {_fmt_fator(k)}) "
            "— antigos encadeados no 1º ponto da janela"
        )
    if not (FATOR_ALERTA_MIN <= k <= FATOR_ALERTA_MAX):
        resultado.avisos.append(
            f"ATENÇÃO: série {ticker}, campo {campo}: fator de encadeamento {_fmt_fator(k)} "
            "fora de [0,5; 2,0] — conferir o processamento (aplicado mesmo assim)"
        )


def _analisar_so_aviso(ticker, campo, razoes, costura, antigas, resultado, lacuna=0):
    k, motivo = _fator_da_costura(costura)
    data_evento = None
    if motivo is not None:
        classificacao = "irregular"
    elif _iguais(k, 1.0):
        return
    else:
        classificacao, _, data_evento = _classificar(razoes, k)

    datas_afetadas = antigas or [d for d, _ in razoes]
    detalhe = {
        "motivo": MOTIVO_SO_AVISO,
        "antigos": _faixa(antigas),
        "razoes_costura": _razoes_dict(costura),
    }
    if data_evento is not None:
        detalhe["data_evento"] = data_evento
    # Sem fator porque o antigo era null onde o novo tem número: é uma lacuna preenchida, não mudança do valor oficial.
    so_lacuna = k is None and lacuna > 0
    if so_lacuna:
        detalhe["lacuna_preenchida"] = lacuna
    resultado.ajustes.append(Ajuste(
        campo=campo,
        fator=k,
        de=datas_afetadas[0],
        ate=datas_afetadas[-1],
        pontos=len(datas_afetadas),
        origem=ORIGEM_EMENDA if antigas else ORIGEM_JANELA,
        classificacao=classificacao,
        aplicado=False,
        detalhe=detalhe,
    ))
    if so_lacuna:
        diferentes = sum(1 for _, r in razoes if not _iguais(r, 1.0))
        detalhe["pontos_diferentes"] = diferentes
        mudanca = f"lacuna preenchida ({lacuna} pontos)"
        if diferentes:
            mudanca += f"; {diferentes} ponto(s) já existente(s) com valor diferente"
    else:
        mudanca = f"campo oficial mudou (fator {_fmt_fator(k)})"
    resultado.avisos.append(f"série {ticker}, campo {campo}: {mudanca} — só aviso, nada foi ajustado")


def _fator_da_costura(costura):
    """Devolve (k, motivo). motivo não é None quando a costura não serve para um fator."""
    if len(costura) < MINIMO_PONTOS:
        return None, f"poucos pontos para estimar o fator ({len(costura)} de {MINIMO_PONTOS})"
    k = costura[0][1]
    if not all(_iguais(r, k) for _, r in costura):
        # Um ponto corrigido no começo da janela não é mudança de histórico: se a maioria da costura
        # continua igual (fator 1), a mudança vai ponto a ponto, sem emenda e sem recusa.
        if sum(_iguais(r, 1.0) for _, r in costura) >= MINIMO_PONTOS:
            return 1.0, None
        return k, "razões da costura não concordam"
    return k, None


def _classificar(razoes, k):
    """Devolve (classificacao, datas explicadas pelo fator, data do evento ou None)."""
    if all(_iguais(r, k) for _, r in razoes):
        return "uniforme", [d for d, _ in razoes], None
    s = _indice_evento(razoes, k)
    if s is not None:
        return "evento", [d for d, _ in razoes[:s]], razoes[s][0]
    return "irregular", [], None


def _indice_evento(razoes, k):
    for s in range(1, len(razoes)):
        if all(_iguais(r, k) for _, r in razoes[:s]) and all(_iguais(r, 1.0) for _, r in razoes[s:]):
            return s
    return None


def _razoes(campo, sobreposicao, antigos, novos_por_data):
    """Razões (data, novo ÷ antigo) na sobreposição, só onde os dois valores existem e antigo ≠ 0."""
    saida = []
    for data in sobreposicao:
        novo = novos_por_data[data].valores.get(campo)
        antigo = antigos[data].valores.get(campo)
        if novo is None or antigo is None or antigo == 0:
            continue
        saida.append((data, novo / antigo))
    return saida


def _lacuna_preenchida(campo, sobreposicao, antigos, novos_por_data):
    """Quantas datas da sobreposição tinham o campo null no antigo e agora têm número."""
    return sum(
        1 for data in sobreposicao
        if antigos[data].valores.get(campo) is None and novos_por_data[data].valores.get(campo) is not None
    )


def _antigos_com_valor(campo, antigos, d0):
    return [d for d in sorted(antigos) if d < d0 and antigos[d].valores.get(campo) is not None]


def _iguais(a, b):
    return abs(a - b) <= TOLERANCIA * abs(b)


def _faixa(datas):
    return {
        "de": datas[0] if datas else None,
        "ate": datas[-1] if datas else None,
        "pontos": len(datas),
    }


def _razoes_dict(costura):
    return [{"data": d, "razao": r} for d, r in costura]


def _fmt_fator(k):
    return "indefinido" if k is None else f"{k:.8g}"
