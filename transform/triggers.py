"""Gatilhos configuraveis por YAML.

O sistema e descritivo. Um gatilho nao recomenda nada: ele diz que uma
condicao que VOCE escreveu foi satisfeita, e mostra os numeros que a
satisfizeram, com a linhagem de cada um.

Avaliacao sem `eval`. Os operadores sao um dicionario fechado; qualquer coisa
fora dele e erro de configuracao, nao codigo executado.

Metrica ausente nao dispara e nao silencia: o gatilho fica INDETERMINADO com o
motivo por condicao.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from etl.config import CONFIG_DIR

ARQUIVO = CONFIG_DIR / "triggers.yml"

DISPAROU = "DISPAROU"
NAO_DISPAROU = "NAO_DISPAROU"
INDETERMINADO = "INDETERMINADO"

OPERADORES = {
    "lt": (lambda a, b: a < b, "<"),
    "le": (lambda a, b: a <= b, "<="),
    "gt": (lambda a, b: a > b, ">"),
    "ge": (lambda a, b: a >= b, ">="),
    "eq": (lambda a, b: a == b, "=="),
    "ne": (lambda a, b: a != b, "!="),
    "entre": (lambda a, b: b[0] <= a <= b[1], "entre"),
}


class ConfiguracaoInvalida(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoCondicao:
    metrica: str
    operador: str
    alvo: Any
    valor_observado: float | None
    satisfeita: bool | None  # None = indeterminado
    motivo: str | None
    origem: str | None = None  # linhagem da metrica


@dataclass(frozen=True)
class ResultadoGatilho:
    nome: str
    descricao: str
    escopo: str
    entidade: str
    status: str
    condicoes: tuple[ResultadoCondicao, ...] = field(default_factory=tuple)

    @property
    def explicacao(self) -> str:
        """Texto exibido na interface. Descreve, nunca prescreve."""
        if self.status == DISPAROU:
            partes = [
                f"{c.metrica} = {_fmt(c.valor_observado)} {OPERADORES[c.operador][1]} "
                f"{_fmt(c.alvo)}"
                for c in self.condicoes
                if c.satisfeita
            ]
            return "Condicao satisfeita: " + "; ".join(partes)
        if self.status == INDETERMINADO:
            partes = [f"{c.metrica}: {c.motivo}" for c in self.condicoes if c.satisfeita is None]
            return "Nao avaliado -- " + "; ".join(partes)
        partes = [
            f"{c.metrica} = {_fmt(c.valor_observado)} nao satisfaz "
            f"{OPERADORES[c.operador][1]} {_fmt(c.alvo)}"
            for c in self.condicoes
            if c.satisfeita is False
        ]
        return "Nao satisfeito: " + "; ".join(partes)


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, (list, tuple)):
        return f"[{', '.join(_fmt(x) for x in v)}]"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def carregar(caminho: Path | None = None) -> list[dict]:
    cfg = yaml.safe_load((caminho or ARQUIVO).read_text(encoding="utf-8")) or {}
    gatilhos = cfg.get("gatilhos") or []
    for g in gatilhos:
        for campo in ("nome", "descricao", "escopo"):
            if campo not in g:
                raise ConfiguracaoInvalida(f"gatilho sem '{campo}': {g}")
        if g["escopo"] not in {"TICKER", "EMPRESA"}:
            raise ConfiguracaoInvalida(f"escopo invalido em '{g['nome']}': {g['escopo']}")
        if not (g.get("todas") or g.get("qualquer")):
            raise ConfiguracaoInvalida(f"gatilho '{g['nome']}' sem condicoes")
        for c in (g.get("todas") or []) + (g.get("qualquer") or []):
            if c.get("operador") not in OPERADORES:
                raise ConfiguracaoInvalida(
                    f"operador '{c.get('operador')}' desconhecido em '{g['nome']}'. "
                    f"Validos: {sorted(OPERADORES)}"
                )
            if "metrica" not in c or "valor" not in c:
                raise ConfiguracaoInvalida(f"condicao incompleta em '{g['nome']}': {c}")
    return gatilhos


def _avaliar_condicao(c: dict, metricas: dict[str, Any]) -> ResultadoCondicao:
    nome = c["metrica"]
    op_nome = c["operador"]
    fn, _ = OPERADORES[op_nome]
    alvo = c["valor"]

    registro = metricas.get(nome)
    if registro is None:
        return ResultadoCondicao(nome, op_nome, alvo, None, None,
                                 "metrica nao calculada para esta entidade/periodo")
    valor = registro.get("valor") if isinstance(registro, dict) else registro
    status = registro.get("status", "OK") if isinstance(registro, dict) else "OK"
    origem = registro.get("origem") if isinstance(registro, dict) else None
    if status != "OK" or valor is None:
        motivo = (registro.get("motivo") if isinstance(registro, dict) else None) or status
        return ResultadoCondicao(nome, op_nome, alvo, None, None, motivo, origem)

    # Alvo pode referenciar outra metrica pelo nome.
    if isinstance(alvo, str):
        outro = metricas.get(alvo)
        v_outro = outro.get("valor") if isinstance(outro, dict) else outro
        if v_outro is None:
            return ResultadoCondicao(nome, op_nome, alvo, float(valor), None,
                                     f"metrica de comparacao '{alvo}' indisponivel", origem)
        alvo = float(v_outro)

    return ResultadoCondicao(nome, op_nome, alvo, float(valor),
                             bool(fn(float(valor), alvo)), None, origem)


def avaliar(gatilho: dict, entidade: str, metricas: dict[str, Any]) -> ResultadoGatilho:
    todas = [_avaliar_condicao(c, metricas) for c in (gatilho.get("todas") or [])]
    qualquer = [_avaliar_condicao(c, metricas) for c in (gatilho.get("qualquer") or [])]
    cond = tuple(todas + qualquer)

    if any(c.satisfeita is None for c in todas):
        status = INDETERMINADO
    elif todas and not all(c.satisfeita for c in todas):
        status = NAO_DISPAROU
    elif qualquer and not any(c.satisfeita for c in qualquer):
        status = INDETERMINADO if all(c.satisfeita is None for c in qualquer) else NAO_DISPAROU
    else:
        status = DISPAROU

    return ResultadoGatilho(
        gatilho["nome"], gatilho["descricao"], gatilho["escopo"], entidade, status, cond
    )


def avaliar_todos(
    entidade: str, escopo: str, metricas: dict[str, Any], caminho: Path | None = None
) -> list[ResultadoGatilho]:
    return [
        avaliar(g, entidade, metricas)
        for g in carregar(caminho)
        if g["escopo"] == escopo
    ]
