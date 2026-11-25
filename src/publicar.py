"""Publicação: monta ``site/``, o Hugging Face Space estático (``sdk: static``).

Conteúdo de ``site/`` (recriado do zero a cada publicação; entra no Git):
- ``index.html``, ``app.js``, ``styles.css``: cópia de ``app/static``;
- ``README.md``: cabeçalho do Spaces + descrição curta;
- ``data/{escopo}.json`` (BR + 27 UFs) e ``data/regioes.json``: de ``outputs/app_data``;
- ``data/perguntas_{escopo}.json``: os que existirem em ``outputs/app_data`` (opcionais);
- ``data/escopos.json``: lista ``[{escopo, nome}]`` (a mesma de ``/api/escopos``);
- ``figs/*.png``: de ``outputs/figs``;
- ``pdf/Executive_Briefing_SENAI_{escopo}.pdf``: os que existirem em ``outputs/``;
- ``MANIFESTO.json``: data/hora, revisor e sha256 de cada arquivo.

Antes de copiar, ``src.revalidar`` confere briefings e perguntas contra a ficha de
fatos atual; se algum texto estiver desatualizado, nada é publicado.

Nada fora dessa seleção é copiado; ``verificar_site`` recusa a publicação se
aparecer ``.env``, ``data/raw``, logs, ``src/``, ``tests/`` ou código Python.

Uso:
    python -m src.publicar --revisado-por "Nome"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.produto import ESCOPOS, NOMES
from src.revalidar import revalidar

logger = logging.getLogger("publicar")

RAIZ = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = RAIZ / "outputs"
STATIC_DIR = RAIZ / "app" / "static"
SITE_DIR = RAIZ / "site"
MANIFESTO = "MANIFESTO.json"
ARQUIVOS_PAGINA: tuple[str, ...] = ("index.html", "app.js", "styles.css")

README_SPACE = """---
title: Briefing Executivo SENAI
emoji: 📊
colorFrom: blue
colorTo: gray
sdk: static
pinned: false
---

# Briefing Executivo SENAI

Painel e briefing executivo do desempenho do SENAI (Brasil e os 27 Departamentos
Regionais), a partir de dados públicos do Portal da Transparência. Os textos são
gerados com IA e só vão ao ar depois de passar por um validador de fatos e por
revisão humana. Site estático: os dados estão em `data/`, as figuras em `figs/`
e os PDFs executivos em `pdf/`; `MANIFESTO.json` registra quem revisou e o
sha256 de cada arquivo.
"""

# Nomes/pastas que nunca podem ir ao ar (comparados em minúsculas).
PROIBIDOS_NOME: frozenset[str] = frozenset({".env", "src", "tests", "logs", "raw", ".git"})
PROIBIDOS_SUFIXO: tuple[str, ...] = (".py", ".pyc", ".log", ".env")


class ErroPublicacao(Exception):
    """Publicação recusada (revisor ausente, dados incompletos ou conteúdo proibido)."""


def sha256(caminho: Path) -> str:
    """Hash sha256 (hex) do conteúdo do arquivo."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 16), b""):
            h.update(bloco)
    return h.hexdigest()


def selecionar(outputs_dir: Path, static_dir: Path) -> list[tuple[Path, str]]:
    """Arquivos que vão ao ar, com o caminho relativo de destino em ``site/``.

    Recusa (``ErroPublicacao``) se faltar arquivo da página, JSON de algum
    escopo ou ``regioes.json`` (o site ficaria quebrado).
    """
    itens: list[tuple[Path, str]] = []
    faltando: list[str] = []
    for nome in ARQUIVOS_PAGINA:
        origem = static_dir / nome
        if origem.is_file():
            itens.append((origem, nome))
        else:
            faltando.append(str(origem))
    app_data = outputs_dir / "app_data"
    for nome in (*ESCOPOS, "regioes"):
        origem = app_data / f"{nome}.json"
        if origem.is_file():
            itens.append((origem, f"data/{nome}.json"))
        else:
            faltando.append(str(origem))
    if faltando:
        raise ErroPublicacao("Nada publicado; faltam (rode python -m src.produto): "
                             + ", ".join(faltando))
    for escopo in ESCOPOS:
        origem = app_data / f"perguntas_{escopo}.json"
        if origem.is_file():
            itens.append((origem, f"data/{origem.name}"))
    for origem in sorted((outputs_dir / "figs").glob("*.png")):
        if origem.is_file():
            itens.append((origem, f"figs/{origem.name}"))
    for escopo in ESCOPOS:
        origem = outputs_dir / f"Executive_Briefing_SENAI_{escopo}.pdf"
        if origem.is_file():
            itens.append((origem, f"pdf/{origem.name}"))
    return itens


def verificar_site(site_dir: Path) -> list[str]:
    """Caminhos (relativos) proibidos encontrados em ``site_dir``; vazio = ok."""
    ruins: list[str] = []
    for p in site_dir.rglob("*"):
        rel = p.relative_to(site_dir)
        partes = [x.lower() for x in rel.parts]
        if any(x in PROIBIDOS_NOME for x in partes) or p.name.lower().endswith(PROIBIDOS_SUFIXO):
            ruins.append(rel.as_posix())
    return sorted(ruins)


def publicar(revisado_por: str, outputs_dir: Path = OUTPUTS_DIR,
             site_dir: Path = SITE_DIR, static_dir: Path = STATIC_DIR,
             agora: datetime | None = None) -> dict[str, Any]:
    """Recria ``site_dir`` com a página, os dados selecionados e o manifesto."""
    revisor = revisado_por.strip()
    if not revisor:
        raise ErroPublicacao("Informe quem revisou (--revisado-por).")
    itens = selecionar(outputs_dir, static_dir)
    reprovados = revalidar(outputs_dir)
    if reprovados:
        for escopo, erros in reprovados.items():
            for e in erros:
                logger.error("Revalidação %s: %s", escopo, e)
        raise ErroPublicacao("Nada publicado; texto desatualizado em "
                             + ", ".join(reprovados) + "; regere o escopo.")

    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)
    for origem, rel in itens:
        destino = site_dir / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem, destino)
    escopos = [{"escopo": e, "nome": NOMES[e]} for e in ESCOPOS]
    (site_dir / "data" / "escopos.json").write_text(
        json.dumps(escopos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (site_dir / "README.md").write_text(README_SPACE, encoding="utf-8")

    ruins = verificar_site(site_dir)
    if ruins:
        shutil.rmtree(site_dir)
        raise ErroPublicacao("Conteúdo proibido no site (removido): " + ", ".join(ruins))

    arquivos = [{"arquivo": p.relative_to(site_dir).as_posix(), "sha256": sha256(p)}
                for p in sorted(site_dir.rglob("*")) if p.is_file()]
    manifesto = {
        "publicado_em": (agora or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "revisado_por": revisor,
        "arquivos": arquivos,
    }
    (site_dir / MANIFESTO).write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("Publicados %d arquivos em %s (revisado por %s)",
                len(arquivos), site_dir, revisor)
    return manifesto


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m src.publicar --revisado-por "Nome"``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Monta site/ (Space estático) com o que vai ao ar.")
    parser.add_argument("--revisado-por", required=True, help="Nome de quem revisou o conteúdo.")
    args = parser.parse_args(argv)
    try:
        publicar(args.revisado_por)
    except ErroPublicacao as erro:
        logger.error("%s", erro)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
