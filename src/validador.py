"""Validador do briefing gerado pela IA contra a ficha de fatos.

``validar(briefing, fatos)`` devolve uma lista de erros em linguagem clara
(lista vazia = aprovado). Verificações:

1. Esquema de ``prompts/briefing_v1.md`` (pydantic): campos, tipos, quantidades,
   limites de palavras, ``responsavel`` e ``horizonte`` nos valores permitidos.
2. Todo item cita ao menos 1 fato e todos os IDs citados existem.
3. Todo número do texto do item aparece nas frases dos fatos citados NAQUELE
   item (2025 e 2026 são sempre permitidos).
4. Toda sigla de UF do item aparece nas frases dos fatos citados.
5. Linguagem causal exige a palavra "hipótese".
6. Jargão técnico proibido.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger("validador")

UFS: tuple[str, ...] = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)
NUMEROS_SEMPRE_PERMITIDOS = frozenset({"2025", "2026"})
RESPONSAVEIS = ("DN", "DR", "DN e DR")
HORIZONTES = ("imediato (4º tri/2026)", "próximo ciclo de planejamento")
MAX_PALAVRAS_TEXTO = 45
MAX_PALAVRAS_ACAO = 40

# Número não pode começar colado a letra/dígito (evita "F001" e o "-" de "2025-2026").
RE_NUMERO = re.compile(r"(?<![\w])-?\d[\d.,]*")
RE_UF = re.compile(r"\b(" + "|".join(UFS) + r")\b")
RE_CAUSAL = re.compile(
    r"\bporque\b|\bdevido\b|\bcausa(?:s|ram|ou)?\b|\bem razão de\b|\bpor conta de\b",
    re.IGNORECASE,
)
JARGAO_PROIBIDO = ("_txt", "CSV", "status_dado", "farol")


# ---------------------------------------------------------------- esquema
class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _palavras(texto: str) -> int:
    return len(texto.split())


class MensagemCentral(_Base):
    texto: str = Field(min_length=1)
    fatos: list[str]


class ItemTexto(_Base):
    titulo: str = Field(min_length=1)
    texto: str = Field(min_length=1)
    fatos: list[str]

    @field_validator("texto")
    @classmethod
    def _limite(cls, v: str) -> str:
        if _palavras(v) > MAX_PALAVRAS_TEXTO:
            raise ValueError(f"texto com {_palavras(v)} palavras (máximo {MAX_PALAVRAS_TEXTO})")
        return v


class Recomendacao(_Base):
    titulo: str = Field(min_length=1)
    acao: str = Field(min_length=1)
    responsavel: Literal["DN", "DR", "DN e DR"]
    horizonte: Literal["imediato (4º tri/2026)", "próximo ciclo de planejamento"]
    fatos: list[str]

    @field_validator("acao")
    @classmethod
    def _limite(cls, v: str) -> str:
        if _palavras(v) > MAX_PALAVRAS_ACAO:
            raise ValueError(f"ação com {_palavras(v)} palavras (máximo {MAX_PALAVRAS_ACAO})")
        return v


class Limitacao(_Base):
    texto: str = Field(min_length=1)
    fatos: list[str]


class Briefing(_Base):
    mensagem_central: MensagemCentral
    destaques: list[ItemTexto] = Field(min_length=3, max_length=3)
    pontos_atencao: list[ItemTexto] = Field(min_length=3, max_length=4)
    recomendacoes: list[Recomendacao] = Field(min_length=3, max_length=4)
    limitacoes: list[Limitacao] = Field(default_factory=list, max_length=3)


def _erros_esquema(exc: ValidationError) -> list[str]:
    """Traduz os erros do pydantic para frases legíveis."""
    erros = []
    for e in exc.errors():
        local = ".".join(str(p) for p in e["loc"]) or "(raiz)"
        erros.append(f"Esquema: campo '{local}' inválido — {e['msg']}.")
    return erros


# ---------------------------------------------------------------- tokens
def tokens_numericos(texto: str) -> set[str]:
    """Extrai tokens numéricos (``-?\\d[\\d.,]*``) sem a pontuação final.

    "39,8%" -> "39,8"; "R$ 1,06 bi" -> "1,06"; "7.476." -> "7.476".
    """
    tokens = set()
    for m in RE_NUMERO.finditer(texto):
        tok = m.group(0).rstrip(".,")
        if tok and tok != "-":
            tokens.add(tok)
    return tokens


def ufs_mencionadas(texto: str) -> set[str]:
    """Siglas de UF (maiúsculas, palavra inteira) presentes no texto."""
    return set(RE_UF.findall(texto))


# ---------------------------------------------------------------- itens
def _itens(briefing: dict) -> list[tuple[str, dict, str]]:
    """Lista (rótulo, item, texto verificável) de todos os itens do briefing."""
    itens: list[tuple[str, dict, str]] = []
    mc = briefing.get("mensagem_central")
    if isinstance(mc, dict):
        itens.append(("mensagem_central", mc, str(mc.get("texto", ""))))
    for secao, campo_texto in (
        ("destaques", "texto"),
        ("pontos_atencao", "texto"),
        ("recomendacoes", "acao"),
        ("limitacoes", "texto"),
    ):
        lista = briefing.get(secao)
        if not isinstance(lista, list):
            continue
        for i, item in enumerate(lista, start=1):
            if not isinstance(item, dict):
                continue
            partes = [str(item.get("titulo", "")), str(item.get(campo_texto, ""))]
            itens.append((f"{secao}[{i}]", item, " ".join(p for p in partes if p)))
    return itens


def _validar_item(rotulo: str, item: dict, texto: str, frases: dict[str, str]) -> list[str]:
    erros: list[str] = []
    citados = item.get("fatos")
    if not isinstance(citados, list) or not citados:
        erros.append(f"{rotulo}: não cita nenhum fato (campo 'fatos' vazio).")
        citados = []
    inexistentes = [str(f) for f in citados if f not in frases]
    for fid in inexistentes:
        erros.append(f"{rotulo}: cita o fato '{fid}', que não existe na ficha.")
    frases_citadas = " ".join(frases[f] for f in citados if f in frases)

    permitidos = tokens_numericos(frases_citadas) | NUMEROS_SEMPRE_PERMITIDOS
    for tok in sorted(tokens_numericos(texto) - permitidos):
        erros.append(
            f"{rotulo}: o número '{tok}' não aparece nos fatos citados "
            f"({', '.join(map(str, citados)) or 'nenhum'})."
        )

    for uf in sorted(ufs_mencionadas(texto) - ufs_mencionadas(frases_citadas)):
        erros.append(f"{rotulo}: a UF '{uf}' não aparece nos fatos citados.")

    m = RE_CAUSAL.search(texto)
    if m and "hipótese" not in texto.lower():
        erros.append(
            f"{rotulo}: usa linguagem de causa ('{m.group(0)}') sem a palavra 'hipótese'."
        )

    baixo = texto.lower()
    for termo in JARGAO_PROIBIDO:
        if termo.lower() in baixo:
            erros.append(f"{rotulo}: contém o termo técnico proibido '{termo}'.")
    return erros


def validar(briefing: dict, fatos: list[dict]) -> list[str]:
    """Valida o briefing contra a ficha de fatos.

    Args:
        briefing: JSON do briefing já convertido em dict.
        fatos: lista de fatos (cada um com ao menos ``id`` e ``frase``).

    Returns:
        Lista de erros em linguagem clara; vazia significa aprovado.
    """
    if not isinstance(briefing, dict):
        return ["Esquema: o briefing deve ser um objeto JSON."]
    erros: list[str] = []
    try:
        Briefing.model_validate(briefing)
    except ValidationError as exc:
        erros.extend(_erros_esquema(exc))

    frases = {str(f["id"]): str(f.get("frase", "")) for f in fatos}
    for rotulo, item, texto in _itens(briefing):
        erros.extend(_validar_item(rotulo, item, texto, frases))

    logger.debug("Validação: %d erro(s).", len(erros))
    return erros
