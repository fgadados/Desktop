"""Quando parar o pipeline e quando apenas avisar.

O critério
----------
Este projeto trata silêncio como o pior modo de falha, e por isso adotou o
hábito de levantar erro em qualquer divergência. Levado longe demais, isso
vira outro problema: uma suposição minha, errada, derruba meia hora de
trabalho bom por uma diferença que não torna número nenhum incorreto.

A regra passa a ser explícita:

    **Falha dura** quando a suposição errada produziria NÚMERO ERRADO.
    **Aviso registrado** quando produziria número FALTANDO ou NÃO CONFERIDO.

Exemplos de cada lado, todos vindos de divergências reais encontradas contra
os arquivos da CVM e da B3:

| Divergência                          | Efeito              | Decisão |
|--------------------------------------|---------------------|---------|
| `ESCALA_MOEDA` desconhecida          | valor 1000x errado  | dura    |
| coluna declarada ausente do arquivo  | lê o campo errado   | dura    |
| registro fora de 245 bytes           | desloca todo campo  | dura    |
| `ORDEM_EXERC` fora do domínio        | dobra o valor       | dura    |
| `src_line` que os leitores discordam | origem errada       | dura    |
| coluna NOVA, não declarada           | nada fica errado    | aviso   |
| contagem do rodapé fora por poucos   | nada fica errado    | aviso   |
| arquivo novo dentro do pacote        | dado faltando       | aviso   |

Aviso não é silêncio: fica registrado, é impresso ao fim da execução e vai
para a tabela `aviso` do banco, com a origem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Aviso:
    origem: str  # arquivo ou etapa onde ocorreu
    categoria: str  # rótulo curto e estável, para agrupar
    mensagem: str
    registrado_em: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


_REGISTRO: list[Aviso] = []


def avisar(origem: str, categoria: str, mensagem: str, *, imprimir: bool = True) -> Aviso:
    """Registra uma divergência que não invalida número nenhum."""
    a = Aviso(origem=origem, categoria=categoria, mensagem=mensagem)
    _REGISTRO.append(a)
    if imprimir:
        print(f"      AVISO [{categoria}] {origem}: {mensagem}", flush=True)
    return a


def registrados() -> list[Aviso]:
    return list(_REGISTRO)


def limpar() -> None:
    """Só para os testes: o registro é global por processo."""
    _REGISTRO.clear()


def resumo() -> str:
    if not _REGISTRO:
        return "nenhum aviso."
    por_categoria: dict[str, int] = {}
    for a in _REGISTRO:
        por_categoria[a.categoria] = por_categoria.get(a.categoria, 0) + 1
    linhas = [f"{len(_REGISTRO)} aviso(s) -- nada aqui invalida numero:"]
    for cat, n in sorted(por_categoria.items(), key=lambda kv: -kv[1]):
        linhas.append(f"  {cat}: {n}")
    return "\n".join(linhas)


def para_quadro():
    import pandas as pd

    if not _REGISTRO:
        return pd.DataFrame(columns=["origem", "categoria", "mensagem", "registrado_em"])
    return pd.DataFrame(
        [{"origem": a.origem, "categoria": a.categoria, "mensagem": a.mensagem,
          "registrado_em": a.registrado_em} for a in _REGISTRO]
    )
