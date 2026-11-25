"""Testes de src.redator — nunca chamam a API: usam um cliente falso."""

import json
from types import SimpleNamespace

import pytest

from src import redator
from tests.test_validador import FATOS, _briefing


class ClienteFalso:
    """Imita ``anthropic.Anthropic`` devolvendo respostas pré-definidas."""

    def __init__(self, respostas: list[str]):
        self.respostas = list(respostas)
        self.chamadas: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.chamadas.append(kwargs)
        texto = self.respostas.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=texto)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


@pytest.fixture
def dirs(tmp_path):
    outputs, logs = tmp_path / "outputs", tmp_path / "logs"
    outputs.mkdir()
    (outputs / "fatos_BR.json").write_text(
        json.dumps({"escopo": "BR", "fatos": FATOS}, ensure_ascii=False), encoding="utf-8"
    )
    prompt = tmp_path / "briefing_v1.md"
    prompt.write_text("Prompt de teste.", encoding="utf-8")
    return {"outputs_dir": outputs, "logs_dir": logs, "prompt_path": prompt}


def _invalido() -> str:
    b = _briefing()
    b["destaques"][0]["texto"] = "A ocupação chegou a 41,2% no ano."
    return json.dumps(b, ensure_ascii=False)


def _valido() -> str:
    return "```json\n" + json.dumps(_briefing(), ensure_ascii=False) + "\n```"


def _log(dirs) -> dict:
    (arquivo,) = dirs["logs_dir"].glob("geracao_BR_*.json")
    return json.loads(arquivo.read_text(encoding="utf-8"))


def test_aprovado_na_primeira(dirs):
    cliente = ClienteFalso([_valido()])
    doc = redator.gerar_briefing("BR", cliente, **dirs)
    assert doc["status"] == "aprovado"
    assert doc["versao_prompt"] == "v1"
    assert len(cliente.chamadas) == 1
    msg = cliente.chamadas[0]["messages"][0]["content"]
    assert "<fatos>" in msg and "</fatos>" in msg and "F001" in msg
    assert cliente.chamadas[0]["max_tokens"] == 6000
    salvo = json.loads((dirs["outputs_dir"] / "briefing_BR.json").read_text(encoding="utf-8"))
    assert salvo["status"] == "aprovado" and salvo["modelo"] == redator.MODELO_PADRAO


def test_reenvio_corrige_na_segunda(dirs):
    cliente = ClienteFalso([_invalido(), _valido()])
    doc = redator.gerar_briefing("BR", cliente, **dirs)
    assert doc["status"] == "aprovado"
    assert doc["tentativas"] == 2
    segunda = cliente.chamadas[1]["messages"]
    assert [m["role"] for m in segunda] == ["user", "assistant", "user"]
    assert "41,2" in segunda[-1]["content"]
    log = _log(dirs)
    assert log["tentativas"][0]["erros"] and log["tentativas"][1]["erros"] == []
    assert log["tentativas"][0]["resposta_bruta"] == _invalido()
    assert log["uso_total"]["input_tokens"] == 200
    assert len(log["prompt_sha256"]) == 64 and len(log["fatos_sha256"]) == 64


def test_tres_falhas_contingencia(dirs):
    cliente = ClienteFalso([_invalido(), "sem json aqui", _invalido()])
    doc = redator.gerar_briefing("BR", cliente, **dirs)
    assert doc["status"] == "contingencia"
    assert len(cliente.chamadas) == 3
    assert doc["briefing"] is None
    grupos = doc["fatos_por_categoria"]
    assert grupos["resultado_nacional"][0] == {"id": "F001", "frase": FATOS[0]["frase"]}
    assert set(grupos) == {f["categoria"] for f in FATOS}
    assert len(_log(dirs)["tentativas"]) == 3


def test_extrair_json():
    assert redator.extrair_json('texto {"a": 1} fim') == {"a": 1}
    with pytest.raises(ValueError):
        redator.extrair_json("nada")


def test_dry_run_nao_chama_api(dirs):
    info = redator.dry_run("BR", outputs_dir=dirs["outputs_dir"], prompt_path=dirs["prompt_path"])
    assert info["n_fatos"] == len(FATOS) and info["tokens_estimados"] > 0


# ---------------------------------------------------------------- --prompt
def test_caminho_prompt_por_versao(tmp_path):
    (tmp_path / "briefing_v2.md").write_text("x", encoding="utf-8")
    assert redator.caminho_prompt("v2", tmp_path) == tmp_path / "briefing_v2.md"
    with pytest.raises(FileNotFoundError):
        redator.caminho_prompt("v9", tmp_path)


def test_padrao_e_v2_e_max_tokens():
    assert redator.VERSAO_PROMPT_PADRAO == "v2"
    assert redator.PROMPT_PATH.name == "briefing_v2.md"
    assert redator.MAX_TOKENS == 6000


def test_versao_do_prompt_no_briefing_e_no_log(dirs):
    v2 = dirs["prompt_path"].with_name("briefing_v2.md")
    v2.write_text("Prompt v2.", encoding="utf-8")
    doc = redator.gerar_briefing("BR", ClienteFalso([_valido()]), **{**dirs, "prompt_path": v2})
    assert doc["versao_prompt"] == "v2"
    assert _log(dirs)["versao_prompt"] == "v2"


def test_main_repassa_prompt(monkeypatch, tmp_path):
    (tmp_path / "briefing_v1.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(redator, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(redator.caminho_prompt, "__defaults__", (tmp_path,))
    chamado = {}
    monkeypatch.setattr(redator, "dry_run", lambda escopo, prompt_path: chamado.update(p=prompt_path))
    redator.main(["--escopo", "br", "--dry-run", "--prompt", "v1"])
    assert chamado["p"] == tmp_path / "briefing_v1.md"
    with pytest.raises(SystemExit):
        redator.main(["--escopo", "br", "--dry-run", "--prompt", "v9"])
