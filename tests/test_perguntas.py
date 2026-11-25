"""Testes de src.perguntas — nunca chamam a API: usam um cliente falso."""

import json

import pytest

from src import perguntas as P
from tests.test_redator import ClienteFalso
from tests.test_validador import FATOS

FRASES = {f["id"]: f["frase"] for f in FATOS}
PERGUNTA = "Qual é o principal problema hoje?"


def _item(resposta: str, fatos: list[str], pid: str = "P1", pergunta: str = PERGUNTA) -> dict:
    return {"id": pid, "pergunta": pergunta, "resposta": resposta, "fatos": fatos}


def _todas(escopo: str = "BR", **sobrescreve: dict) -> list[dict]:
    base = [_item("A ocupação de egressos ficou em 39,8%, abaixo da meta de 45,0%.", ["F001"], pid, p)
            for pid, p in P.catalogo(escopo)]
    return [sobrescreve.get(i["id"], i) for i in base]


# ---------------------------------------------------------------- validação
def test_resposta_valida():
    assert P.validar_resposta(_item("Ocupação em 39,8% frente à meta de 45,0% em 2025.", ["F001"]),
                              PERGUNTA, FRASES) == []


def test_numero_fora_dos_fatos_citados_reprova():
    erros = P.validar_resposta(_item("Ocupação em 39,8%, meta 50%.", ["F001"]), PERGUNTA, FRASES)
    assert any("'50'" in e for e in erros)


def test_numero_de_outro_fato_nao_citado_reprova():
    erros = P.validar_resposta(_item("Receita de R$ 1,06 bi.", ["F001"]), PERGUNTA, FRASES)
    assert any("'1,06'" in e for e in erros)


def test_fato_inexistente_reprova():
    erros = P.validar_resposta(_item("Ocupação em 39,8%.", ["F001", "F999"]), PERGUNTA, FRASES)
    assert any("F999" in e for e in erros)


def test_sem_fato_reprova():
    assert P.validar_resposta(_item("Ocupação abaixo da meta.", []), PERGUNTA, FRASES)


def test_recusa_valida_e_recusa_com_fato_reprova():
    assert P.validar_resposta(_item(P.RECUSA, []), PERGUNTA, FRASES) == []
    assert P.validar_resposta(_item(P.RECUSA, ["F001"]), PERGUNTA, FRASES)


def test_regras_do_validador_reaproveitadas():
    casos = [
        "A ocupação caiu devido à crise.",            # causal sem hipótese
        "O farol da ocupação está em atenção.",       # jargão
        "O indicador 1 ficou abaixo da meta.",        # código de indicador
        "SP e AC precisam de apoio.",                 # UF fora dos fatos (SP)
    ]
    for texto in casos:
        assert P.validar_resposta(_item(texto, ["F001", "F002"]), PERGUNTA, FRASES), texto


def test_limite_de_palavras():
    erros = P.validar_resposta(_item("palavra " * 71, ["F001"]), PERGUNTA, FRASES)
    assert any("71 palavras" in e for e in erros)


@pytest.mark.parametrize("item", [
    "texto solto",
    {"id": "P1", "pergunta": PERGUNTA, "resposta": "x"},                         # falta fatos
    {**_item("Ocupação.", ["F001"]), "extra": 1},                                # campo extra
    _item("Ocupação.", "F001"),                                                  # fatos não é lista
    _item("Ocupação.", ["F001"], pergunta="Outra pergunta?"),                    # pergunta alterada
])
def test_schema_reprova(item):
    assert P.validar_resposta(item, PERGUNTA, FRASES)


def test_catalogo_dr_troca_pergunta_2():
    assert dict(P.catalogo("AL"))["P2"] == "Como meu DR está frente ao Brasil?"
    assert len(P.catalogo("BR")) == len(P.catalogo("AL")) == 6


# ---------------------------------------------------------------- orquestração
@pytest.fixture
def dirs(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "fatos_BR.json").write_text(json.dumps({"escopo": "BR", "fatos": [
        {**f, "fonte": {"tipo": "api", "arquivo": "x.json"}} for f in FATOS]}, ensure_ascii=False),
        encoding="utf-8")
    prompt = tmp_path / "perguntas_v1.md"
    prompt.write_text("Prompt de teste.", encoding="utf-8")
    return {"outputs_dir": outputs, "app_data_dir": outputs / "app_data",
            "logs_dir": tmp_path / "logs", "prompt_path": prompt}


def test_gera_tudo_aprovado_com_fonte(dirs):
    cliente = ClienteFalso([json.dumps(_todas(), ensure_ascii=False)])
    doc = P.gerar_perguntas("BR", cliente, **dirs)
    assert len(cliente.chamadas) == 1 and len(doc["perguntas"]) == 6 and doc["removidas"] == []
    salvo = json.loads((dirs["app_data_dir"] / "perguntas_BR.json").read_text(encoding="utf-8"))
    fato = salvo["perguntas"][0]["fatos"][0]
    assert fato == {"id": "F001", "texto": FRASES["F001"], "fonte": {"tipo": "api", "arquivo": "x.json"}}
    log = json.loads(next(dirs["logs_dir"].glob("perguntas_BR_*.json")).read_text(encoding="utf-8"))
    assert {"prompt_sha256", "fatos_sha256", "saida_sha256"} <= set(log)


def test_retry_corrige_so_a_reprovada(dirs):
    ruim = _item("Meta de 99%.", ["F001"], "P3", "Vamos bater as metas de 2026?")
    boa = _item("Receita projetada de R$ 1,06 bi.", ["F003"], "P3", "Vamos bater as metas de 2026?")
    cliente = ClienteFalso([json.dumps(_todas(P3=ruim), ensure_ascii=False), json.dumps([boa], ensure_ascii=False)])
    doc = P.gerar_perguntas("BR", cliente, **dirs)
    assert len(cliente.chamadas) == 2
    assert [p["id"] for p in doc["perguntas"]] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert "P3" in cliente.chamadas[1]["messages"][-1]["content"]


def test_falha_apos_3_tentativas_remove_pergunta(dirs):
    ruim = _item("Fato inexistente.", ["F999"], "P2", "Quais DRs precisam de apoio prioritário?")
    cliente = ClienteFalso([json.dumps(_todas(P2=ruim), ensure_ascii=False)] + [json.dumps([ruim])] * 2)
    doc = P.gerar_perguntas("BR", cliente, **dirs)
    assert len(cliente.chamadas) == 3
    assert doc["removidas"] == ["P2"]
    assert "P2" not in [p["id"] for p in doc["perguntas"]]


def test_json_invalido_nao_publica_nada(dirs):
    doc = P.gerar_perguntas("BR", ClienteFalso(["sem json"] * 3), **dirs)
    assert doc["perguntas"] == [] and len(doc["removidas"]) == 6


def test_recusa_publicada_sem_fatos(dirs):
    rec = _item(P.RECUSA, [], "P5", "Quão confiáveis são esses números?")
    doc = P.gerar_perguntas("BR", ClienteFalso([json.dumps(_todas(P5=rec), ensure_ascii=False)]), **dirs)
    p5 = next(p for p in doc["perguntas"] if p["id"] == "P5")
    assert p5["resposta"] == P.RECUSA and p5["fatos"] == []


def test_dry_run_nao_chama_api(dirs):
    info = P.dry_run("BR", outputs_dir=dirs["outputs_dir"], prompt_path=dirs["prompt_path"])
    assert info["n_fatos"] == len(FATOS) and info["n_perguntas"] == 6
