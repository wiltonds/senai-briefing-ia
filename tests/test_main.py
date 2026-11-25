"""Testes de app.main (rotas FastAPI)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def cliente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App apontando para dados e PDF temporários."""
    dados = tmp_path / "app_data"
    dados.mkdir()
    (dados / "BR.json").write_text(json.dumps({"meta": {"escopo": "BR"}}), encoding="utf-8")
    (dados / "regioes.json").write_text(json.dumps({"regioes": []}), encoding="utf-8")
    (tmp_path / "Executive_Briefing_SENAI_BR.pdf").write_bytes(b"%PDF-1.4 teste")
    monkeypatch.setattr(main, "APP_DATA_DIR", dados)
    monkeypatch.setattr(main, "OUTPUTS_DIR", tmp_path)
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
    r = cliente.get(f"/static/{arquivo}")
    assert r.status_code == 200 and tipo in r.headers["content-type"]


def test_pagina_referencia_js_e_css(cliente: TestClient) -> None:
    html = cliente.get("/").text
    assert "/static/app.js" in html and "/static/styles.css" in html
