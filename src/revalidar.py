"""Revalidação dos textos publicados contra a ficha de fatos ATUAL.

Quando a ficha muda (ex.: correção de arredondamento), um texto aprovado antes
pode citar números que não existem mais. Este módulo reaplica as mesmas regras
da geração, sem reimplementá-las:

- ``outputs/briefing_{escopo}.json`` (só ``status == "aprovado"``; a contingência
  reproduz as frases da ficha e não traz texto da IA): ``src.validador.validar``;
- ``outputs/app_data/perguntas_{escopo}.json``: ``src.perguntas.validar_resposta``
  (regras de ``src.validador`` + regras próprias das respostas).

Só entram os escopos publicáveis (``src.produto.ESCOPOS``). A ficha atual é
``outputs/fatos_{escopo}.json``; se faltar e houver texto a conferir, o escopo é
reprovado (não se gera ficha aqui).

Uso:
    python -m src.revalidar
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from src.perguntas import catalogo, validar_resposta
from src.produto import ESCOPOS
from src.validador import validar

logger = logging.getLogger("revalidar")

RAIZ = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = RAIZ / "outputs"


def _ler_json(caminho: Path) -> tuple[dict | None, str | None]:
    """Lê um JSON; devolve (objeto, erro)."""
    try:
        with caminho.open(encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"não foi possível ler {caminho.name} ({exc})"
    if not isinstance(obj, dict):
        return None, f"{caminho.name} não é um objeto JSON"
    return obj, None


def _ficha_atual(escopo: str, outputs_dir: Path) -> tuple[list[dict] | None, str | None]:
    """Fatos atuais do escopo (``outputs/fatos_{escopo}.json``)."""
    caminho = outputs_dir / f"fatos_{escopo}.json"
    if not caminho.is_file():
        return None, f"ficha de fatos atual ausente ({caminho.name})"
    doc, erro = _ler_json(caminho)
    if erro:
        return None, erro
    fatos = doc.get("fatos")
    if not isinstance(fatos, list):
        return None, f"{caminho.name} sem a lista 'fatos'"
    return fatos, None


def revalidar_briefing(caminho: Path, outputs_dir: Path) -> list[str]:
    """Erros do briefing aprovado contra a ficha atual (vazio = ok)."""
    doc, erro = _ler_json(caminho)
    if erro:
        return [erro]
    if doc.get("status") != "aprovado":
        logger.info("%s: status %r, sem texto da IA; ignorado.", caminho.name, doc.get("status"))
        return []
    escopo = str(doc.get("escopo") or caminho.stem.removeprefix("briefing_"))
    fatos, erro = _ficha_atual(escopo, outputs_dir)
    if erro:
        return [erro]
    return [f"{caminho.name}: {e}" for e in validar(doc.get("briefing"), fatos)]


def revalidar_perguntas(caminho: Path, outputs_dir: Path) -> list[str]:
    """Erros das respostas publicadas contra a ficha atual (vazio = ok)."""
    doc, erro = _ler_json(caminho)
    if erro:
        return [erro]
    escopo = str(doc.get("escopo") or caminho.stem.removeprefix("perguntas_"))
    itens = doc.get("perguntas")
    if not isinstance(itens, list):
        return [f"{caminho.name}: sem a lista 'perguntas'"]
    if not itens:
        return []
    fatos, erro = _ficha_atual(escopo, outputs_dir)
    if erro:
        return [erro]
    frases = {str(f["id"]): str(f.get("frase", "")) for f in fatos}
    oficiais = dict(catalogo(escopo))
    erros: list[str] = []
    for item in itens:
        if not isinstance(item, dict):
            erros.append(f"{caminho.name}: resposta que não é objeto JSON")
            continue
        pid = str(item.get("id", "?"))
        # Publicado, cada fato vira {id, texto, fonte}; volta a ser só o ID, como na geração.
        citados = item.get("fatos")
        if isinstance(citados, list):
            citados = [f.get("id") if isinstance(f, dict) else f for f in citados]
        nucleo = {"id": item.get("id"), "pergunta": item.get("pergunta"),
                  "resposta": item.get("resposta"), "fatos": citados}
        pergunta = oficiais.get(pid, str(item.get("pergunta", "")))
        erros.extend(f"{caminho.name} [{pid}]: {e}"
                     for e in validar_resposta(nucleo, pergunta, frases))
    return erros


def revalidar(outputs_dir: Path = OUTPUTS_DIR) -> dict[str, list[str]]:
    """Revalida todos os textos publicados.

    Returns:
        ``{escopo: [erros]}`` só com os escopos reprovados (vazio = tudo ok).
    """
    reprovados: dict[str, list[str]] = {}
    alvos: list[tuple[str, Path, Callable[[Path, Path], list[str]]]] = []
    for escopo in ESCOPOS:  # só escopos publicáveis (os mesmos de src.publicar)
        alvos.append((escopo, outputs_dir / f"briefing_{escopo}.json", revalidar_briefing))
        alvos.append((escopo, outputs_dir / "app_data" / f"perguntas_{escopo}.json",
                       revalidar_perguntas))
    alvos = [a for a in alvos if a[1].is_file()]
    for escopo, caminho, funcao in alvos:
        erros = funcao(caminho, outputs_dir)
        if erros:
            reprovados.setdefault(escopo, []).extend(erros)
    logger.info("Revalidação: %d arquivo(s), %d escopo(s) reprovado(s).",
                len(alvos), len(reprovados))
    return reprovados


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m src.revalidar``; código 1 se houver reprovação."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    reprovados = revalidar(OUTPUTS_DIR)
    for escopo, erros in reprovados.items():
        logger.error("Escopo %s reprovado (%d erro(s)):", escopo, len(erros))
        for e in erros:
            logger.error("  - %s", e)
    if not reprovados:
        logger.info("Todos os textos publicados conferem com a ficha atual.")
    return 1 if reprovados else 0


if __name__ == "__main__":
    raise SystemExit(main())
