"""Valida a Entrada de uma data D contra o contrato 1.0. Não para no primeiro erro, mas limita a 200 mensagens."""
import re
from datetime import date

from comum import contrato
from publicador.entrada import Entrada

MAXIMO_ERROS = 200
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_SERIE = "series_diarias"
_PACOTES_REFERENCIA = ("resumo_acao", "mercado_dia", "noticias_dia")


class _Erros:
    def __init__(self) -> None:
        self.lista: list[str] = []

    def add(self, mensagem: str) -> None:
        if len(self.lista) < MAXIMO_ERROS:
            self.lista.append(mensagem)


def _data_iso(valor) -> bool:
    if not isinstance(valor, str) or _DATA.fullmatch(valor) is None:
        return False
    try:
        date.fromisoformat(valor)
    except ValueError:
        return False
    return True


def _numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _rotulo(nome: str, registro, campos: tuple[str, ...], indice: int) -> str:
    """'nome[v1|v2]' com os valores da chave; 'nome[registro N]' se a chave não pode ser montada."""
    if isinstance(registro, dict) and all(registro.get(c) is not None for c in campos):
        return f"{nome}[{'|'.join(str(registro[c]) for c in campos)}]"
    return f"{nome}[registro {indice}]"


def _envelope(rotulo: str, env: dict, pacote: str, erros: _Erros) -> None:
    for campo in contrato.CAMPOS_ENVELOPE:
        if campo not in env:
            erros.add(f"{rotulo}: campo de envelope ausente '{campo}'")
    if "pacote" in env and env["pacote"] != pacote:
        erros.add(f"{rotulo}.pacote: {env['pacote']!r} (esperado {pacote!r})")
    if "versao_contrato" in env and env["versao_contrato"] != contrato.VERSAO:
        erros.add(f"{rotulo}.versao_contrato: {env['versao_contrato']!r} (esperado {contrato.VERSAO!r})")
    esperada = list(contrato.PACOTES[pacote].chave)
    if "chave" in env and env["chave"] != esperada:
        erros.add(f"{rotulo}.chave: {env['chave']!r} (esperado {esperada!r})")
    for campo in ("registros", "removidos"):
        if campo in env and not isinstance(env[campo], list):
            erros.add(f"{rotulo}.{campo}: não é lista")


def _folha(valor, status, onde: str, extras: frozenset, erros: _Erros) -> None:
    if not isinstance(status, str) or status not in (contrato.STATUS_VALIDOS | extras):
        erros.add(f"{onde}: status {status!r} inválido")
    elif status == "ok" and valor is None:
        erros.add(f"{onde}: status 'ok' com valor nulo")
    elif valor is not None and status not in ({"ok", "lucro_negativo"} | extras):
        erros.add(f"{onde}: valor {valor!r} com status {status!r} (só valor nulo)")


def _folhas(obj, rotulo: str, caminho: str, extras: frozenset, erros: _Erros) -> None:
    """Percorre recursivamente; todo dict com 'valor' e 'status' é uma folha. Dict sem 'valor' é só percorrido."""
    if isinstance(obj, dict):
        if "valor" in obj and "status" in obj:
            _folha(obj["valor"], obj["status"], f"{rotulo}.{caminho}" if caminho else rotulo, extras, erros)
            return
        for chave, valor in obj.items():
            _folhas(valor, rotulo, f"{caminho}.{chave}" if caminho else str(chave), extras, erros)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _folhas(item, rotulo, f"{caminho}[{i}]", extras, erros)


def _validar_execucao(rotulo: str, env, data: str, erros: _Erros) -> None:
    if not isinstance(env, dict):
        erros.add(f"{rotulo}: envelope não é objeto")
        return
    _envelope(rotulo, env, "execucao", erros)
    registros = env.get("registros")
    if not isinstance(registros, list) or not registros:
        erros.add(f"{rotulo}.registros: vazio ou não é lista")
        return
    campos = contrato.PACOTES["execucao"].chave
    for i, reg in enumerate(registros):
        r = _rotulo(rotulo, reg, campos, i)
        if not isinstance(reg, dict):
            erros.add(f"{r}: registro não é objeto")
            continue
        if reg.get("data_referencia") != data:
            erros.add(f"{r}.data_referencia: {reg.get('data_referencia')!r} (esperado {data!r})")
        rodada = reg.get("rodada")
        if not isinstance(rodada, int) or isinstance(rodada, bool):
            erros.add(f"{r}.rodada: {rodada!r} não é inteiro")
        status = reg.get("status")
        if not isinstance(status, str) or status not in contrato.STATUS_EXECUCAO:
            erros.add(f"{r}.status: {status!r} fora de STATUS_EXECUCAO")
        _folhas(reg, r, "", frozenset(), erros)


