"""Regra 6: plano de contas setorial.

Resolve CONCEITO -> conta real da CVM segundo o plano do setor da companhia, e
responde se um indicador sequer faz sentido para aquele plano.

Duas garantias:

* nenhum conceito e resolvido por aproximacao. Se o codigo declarado no plano
  nao existir nos dados da empresa naquele periodo, o conceito fica FALTANDO;
* empresa sem setor identificado cai no plano INDEFINIDO, que declara quase
  tudo como nao aplicavel. O sistema prefere nao exibir numero a exibir numero
  incomparavel.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from etl.config import CONFIG_DIR, MANUAL
from transform.lineage import Entrada

ARQUIVO_PLANOS = CONFIG_DIR / "sector_plans.yml"
ARQUIVO_OVERRIDES = MANUAL / "sector_overrides.csv"


def _sem_acento(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", str(texto or ""))
        .encode("ascii", "ignore")
        .decode()
        .upper()
        .strip()
    )


@lru_cache(maxsize=4)
def carregar_planos(caminho: str | None = None) -> dict:
    p = Path(caminho) if caminho else ARQUIVO_PLANOS
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))

    # Resolve heranca entre planos (ENERGIA herda INDUSTRIAL).
    planos = cfg["planos"]
    for nome, plano in planos.items():
        pai = plano.get("herda")
        if pai:
            base = planos[pai]
            plano["contas"] = {**base.get("contas", {}), **(plano.get("contas") or {})}
            vistos = {d["conceito"] for d in (plano.get("nao_se_aplica") or [])}
            plano["nao_se_aplica"] = (plano.get("nao_se_aplica") or []) + [
                d for d in base.get("nao_se_aplica", []) if d["conceito"] not in vistos
            ]
    return cfg


def _overrides() -> dict[str, str]:
    if not ARQUIVO_OVERRIDES.exists():
        return {}
    df = pd.read_csv(ARQUIVO_OVERRIDES, dtype=str)
    faltando = {"cnpj", "plano"} - set(df.columns)
    if faltando:
        raise ValueError(f"{ARQUIVO_OVERRIDES}: colunas ausentes {sorted(faltando)}")
    return {
        re.sub(r"\D", "", str(c)).zfill(14): str(p).upper().strip()
        for c, p in zip(df["cnpj"], df["plano"])
    }


def classificar(cadastro: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """CNPJ -> plano de contas, com a origem da classificacao registrada."""
    cfg = cfg or carregar_planos()
    regras = cfg["deteccao"]
    over = _overrides()

    from transform.depara import normalizar_cnpj

    cnpj = normalizar_cnpj(cadastro["CNPJ_CIA"])
    setor = cadastro["SETOR_ATIV"].map(_sem_acento) if "SETOR_ATIV" in cadastro else ""

    planos, origens = [], []
    for c, s in zip(cnpj, setor):
        if c in over:
            planos.append(over[c])
            origens.append(f"override manual ({ARQUIVO_OVERRIDES.name})")
            continue
        escolhido, origem = "INDEFINIDO", "sem casamento em SETOR_ATIV"
        for regra in regras:
            if re.search(regra["padrao"], s or ""):
                escolhido = regra["plano"]
                origem = f"SETOR_ATIV='{s}' casou com /{regra['padrao']}/"
                break
        planos.append(escolhido)
        origens.append(origem)

    return pd.DataFrame(
        {"cnpj": cnpj, "setor_ativ": setor, "plano": planos, "origem_classificacao": origens}
    ).drop_duplicates("cnpj").reset_index(drop=True)


@dataclass(frozen=True)
class Resolucao:
    conceito: str
    valor: float | None
    entrada: Entrada | None
    status: str  # OK | FALTANDO | NAO_SE_APLICA | DIVERGENCIA_LAYOUT
    motivo: str | None = None


class Resolvedor:
    """Resolve conceitos contra os fatos de uma empresa/periodo."""

    def __init__(self, plano: str, cfg: dict | None = None):
        self.cfg = cfg or carregar_planos()
        if plano not in self.cfg["planos"]:
            raise KeyError(f"plano de contas desconhecido: {plano}")
        self.plano = plano
        self._spec = self.cfg["planos"][plano]
        self._na = {d["conceito"]: d["motivo"] for d in (self._spec.get("nao_se_aplica") or [])}

    # -- aplicabilidade -----------------------------------------------------
    def conceito_aplicavel(self, conceito: str) -> tuple[bool, str | None]:
        motivo = self._na.get(conceito)
        return (motivo is None), motivo

    def indicador_aplicavel(self, indicador: str) -> tuple[bool, str | None]:
        deps = self.cfg["dependencias_indicador"].get(indicador, [])
        for d in deps:
            ok, motivo = self.conceito_aplicavel(d)
            if not ok:
                return False, f"{indicador} depende de '{d}': {motivo}"
        return True, None

    # -- resolucao ----------------------------------------------------------
    def resolver(self, fatos: pd.DataFrame, conceito: str) -> Resolucao:
        aplicavel, motivo = self.conceito_aplicavel(conceito)
        if not aplicavel:
            return Resolucao(conceito, None, None, "NAO_SE_APLICA", motivo)

        spec = (self._spec.get("contas") or {}).get(conceito)
        if spec is None:
            return Resolucao(
                conceito, None, None, "FALTANDO",
                f"conceito '{conceito}' nao mapeado no plano {self.plano}",
            )

        dem = spec.get("demonstrativo")
        cand = fatos[fatos["demonstrativo"] == dem] if dem else fatos

        for codigo in spec.get("codigos") or []:
            linha = cand[cand["CD_CONTA"] == codigo]
            if linha.empty:
                continue
            if len(linha) > 1:
                return Resolucao(
                    conceito, None, None, "FALTANDO",
                    f"conta {codigo} aparece {len(linha)} vezes apos deduplicacao "
                    "-- ambiguidade nao resolvida silenciosamente",
                )
            r = linha.iloc[0]
            padrao = spec.get("padrao_ds_conta")
            if padrao and not re.search(padrao, _sem_acento(r["DS_CONTA"])):
                return Resolucao(
                    conceito, None, None, "DIVERGENCIA_LAYOUT",
                    f"conta {codigo} tem descricao '{r['DS_CONTA']}', que nao casa com "
                    f"/{padrao}/ esperado no plano {self.plano}",
                )
            return Resolucao(conceito, float(r["VL_CONTA_NUM"]), _entrada(conceito, r), "OK")

        # Busca por descricao dentro de um subgrupo (caso da D&A na DFC).
        prefixo, busca = spec.get("prefixo_busca"), spec.get("busca_por_descricao")
        if prefixo and busca:
            sub = cand[cand["CD_CONTA"].astype(str).str.startswith(prefixo)]
            achados = sub[sub["DS_CONTA"].map(lambda d: bool(re.search(busca, _sem_acento(d))))]
            if len(achados) == 1:
                r = achados.iloc[0]
                return Resolucao(conceito, float(r["VL_CONTA_NUM"]), _entrada(conceito, r), "OK")
            if len(achados) > 1:
                # Varias linhas de D&A: soma, mas registra todas as origens.
                valor = float(achados["VL_CONTA_NUM"].sum())
                r = achados.iloc[0]
                ent = _entrada(conceito, r)
                ent = Entrada(
                    **{
                        **ent.__dict__,
                        "valor": valor,
                        "ds_conta": " + ".join(achados["DS_CONTA"].astype(str)),
                        "cd_conta": " + ".join(achados["CD_CONTA"].astype(str)),
                    }
                )
                return Resolucao(conceito, valor, ent, "OK")
            return Resolucao(
                conceito, None, None, "FALTANDO",
                f"nenhuma conta sob {prefixo}.* com descricao casando /{busca}/",
            )

        codigos = spec.get("codigos") or []
        return Resolucao(
            conceito, None, None, "FALTANDO",
            f"nenhum dos codigos {codigos} presente em {dem} para este periodo",
        )


def _entrada(conceito: str, r: pd.Series) -> Entrada:
    return Entrada(
        rotulo=conceito,
        valor=float(r["VL_CONTA_NUM"]),
        cd_conta=str(r["CD_CONTA"]),
        ds_conta=str(r["DS_CONTA"]),
        demonstrativo=str(r.get("demonstrativo")),
        base=str(r.get("base")),
        versao=str(r.get("VERSAO")),
        ordem_exerc=str(r.get("ORDEM_EXERC")),
        dt_refer=str(r.get("DT_REFER")),
        src_archive=r.get("src_archive"),
        src_file=r.get("src_file"),
        src_line=int(r["src_line"]) if pd.notna(r.get("src_line")) else None,
        src_sha256=r.get("src_sha256"),
    )
