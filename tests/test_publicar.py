"""Testes de src.publicar (site/ estático do Hugging Face Space)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import publicar
from src.produto import ESCOPOS


@pytest.fixture
def static(tmp_path: Path) -> Path:
    """app/static falso."""
    s = tmp_path / "static"
    s.mkdir()
    for nome in publicar.ARQUIVOS_PAGINA:
        (s / nome).write_text(nome, encoding="utf-8")
    return s


@pytest.fixture
def outputs(tmp_path: Path) -> Path:
    """outputs/ falso com o que vai ao ar e iscas que não podem ir."""
    out = tmp_path / "outputs"
    (out / "app_data").mkdir(parents=True)
    (out / "figs").mkdir()
    for e in ESCOPOS:
        (out / "app_data" / f"{e}.json").write_text('{"meta": {}}', encoding="utf-8")
    (out / "app_data" / "regioes.json").write_text("{}", encoding="utf-8")
    (out / "figs" / "g_AL.png").write_bytes(b"png")
    (out / "Executive_Briefing_SENAI_BR.pdf").write_bytes(b"%PDF")
    # iscas
    (out / "briefing_BR.json").write_text("{}", encoding="utf-8")
    (out / "figs" / "rascunho.svg").write_text("x", encoding="utf-8")
    (out / "app_data" / "notas.txt").write_text("x", encoding="utf-8")
    (out / "Executive_Briefing_SENAI_XX.pdf").write_bytes(b"%PDF")
    (out / ".env").write_text("SEGREDO=1", encoding="utf-8")
    for pasta in ("logs", "src", "tests", "raw"):
        (out / pasta).mkdir()
        (out / pasta / "x.py").write_text("x", encoding="utf-8")
    return out


def _arquivos(site: Path) -> list[str]:
    return sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())


def test_monta_site_e_manifesto(outputs: Path, static: Path, tmp_path: Path) -> None:
    site = tmp_path / "site"
    agora = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    m = publicar.publicar("  Fulana  ", outputs, site, static, agora)

    esperado = sorted(
        ["index.html", "app.js", "styles.css", "README.md", "MANIFESTO.json",
         "data/regioes.json", "data/escopos.json", "figs/g_AL.png",
         "pdf/Executive_Briefing_SENAI_BR.pdf"]
        + [f"data/{e}.json" for e in ESCOPOS])
    assert _arquivos(site) == esperado
    assert json.loads((site / "MANIFESTO.json").read_text(encoding="utf-8")) == m
    assert m["revisado_por"] == "Fulana" and m["publicado_em"] == "2026-11-25T12:00:00+00:00"
    hashes = {a["arquivo"]: a["sha256"] for a in m["arquivos"]}
    assert set(hashes) == set(esperado) - {"MANIFESTO.json"}
    assert hashes["figs/g_AL.png"] == publicar.sha256(site / "figs" / "g_AL.png")
    assert len(hashes["figs/g_AL.png"]) == 64


def test_escopos_json(outputs: Path, static: Path, tmp_path: Path) -> None:
    site = tmp_path / "site"
    publicar.publicar("A", outputs, site, static)
    lista = json.loads((site / "data" / "escopos.json").read_text(encoding="utf-8"))
    assert len(lista) == 28 and lista[0] == {"escopo": "BR", "nome": "Brasil"}


def test_readme_do_space(outputs: Path, static: Path, tmp_path: Path) -> None:
    site = tmp_path / "site"
    publicar.publicar("A", outputs, site, static)
    texto = (site / "README.md").read_text(encoding="utf-8")
    assert texto.startswith("---\ntitle: Briefing Executivo SENAI\n")
    assert "sdk: static" in texto and "app_port" not in texto


def test_site_sem_conteudo_proibido(outputs: Path, static: Path, tmp_path: Path) -> None:
    site = tmp_path / "site"
    publicar.publicar("A", outputs, site, static)
    assert publicar.verificar_site(site) == []
    for rel in _arquivos(site):
        partes = rel.lower().split("/")
        assert ".env" not in partes and not rel.endswith((".py", ".log"))
        assert not {"src", "tests", "logs", "raw"} & set(partes)
        assert not rel.startswith("data/raw")


def test_verificar_site_detecta_proibidos(tmp_path: Path) -> None:
    site = tmp_path / "site"
    (site / "data" / "raw").mkdir(parents=True)
    (site / "src").mkdir()
    (site / "logs").mkdir()
    (site / "tests").mkdir()
    (site / ".env").write_text("x", encoding="utf-8")
    (site / "x.py").write_text("x", encoding="utf-8")
    ruins = publicar.verificar_site(site)
    for esperado in (".env", "data/raw", "src", "logs", "tests", "x.py"):
        assert esperado in ruins


def test_site_versionado_sem_conteudo_proibido() -> None:
    """O site/ do repositório (se já gerado) também não pode ter nada proibido."""
    if not publicar.SITE_DIR.exists():
        pytest.skip("site/ ainda não gerado")
    assert publicar.verificar_site(publicar.SITE_DIR) == []


def test_republicar_remove_arquivo_antigo(outputs: Path, static: Path, tmp_path: Path) -> None:
    site = tmp_path / "site"
    publicar.publicar("A", outputs, site, static)
    (outputs / "figs" / "g_AL.png").unlink()
    publicar.publicar("B", outputs, site, static)
    assert not (site / "figs" / "g_AL.png").exists()


def test_recusa_sem_revisor(outputs: Path, static: Path, tmp_path: Path) -> None:
    with pytest.raises(publicar.ErroPublicacao):
        publicar.publicar("   ", outputs, tmp_path / "site", static)
    assert not (tmp_path / "site").exists()


def test_recusa_se_faltar_escopo(outputs: Path, static: Path, tmp_path: Path) -> None:
    (outputs / "app_data" / "TO.json").unlink()
    with pytest.raises(publicar.ErroPublicacao, match="TO.json"):
        publicar.publicar("A", outputs, tmp_path / "site", static)
    assert not (tmp_path / "site").exists()


def test_cli_exige_revisado_por() -> None:
    with pytest.raises(SystemExit):
        publicar.main([])


def test_copia_perguntas_existentes(outputs: Path, static: Path, tmp_path: Path) -> None:
    (outputs / "app_data" / "perguntas_BR.json").write_text('{"perguntas": []}', encoding="utf-8")
    (outputs / "app_data" / "perguntas_XX.json").write_text("{}", encoding="utf-8")  # escopo inválido
    site = tmp_path / "site"
    m = publicar.publicar("A", outputs, site, static)
    arquivos = _arquivos(site)
    assert "data/perguntas_BR.json" in arquivos and "data/perguntas_XX.json" not in arquivos
    assert "data/perguntas_BR.json" in {a["arquivo"] for a in m["arquivos"]}