def _validar_pacote(nome: str, env, erros: _Erros) -> None:
    if nome not in contrato.PACOTES:
        erros.add(f"{nome}: pacote desconhecido")
        return
    if not isinstance(env, dict):
        erros.add(f"{nome}: envelope não é objeto")
        return
    _envelope(nome, env, nome, erros)
    registros = env.get("registros")
    if not isinstance(registros, list):
        return
    if nome == _SERIE:
        _validar_serie(registros, erros)
        return
    campos = contrato.PACOTES[nome].chave
    extras = contrato.STATUS_EXTRAS.get(nome, frozenset())
    vistos = {}
    for i, reg in enumerate(registros):
        rotulo = _rotulo(nome, reg, campos, i)
        if not isinstance(reg, dict):
            erros.add(f"{rotulo}: registro não é objeto")
            continue
        if reg.get("versao_contrato") != contrato.VERSAO:
            erros.add(f"{rotulo}.versao_contrato: {reg.get('versao_contrato')!r} (esperado {contrato.VERSAO!r})")
        for campo in campos:
            valor = reg.get(campo)
            if valor is None:
                erros.add(f"{rotulo}: campo de chave '{campo}' ausente ou nulo")
            elif campo.startswith("data") and not _data_iso(valor):
                erros.add(f"{rotulo}.{campo}: {valor!r} fora de AAAA-MM-DD")
        if all(reg.get(c) is not None for c in campos):
            chave = repr(tuple(reg[c] for c in campos))
            if chave in vistos:
                erros.add(f"{rotulo}: chave duplicada (registros {vistos[chave]} e {i})")
            else:
                vistos[chave] = i
        _folhas(reg, rotulo, "", extras, erros)


def _validar_serie(registros: list, erros: _Erros) -> None:
    vistos = {}
    for i, reg in enumerate(registros):
        if not isinstance(reg, dict):
            erros.add(f"{_SERIE}[registro {i}]: registro não é objeto")
            continue
        ticker = reg.get("ticker")
        rotulo = f"{_SERIE}[{ticker}]" if ticker is not None else f"{_SERIE}[registro {i}]"
        if reg.get("versao_contrato") != contrato.VERSAO:
            erros.add(f"{rotulo}.versao_contrato: {reg.get('versao_contrato')!r} (esperado {contrato.VERSAO!r})")
        if ticker is None:
            erros.add(f"{rotulo}: campo de chave 'ticker' ausente ou nulo")
        else:
            chave = repr(ticker)
            if chave in vistos:
                erros.add(f"{rotulo}: chave duplicada (registros {vistos[chave]} e {i})")
            else:
                vistos[chave] = i
        if reg.get("tipo") not in contrato.TIPOS_SERIE:
            erros.add(f"{rotulo}.tipo: {reg.get('tipo')!r} fora de TIPOS_SERIE")
        datas = reg.get("datas")
        if not isinstance(datas, list):
            erros.add(f"{rotulo}.datas: não é lista")
            continue
        for j, data in enumerate(datas):
            if not _data_iso(data):
                erros.add(f"{rotulo}.datas[{j}]: {data!r} fora de AAAA-MM-DD")
        for j in range(len(datas) - 1):
            if _data_iso(datas[j]) and _data_iso(datas[j + 1]) and datas[j] >= datas[j + 1]:
                erros.add(f"{rotulo}.datas[{j + 1}]: {datas[j + 1]} não é posterior a {datas[j]}")
        posicao = {d: j for j, d in enumerate(datas) if _data_iso(d)}

        valores = reg.get("valores")
        if not isinstance(valores, dict):
            erros.add(f"{rotulo}.valores: não é objeto")
            continue
        com_tamanho = {}
        for campo, lista in valores.items():
            if not isinstance(lista, list):
                erros.add(f"{rotulo}.valores.{campo}: não é lista")
                continue
            if len(lista) != len(datas):
                erros.add(f"{rotulo}.valores.{campo}: {len(lista)} valores para {len(datas)} datas")
                continue
            com_tamanho[campo] = lista
            for j, v in enumerate(lista):
                if v is not None and not _numero(v):
                    erros.add(f"{rotulo}.valores.{campo}[{j}]: {v!r} não é número nem null")

        status = reg.get("status", {})
        if not isinstance(status, dict):
            erros.add(f"{rotulo}.status: não é objeto")
            status = {}
        cobertura = {}
        for campo, trechos in status.items():
            if campo not in valores:
                erros.add(f"{rotulo}.status.{campo}: campo sem valores")
            if not isinstance(trechos, list):
                erros.add(f"{rotulo}.status.{campo}: não é lista")
                continue
            cob = [None] * len(datas)
            for k, t in enumerate(trechos):
                onde = f"{rotulo}.status.{campo}[{k}]"
                if not isinstance(t, dict) or any(x not in t for x in ("de", "ate", "status")):
                    erros.add(f"{onde}: trecho sem 'de', 'ate' ou 'status'")
                    continue
                st = t["status"]
                if not isinstance(st, str) or st not in contrato.STATUS_VALIDOS:
                    erros.add(f"{onde}: status {st!r} inválido")
                    continue
                de, ate = t["de"], t["ate"]
                if not (_data_iso(de) and _data_iso(ate) and de in posicao and ate in posicao):
                    erros.add(f"{onde}: 'de' {de!r} ou 'ate' {ate!r} fora das datas")
                    continue
                i, f = posicao[de], posicao[ate]
                if i > f:
                    erros.add(f"{onde}: 'de' {de} posterior a 'ate' {ate}")
                    continue
                if any(cob[x] is not None for x in range(i, f + 1)):
                    erros.add(f"{onde}: trecho sobrepõe outro trecho de {campo}")
                    continue
                for x in range(i, f + 1):
                    cob[x] = st
            cobertura[campo] = cob

        for campo, lista in com_tamanho.items():
            cob = cobertura.get(campo, [None] * len(datas))
            for j, v in enumerate(lista):
                st = cob[j]
                onde = f"{rotulo}.valores.{campo}[{j}] ({datas[j]})"
                if v is None and st is None:
                    erros.add(f"{onde}: null sem trecho de status")
                elif v is None and st == "ok":
                    erros.add(f"{onde}: null com status 'ok'")
                elif v is not None and st not in (None, "lucro_negativo"):
                    erros.add(f"{onde}: valor coberto por status {st!r}")


