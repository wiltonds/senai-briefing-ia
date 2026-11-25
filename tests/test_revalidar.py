"""Testes de src.revalidar (textos publicados x ficha de fatos atual)."""

import json
from pathlib import Path

import pytest

from src import publicar, revalidar
from src.perguntas import catalogo
from src.produto import ESCOPOS


def _gravar(caminho: Path, obj: object) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _ficha(outputs: Path, escopo: str, valor: str) -> None:
    _gravar(outputs / f"fatos_{escopo}.json", {"fatos": [
        {"id": "F001", "categoria": "resultado",
         "frase": f"Em AL, a ocupação dos egressos foi {valor}% em 2025."},
    ]})


def _item(texto: str) -> dict:
    return {"titulo": "Ocupação", "texto": texto, "fatos": ["F001"]}


def _briefing(escopo: str, status: str = "aprovado") -> dict:
    texto = "AL registrou 77,3% de ocupação dos egressos em 2025."
    rec = {"titulo": "Acompanhar", "acao": "Acompanhar a ocupação dos egressos em AL.",
           "responsavel": "DR", "horizonte": "próximo ciclo de planejamento", "fatos": ["F001"]}
    return {"escopo": escopo, "status": status, "briefing": {
        "mensagem_central": {"texto": texto, "fatos": ["F001"]},
        "destaques": [_item(texto)] * 3,
        "pontos_atencao": [_item(texto)] * 3,
        "recomendacoes": [rec] * 3,
        "limitacoes": [],
    }}


def _perguntas(escopo: str) -> dict:
    pid, pergunta = catalogo(escopo)[0]
    return {"escopo": escopo, "removidas": [], "perguntas": [{
        "id": pid, "pergunta": pergunta,
        "resposta": "Em AL, a ocupação dos egressos foi 77,3% em 2025.",
        "fatos": [{"id": "F001", "texto": "...", "fonte": "..."}],  # formato publicado
    }]}


@pytest.fixture
def outputs(tmp_path: Path) -> Path:
    out = tmp_path / "outputs"
    _gravar(out / "briefing_AL.json", _briefing("AL"))
    _gravar(out / "app_data" / "perguntas_AL.json", _perguntas("AL"))
    return out


def test_aprova_textos_conferem_com_ficha_atual(outputs: Path) -> None:
    _ficha(outputs, "AL", "77,3")
    assert revalidar.revalidar(outputs) == {}


def test_reprova_numero_desatualizado_por_escopo(outputs: Path) -> None:
    _ficha(outputs, "AL", "77,2")  # ficha corrigida depois da geração
    reprovados = revalidar.revalidar(outputs)
    assert list(reprovados) == ["AL"]
    erros = reprovados["AL"]
    assert any("briefing_AL.json" in e and "'77,3'" in e for e in erros)
    assert any("perguntas_AL.json" in e and "'77,3'" in e for e in erros)


def test_reprova_sem_ficha_atual(outputs: Path) -> None:
    reprovados = revalidar.revalidar(outputs)
    assert any("ficha de fatos atual ausente" in e for e in reprovados["AL"])


def test_ignora_contingencia_e_escopo_nao_publicavel(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _gravar(out / "briefing_AL.json", {"escopo": "AL", "status": "contingencia", "briefing": None})
    _gravar(out / "briefing_XX.json", _briefing("XX"))  # não vai ao ar
    assert "XX" not in ESCOPOS
    assert revalidar.revalidar(out) == {}


def test_cli_codigo_de_saida(outputs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(revalidar, "OUTPUTS_DIR", outputs)
    _ficha(outputs, "AL", "77,3")
    assert revalidar.main([]) == 0
    _ficha(outputs, "AL", "77,2")
    assert revalidar.main([]) == 1


def test_publicar_aborta_com_texto_desatualizado(outputs: Path, tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    for nome in publicar.ARQUIVOS_PAGINA:
        (static / nome).write_text(nome, encoding="utf-8")
    for e in (*ESCOPOS, "regioes"):
        _gravar(outputs / "app_data" / f"{e}.json", {})
    site = tmp_path / "site"

    _ficha(outputs, "AL", "77,2")
    with pytest.raises(publicar.ErroPublicacao, match="texto desatualizado em AL; regere o escopo"):
        publicar.publicar("Revisor", outputs, site, static)
    assert not site.exists()

    _ficha(outputs, "AL", "77,3")  # escopo regerado / ficha conferida
    publicar.publicar("Revisor", outputs, site, static)
    assert (site / "data" / "perguntas_AL.json").is_file()
