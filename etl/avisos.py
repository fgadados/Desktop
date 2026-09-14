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

Dois processos, um registro
---------------------------
`_REGISTRO` é global por PROCESSO, e `run.py extrair` e `run.py transformar`
são dois. Os avisos da extração morriam no fim dela: o log dizia "2 aviso(s)"
e, minutos depois, "avisos: 0" -- e a tabela `aviso` do banco saía vazia,
contrariando o parágrafo acima. Aviso que não chega ao banco é exatamente o
silêncio que este módulo existe para impedir.

Por isso a extração grava o registro em `data/avisos.json` ao terminar, e a
transformação carrega esse arquivo antes de montar a tabela. O arquivo é
sobrescrito a cada extração: ele descreve a rodada que produziu os Parquet
que estão em disco, não um histórico.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
    _HERDADOS.clear()


# Avisos de um processo ANTERIOR desta mesma rodada (tipicamente a extração).
# Ficam separados de `_REGISTRO` para que `resumo()` continue falando só do
# processo corrente, que é o que o usuário está vendo rodar.
_HERDADOS: list[Aviso] = []


def _arquivo_padrao() -> Path:
    from etl.config import DATA

    return Path(DATA) / "avisos.json"


def persistir(caminho: Path | None = None) -> Path:
    """Grava os avisos deste processo, para o próximo processo da rodada."""
    p = Path(caminho) if caminho else _arquivo_padrao()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([asdict(a) for a in _REGISTRO], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return p


def herdar(caminho: Path | None = None) -> int:
    """Carrega os avisos gravados pelo processo anterior. Devolve quantos.

    Arquivo ausente ou ilegível não é erro: a transformação pode ser rodada
    sozinha, sem extração nenhuma antes dela.
    """
    p = Path(caminho) if caminho else _arquivo_padrao()
    _HERDADOS.clear()
    try:
        bruto = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    for d in bruto:
        try:
            _HERDADOS.append(Aviso(**d))
        except TypeError:
            continue  # formato de outra versao: ignora a linha, nao a rodada
    return len(_HERDADOS)


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
    """Tudo que vai para a tabela `aviso`: este processo mais os herdados."""
    import pandas as pd

    colunas = ["origem", "categoria", "mensagem", "registrado_em"]
    todos = _HERDADOS + _REGISTRO
    if not todos:
        return pd.DataFrame(columns=colunas)
    quadro = pd.DataFrame([asdict(a) for a in todos])[colunas]
    # O mesmo aviso pode chegar pelos dois caminhos se a transformacao rodar
    # no mesmo processo da extracao (`run.py tudo`).
    return quadro.drop_duplicates().reset_index(drop=True)
