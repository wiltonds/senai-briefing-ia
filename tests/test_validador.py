"""Testes de src.validador."""

import copy

import pytest

from src.validador import tokens_numericos, validar

FATOS = [
    {"id": "F001", "categoria": "resultado_nacional",
     "frase": "Indicador 1 (Taxa de ocupação), 2025: meta 45,0%, realizado 39,8%, índice 88,4%; farol atenção."},
    {"id": "F002", "categoria": "assimetria",
     "frase": "10 de 27 DRs têm 4 ou mais indicadores críticos (AC, AL, AM)."},
    {"id": "F003", "categoria": "projecao_2026",
     "frase": "Receita projetada de R$ 1,06 bi para 2026; ritmo 1,2x o de 2025."},
    {"id": "F004", "categoria": "qualidade",
     "frase": "12 de 12 valores nacionais de 2025 confirmados no Relatório de Gestão."},
]


def _briefing() -> dict:
    item = lambda t, f: {"titulo": "Destaque", "texto": t, "fatos": f}  # noqa: E731
    rec = lambda f: {  # noqa: E731
        "titulo": "Apoio", "acao": "DN e DR revisam o plano de ação em 2026.",
        "responsavel": "DN e DR", "horizonte": "imediato (4º tri/2026)", "fatos": f,
    }
    return {
        "mensagem_central": {"texto": "A ocupação de egressos ficou em 39,8%, abaixo da meta de 45,0%.",
                             "fatos": ["F001"]},
        "destaques": [
            item("Receita projetada de R$ 1,06 bi.", ["F003"]),
            item("Ritmo de 1,2x o de 2025.", ["F003"]),
            item("12 de 12 valores confirmados.", ["F004"]),
        ],
        "pontos_atencao": [
            item("10 de 27 DRs, entre eles AC e AL, têm 4 ou mais indicadores críticos.", ["F002"]),
            item("Índice de 88,4% no indicador 1.", ["F001"]),
            item("Hipótese: a ocupação caiu devido ao mercado.", ["F001"]),
        ],
        "recomendacoes": [rec(["F001"]), rec(["F002"]), rec(["F003"])],
        "limitacoes": [{"texto": "Dados de 2026 ainda parciais.", "fatos": ["F003"]}],
    }


def test_briefing_valido_passa():
    assert validar(_briefing(), FATOS) == []


def test_numero_inventado_rejeitado():
    b = _briefing()
    b["destaques"][0]["texto"] = "A ocupação chegou a 41,2% no ano."
    erros = validar(b, FATOS)
    assert any("41,2" in e and "destaques[1]" in e for e in erros)


def test_numero_de_outro_fato_rejeitado():
    b = _briefing()
    b["destaques"][0]["texto"] = "Realizado de 39,8%."  # está em F001, mas o item cita F003
    assert any("39,8" in e for e in validar(b, FATOS))


def test_id_inexistente_rejeitado():
    b = _briefing()
    b["destaques"][1]["fatos"] = ["F003", "F999"]
    assert any("F999" in e for e in validar(b, FATOS))


def test_item_sem_fato_rejeitado():
    b = _briefing()
    b["limitacoes"][0]["fatos"] = []
    assert any("limitacoes[1]" in e and "não cita" in e for e in validar(b, FATOS))


def test_uf_fora_do_fato_rejeitada():
    b = _briefing()
    b["pontos_atencao"][0]["texto"] = "10 de 27 DRs, entre eles AC e SP, têm 4 ou mais indicadores críticos."
    erros = validar(b, FATOS)
    assert any("'SP'" in e for e in erros)
    assert not any("'AC'" in e for e in erros)


def test_causa_sem_hipotese_rejeitada():
    b = _briefing()
    b["pontos_atencao"][2]["texto"] = "A ocupação caiu devido ao mercado."
    assert any("hipótese" in e and "devido" in e for e in validar(b, FATOS))


@pytest.mark.parametrize("termo", ["farol", "CSV", "status_dado", "valor_txt"])
def test_jargao_rejeitado(termo):
    b = _briefing()
    b["destaques"][2]["texto"] = f"12 de 12 valores confirmados no {termo}."
    assert any("termo técnico" in e for e in validar(b, FATOS))


def test_esquema_quantidade_e_valores_permitidos():
    b = _briefing()
    b["destaques"] = b["destaques"][:2]
    b["recomendacoes"][0]["responsavel"] = "Superintendente"
    b["recomendacoes"][1]["horizonte"] = "2027"
    erros = validar(b, FATOS)
    assert any("destaques" in e for e in erros)
    assert any("responsavel" in e for e in erros)
    assert any("horizonte" in e for e in erros)


def test_esquema_campo_faltando():
    b = _briefing()
    del b["mensagem_central"]
    assert any("mensagem_central" in e for e in validar(b, FATOS))


def test_limite_de_palavras():
    b = _briefing()
    b["destaques"][0]["texto"] = " ".join(["palavra"] * 46)
    assert any("palavras" in e for e in validar(b, FATOS))


def test_tokens_numericos():
    assert tokens_numericos("meta 45,0%, R$ 1,06 bi, 1,2x, total 7.476.") == {"45,0", "1,06", "1,2", "7.476"}
    assert tokens_numericos("F001 e 2025-2026") == {"2025", "2026"}
    assert tokens_numericos("variação de -3,5%") == {"-3,5"}


def test_nao_altera_entrada():
    b = _briefing()
    original = copy.deepcopy(b)
    validar(b, FATOS)
    assert b == original
