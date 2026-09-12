"""Linhagem: nenhum indicador existe sem suas contas de origem e sua formula.

Criterio de aceite do projeto: "toda linha de indicador carrega as contas de
origem e a formula". Isto e implementado como tipo, nao como convencao -- um
`Indicador` so pode ser construido informando `formula` e `entradas`, e
`Indicador.OK` exige pelo menos uma entrada.

Status possiveis:

* ``OK``             -- valor calculado.
* ``FALTANDO``       -- insumo ausente na fonte. Nunca interpolado.
* ``NAO_SE_APLICA``  -- o indicador nao existe para o plano de contas do setor
                        (regra 6). Diferente de faltando: aqui o numero nao
                        deveria existir.
* ``DIVIDIR_POR_ZERO``-- denominador nulo ou zero.
* ``BASE_INCONSISTENTE`` -- janela contaminada por troca de base contabil.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

OK = "OK"
FALTANDO = "FALTANDO"
NAO_SE_APLICA = "NAO_SE_APLICA"
DIVIDIR_POR_ZERO = "DIVIDIR_POR_ZERO"
BASE_INCONSISTENTE = "BASE_INCONSISTENTE"
NAO_AJUSTADO = "NAO_AJUSTADO"


@dataclass(frozen=True)
class Entrada:
    """Um insumo, rastreado ate a linha do arquivo de origem."""

    rotulo: str
    valor: float | None
    cd_conta: str | None = None
    ds_conta: str | None = None
    demonstrativo: str | None = None
    base: str | None = None
    versao: str | None = None
    ordem_exerc: str | None = None
    dt_refer: str | None = None
    src_archive: str | None = None
    src_file: str | None = None
    src_line: int | None = None
    src_sha256: str | None = None

    @property
    def referencia(self) -> str:
        """String curta que localiza o numero no arquivo original."""
        if self.src_file and self.src_line:
            alvo = f"{self.src_file}:{self.src_line}"
            if self.src_archive and self.src_archive != self.src_file:
                alvo = f"{self.src_archive}!{alvo}"
            return alvo
        return "(sem origem)"


@dataclass(frozen=True)
class Indicador:
    entidade: str  # CNPJ ou ticker, conforme o dominio do indicador
    periodo: str  # "2023", "2023T3", ou data ISO para indicador diario
    nome: str
    valor: float | None
    formula: str
    status: str
    entradas: tuple[Entrada, ...] = field(default_factory=tuple)
    motivo: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.formula.strip():
            raise ValueError(f"indicador '{self.nome}' sem formula")
        if self.status == OK:
            if self.valor is None:
                raise ValueError(f"indicador '{self.nome}' com status OK e valor nulo")
            if not self.entradas:
                raise ValueError(f"indicador '{self.nome}' com status OK e sem entradas")
        if self.status != OK and not self.motivo:
            raise ValueError(f"indicador '{self.nome}' com status {self.status} e sem motivo")

    def para_linha(self) -> dict[str, Any]:
        return {
            "entidade": self.entidade,
            "periodo": self.periodo,
            "indicador": self.nome,
            "valor": self.valor,
            "formula": self.formula,
            "status": self.status,
            "motivo": self.motivo,
            "contas_origem": ", ".join(
                f"{e.cd_conta} {e.ds_conta}" for e in self.entradas if e.cd_conta
            ) or None,
            "extra_json": json.dumps(self.extra, ensure_ascii=False) if self.extra else None,
        }

    def linhas_entrada(self) -> list[dict[str, Any]]:
        return [
            {
                "entidade": self.entidade,
                "periodo": self.periodo,
                "indicador": self.nome,
                "ordem": i,
                "referencia": e.referencia,
                **asdict(e),
            }
            for i, e in enumerate(self.entradas)
        ]


def faltando(entidade: str, periodo: str, nome: str, formula: str, motivo: str,
             entradas: Iterable[Entrada] = ()) -> Indicador:
    return Indicador(entidade, periodo, nome, None, formula, FALTANDO,
                     tuple(entradas), motivo)


def nao_se_aplica(entidade: str, periodo: str, nome: str, formula: str,
                  motivo: str) -> Indicador:
    return Indicador(entidade, periodo, nome, None, formula, NAO_SE_APLICA, (), motivo)


def para_quadros(indicadores: Iterable[Indicador]):
    """Converte para (df_indicador, df_entrada) prontos para carga no DuckDB."""
    import pandas as pd

    inds = list(indicadores)
    cabecalho = pd.DataFrame([i.para_linha() for i in inds])
    entradas = pd.DataFrame([l for i in inds for l in i.linhas_entrada()])
    return cabecalho, entradas
