"""App web: FastAPI servindo a página HTML e os dados do produto.

Rotas:
- ``GET /``: página (``app/static/index.html``).
- ``GET /api/escopos``: BR e os 27 DRs, com nome por extenso.
- ``GET /api/escopo/{escopo}``: ``outputs/app_data/{escopo}.json`` (escopo validado).
- ``GET /api/regioes``: ``outputs/app_data/regioes.json``.
- ``GET /api/pdf/{escopo}``: PDF executivo do escopo, se existir.
- ``/figs``: arquivos de ``outputs/figs``.
- ``/static``: ``app.js`` e ``styles.css`` da página.

Segurança: nenhuma rota recebe caminho de arquivo; o escopo é validado contra a
lista fixa (BR + 27 UFs) antes de montar qualquer caminho. Os dados vêm de
``python -m src.produto`` (este módulo só lê).

Uso:
    uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.produto import ESCOPOS, NOMES

logger = logging.getLogger("app")

RAIZ = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
OUTPUTS_DIR = RAIZ / "outputs"
APP_DATA_DIR = OUTPUTS_DIR / "app_data"
FIGS_DIR = OUTPUTS_DIR / "figs"

app = FastAPI(title="Inteligência de Gestão SENAI", docs_url=None, redoc_url=None)
app.mount("/figs", StaticFiles(directory=FIGS_DIR, check_dir=False), name="figs")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def validar_escopo(escopo: str) -> str:
    """Escopo em maiúsculas, se estiver na lista fixa; senão 404."""
    e = escopo.upper()
    if e not in ESCOPOS:
        raise HTTPException(status_code=404, detail="Escopo desconhecido")
    return e


def ler_json(nome: str) -> Any:
    """Lê ``APP_DATA_DIR/{nome}.json`` (nome já validado); 404 se ausente."""
    caminho = APP_DATA_DIR / f"{nome}.json"
    if not caminho.exists():
        logger.warning("Dado ausente: %s (rode python -m src.produto)", caminho)
        raise HTTPException(status_code=404, detail="Dados não gerados")
    return json.loads(caminho.read_text(encoding="utf-8"))


@app.get("/", include_in_schema=False)
def pagina() -> FileResponse:
    """Página do produto."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/escopos")
def escopos() -> list[dict[str, str]]:
    """Escopos disponíveis (BR primeiro, DRs em ordem alfabética)."""
    return [{"escopo": e, "nome": NOMES[e]} for e in ESCOPOS]


@app.get("/api/escopo/{escopo}")
def dados_escopo(escopo: str) -> Any:
    """Dados de um escopo."""
    return ler_json(validar_escopo(escopo))


@app.get("/api/regioes")
def regioes() -> Any:
    """Resumo por região."""
    return ler_json("regioes")


@app.get("/api/pdf/{escopo}")
def pdf(escopo: str) -> FileResponse:
    """PDF executivo do escopo."""
    e = validar_escopo(escopo)
    caminho = OUTPUTS_DIR / f"Executive_Briefing_SENAI_{e}.pdf"
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="PDF não gerado para este escopo")
    return FileResponse(caminho, media_type="application/pdf", filename=caminho.name)