def _validar_referencias(completa: dict, erros: _Erros) -> None:
    """resumo_acao, mercado_dia e noticias_dia presentes têm que ter a mesma data_referencia."""
    todas = []
    partes = []
    for nome in _PACOTES_REFERENCIA:
        env = completa.get(nome)
        regs = env.get("registros") if isinstance(env, dict) else None
        if not isinstance(regs, list):
            continue
        deste = []
        for reg in regs:
            if isinstance(reg, dict) and reg.get("data_referencia") is not None and reg["data_referencia"] not in deste:
                deste.append(reg["data_referencia"])
        if deste:
            partes.append(f"{nome}={', '.join(str(v) for v in deste)}")
            for v in deste:
                if v not in todas:
                    todas.append(v)
    if len(todas) > 1:
        erros.add("data_referencia divergente entre pacotes: " + "; ".join(partes))


def _validar_cruzada(entrada: Entrada, erros: _Erros) -> None:
    completa = entrada.completa
    ec = entrada.execucao_completa
    if completa is None or not isinstance(ec, dict):
        return
    registros = ec.get("registros")
    if not isinstance(registros, list) or not registros or not isinstance(registros[0], dict):
        erros.add("execucao_completa.registros: sem registro para conferir pacotes_enviados")
        return
    rotulo = _rotulo("execucao_completa", registros[0], contrato.PACOTES["execucao"].chave, 0)
    enviados = registros[0].get("pacotes_enviados")
    if not isinstance(enviados, list):
        erros.add(f"{rotulo}.pacotes_enviados: não é lista")
        return
    for k, item in enumerate(enviados):
        if not isinstance(item, dict):
            erros.add(f"{rotulo}.pacotes_enviados[{k}]: não é objeto")
            continue
        nome = item.get("pacote")
        if not isinstance(nome, str) or nome not in completa:
            erros.add(f"{rotulo}.pacotes_enviados[{k}]: pacote {nome!r} sem arquivo na carga completa")
            continue
        regs_arquivo = completa[nome].get("registros") if isinstance(completa[nome], dict) else None
        if isinstance(regs_arquivo, list) and item.get("registros") != len(regs_arquivo):
            erros.add(
                f"{rotulo}.pacotes_enviados[{k}]: '{nome}' diz {item.get('registros')!r} registros, "
                f"arquivo tem {len(regs_arquivo)}"
            )


def validar(entrada: Entrada) -> list[str]:
    """Lista de erros legíveis (pacote, chave do registro e caminho do campo). Vazia = ok."""
    erros = _Erros()
    _validar_execucao("execucao", entrada.execucao, entrada.data, erros)
    if entrada.execucao_completa is not None:
        _validar_execucao("execucao_completa", entrada.execucao_completa, entrada.data, erros)
    if entrada.completa:
        for nome, env in entrada.completa.items():
            _validar_pacote(nome, env, erros)
        _validar_referencias(entrada.completa, erros)
    _validar_cruzada(entrada, erros)
    return erros.lista
