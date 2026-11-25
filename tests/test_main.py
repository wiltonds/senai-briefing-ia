"""Testes de app.main (rotas FastAPI)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def cliente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App apontando para um site/ temporário."""
    dados = tmp_path / "data"
    dados.mkdir()
    (dados / "BR.json").write_text(json.dumps({"meta": {"escopo": "BR"}}), encoding="utf-8")
    (dados / "regioes.json").write_text(json.dumps({"regioes": []}), encoding="utf-8")
    (tmp_path / "pdf").mkdir()
    (tmp_path / "pdf" / "Executive_Briefing_SENAI_BR.pdf").write_bytes(b"%PDF-1.4 teste")
    monkeypatch.setattr(main, "APP_DATA_DIR", dados)
    monkeypatch.setattr(main, "PDF_DIR", tmp_path / "pdf")
    return TestClient(main.app)


def test_pagina(cliente: TestClient) -> None:
    r = cliente.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_escopos(cliente: TestClient) -> None:
    r = cliente.get("/api/escopos").json()
    assert len(r) == 28 and r[0] == {"escopo": "BR", "nome": "Brasil"}


def test_escopo_valido_e_minusculo(cliente: TestClient) -> None:
    assert cliente.get("/api/escopo/BR").json()["meta"]["escopo"] == "BR"
    assert cliente.get("/api/escopo/br").status_code == 200


@pytest.mark.parametrize("escopo", ["XX", "DN", "..%2F..%2Fsecret", "BR.json", "regioes"])
def test_escopo_invalido_404(cliente: TestClient, escopo: str) -> None:
    assert cliente.get(f"/api/escopo/{escopo}").status_code == 404


def test_escopo_valido_sem_dado_404(cliente: TestClient) -> None:
    assert cliente.get("/api/escopo/AL").status_code == 404


def test_regioes(cliente: TestClient) -> None:
    assert cliente.get("/api/regioes").json() == {"regioes": []}


def test_pdf(cliente: TestClient) -> None:
    r = cliente.get("/api/pdf/BR")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert cliente.get("/api/pdf/AL").status_code == 404
    assert cliente.get("/api/pdf/XX").status_code == 404


def test_figs_nao_sai_do_diretorio(cliente: TestClient) -> None:
    assert cliente.get("/figs/..%2F..%2F.env").status_code == 404


@pytest.mark.parametrize("arquivo,tipo", [("app.js", "javascript"), ("styles.css", "text/css")])
def test_arquivos_estaticos(cliente: TestClient, arquivo: str, tipo: str) -> None:
    for url in (f"/static/{arquivo}", f"/{arquivo}"):
        r = cliente.get(url)
        assert r.status_code == 200 and tipo in r.headers["content-type"]


def test_pagina_referencia_js_e_css_relativos(cliente: TestClient) -> None:
    """Caminhos relativos: a mesma página funciona no FastAPI e no Space estático."""
    html = cliente.get("/").text
    assert 'src="app.js"' in html and 'href="styles.css"' in html
    assert "/static/" not in html


def test_escopos_iguais_ao_produto() -> None:
    from src import produto

    assert main.ESCOPOS == produto.ESCOPOS and main.NOMES == produto.NOMES


def test_app_le_so_de_site() -> None:
    assert main.SITE_DIR == main.RAIZ / "site"
    assert main.APP_DATA_DIR == main.SITE_DIR / "data"
    assert main.FIGS_DIR == main.SITE_DIR / "figs" and main.PDF_DIR == main.SITE_DIR / "pdf"


def test_app_nao_importa_src() -> None:
    """O app só depende de site/: importar app.main não pode puxar src."""
    import subprocess
    import sys

    codigo = ("import sys, app.main; "
              "assert not [m for m in sys.modules if m == 'src' or m.startswith('src.')]")
    r = subprocess.run([sys.executable, "-c", codigo], cwd=main.RAIZ, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
