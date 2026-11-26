"""App web: FastAPI servindo a página HTML e os dados do produto.

Rotas:
- ``GET /``: página (``app/static/index.html``).
- ``GET /api/escopos``: BR e os 27 DRs, com nome por extenso.
- ``GET /api/escopo/{escopo}``: ``site/data/{escopo}.json`` (escopo validado).
- ``GET /api/regioes``: ``site/data/regioes.json``.
- ``GET /api/perguntas/{escopo}``: ``site/data/perguntas_{escopo}.json`` (404 se não houver).
- ``GET /api/pdf/{escopo}``: ``site/pdf/Executive_Briefing_SENAI_{escopo}.pdf``, se existir.
- ``/figs``: arquivos de ``site/figs``.
- ``GET /app.js``, ``GET /styles.css`` (e ``/static``): arquivos da página.

Uso local. Em produção o Space é estático (``site/``, sem backend) e o
``app.js`` lê ``data/``, ``figs/`` e ``pdf/`` por caminho relativo.

Segurança: nenhuma rota recebe caminho de arquivo; o escopo é validado contra a
lista fixa (BR + 27 UFs) antes de montar qualquer caminho. Lê SÓ de
``site/`` (gerado por ``python -m src.publicar``) e não importa ``src/``.

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

logger = logging.getLogger("app")

RAIZ = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SITE_DIR = RAIZ / "site"
APP_DATA_DIR = SITE_DIR / "data"
FIGS_DIR = SITE_DIR / "figs"
PDF_DIR = SITE_DIR / "pdf"

# Cópia de src.produto.ESCOPOS/NOMES (o app não importa src/); o teste garante que batem.
NOMES: dict[str, str] = {
    "BR": "Brasil", "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo",
    "GO": "Goiás", "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso", "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí",
    "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia",
    "RR": "Roraima", "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe",
    "SP": "São Paulo", "TO": "Tocantins",
}
ESCOPOS: tuple[str, ...] = ("BR",) + tuple(sorted(u for u in NOMES if u != "BR"))

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
        logger.warning("Dado ausente: %s (rode python -m src.publicar)", caminho)
        raise HTTPException(status_code=404, detail="Dados não gerados")
    return json.loads(caminho.read_text(encoding="utf-8"))


@app.get("/", include_in_schema=False)
def pagina() -> FileResponse:
    """Página do produto."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/app.js", include_in_schema=False)
def app_js() -> FileResponse:
    """Script da página (referência relativa, igual ao site estático)."""
    return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript")


@app.get("/styles.css", include_in_schema=False)
def styles_css() -> FileResponse:
    """Estilos da página (referência relativa, igual ao site estático)."""
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")


@app.get("/api/escopos")
def escopos() -> Any:
    """Escopos disponíveis (BR primeiro, DRs em ordem alfabética), com ``tem_briefing``.

    Lê ``site/data/escopos.json`` (gerado por ``src.publicar``); se ausente, devolve a lista
    sem ``tem_briefing``.
    """
    if (APP_DATA_DIR / "escopos.json").exists():
        return ler_json("escopos")
    return [{"escopo": e, "nome": NOMES[e]} for e in ESCOPOS]


@app.get("/api/escopo/{escopo}")
def dados_escopo(escopo: str) -> Any:
    """Dados de um escopo."""
    return ler_json(validar_escopo(escopo))


@app.get("/api/regioes")
def regioes() -> Any:
    """Resumo por região."""
    return ler_json("regioes")


@app.get("/api/perguntas/{escopo}")
def perguntas(escopo: str) -> Any:
    """Perguntas verificadas do escopo (respostas pré-geradas e validadas)."""
    return ler_json(f"perguntas_{validar_escopo(escopo)}")


@app.get("/api/pdf/{escopo}")
def pdf(escopo: str) -> FileResponse:
    """PDF executivo do escopo."""
    e = validar_escopo(escopo)
    caminho = PDF_DIR / f"Executive_Briefing_SENAI_{e}.pdf"
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="PDF não gerado para este escopo")
    return FileResponse(caminho, media_type="application/pdf", filename=caminho.name)
