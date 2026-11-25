"""Testes de src.normaliza."""

import pytest

from src.normaliza import (
    divergencia_indice,
    indice_analise,
    mes_referencia,
    parse_numero_br,
    status_dado,
)


@pytest.mark.parametrize(
    ("txt", "esperado"),
    [
        ("R$ - 0", 0.0),
        ("-R$ 2.522.387,69", -2522387.69),
        ("45,0%", 45.0),
        ("1.635", 1635.0),
        ("2824", 2824.0),
        ("R$ 1.935.884,31", 1935884.31),
        ("R$    1.129.294.509,62", 1129294509.62),  # espaçamento real do portal
    ],
)
def test_parse_numero_br_valores(txt: str, esperado: float) -> None:
    assert parse_numero_br(txt) == pytest.approx(esperado)


@pytest.mark.parametrize("txt", ["-", "", "  ", None])
def test_parse_numero_br_sem_dado_retorna_none(txt: str | None) -> None:
    resultado = parse_numero_br(txt)
    assert resultado is None  # nunca zero


def test_parse_numero_br_zero_nao_e_negativo() -> None:
    assert str(parse_numero_br("R$ - 0")) == "0.0"


@pytest.mark.parametrize("txt", ["abc", "1.5", "12,3,4", "--5", "-R$ -5", "N/A"])
def test_parse_numero_br_texto_invalido_levanta_erro(txt: str) -> None:
    with pytest.raises(ValueError, match=f"{txt!r}".replace("$", r"\$")):
        parse_numero_br(txt)


@pytest.mark.parametrize(
    ("meta", "realizado", "meta_txt", "realizado_txt", "esperado"),
    [
        (10.0, 5.0, "10", "5", "ok"),
        (None, 5.0, "-", "5", "sem_meta"),
        (10.0, None, "10", "-", "sem_realizado"),
        (None, None, "-", "-", "sem_dado"),
        (10.0, 0.0, "10", "R$ - 0", "zero_suspeito"),
        (10.0, 0.0, "10", "R$    - 0", "zero_suspeito"),  # espaçamento real do portal
        (0.0, 5.0, "R$ - 0", "5", "zero_suspeito"),
        (0.0, 5.0, "R$    - 0", "5", "zero_suspeito"),
        (0.0, 0.0, "R$ - 0", "R$ - 0", "zero_suspeito"),
        (0.0, None, "R$ - 0", "-", "zero_suspeito"),  # prioridade sobre sem_realizado
    ],
)
def test_status_dado(meta, realizado, meta_txt, realizado_txt, esperado) -> None:
    assert status_dado(meta, realizado, meta_txt, realizado_txt) == esperado


@pytest.mark.parametrize(
    ("periodo", "esperado"),
    [
        ("QUARTO TRIMESTRE", 12),
        ("QUARTO_TRIMESTRE", 12),
        ("TERCEIRO TRIMESTRE", 9),
        ("TERCEIRO_TRIMESTRE", 9),
        ("SEGUNDO TRIMESTRE", 6),
        ("SEGUNDO_TRIMESTRE", 6),
        ("PRIMEIRO TRIMESTRE", 3),
        ("PRIMEIRO_TRIMESTRE", 3),
        ("JANEIRO", 1),
        ("MARÇO", 3),
        ("SETEMBRO", 9),
        ("DEZEMBRO", 12),
    ],
)
def test_mes_referencia(periodo: str, esperado: int) -> None:
    assert mes_referencia(periodo) == esperado


@pytest.mark.parametrize("periodo", ["QUINTO TRIMESTRE", "SEMESTRE", "", None])
def test_mes_referencia_invalido_levanta_erro(periodo: str | None) -> None:
    with pytest.raises(ValueError):
        mes_referencia(periodo)


@pytest.mark.parametrize(
    ("publicado", "calculado", "esperado"),
    [
        (77.8, 86.34, True),   # caso real: SP 2025, indicador 14.2
        (100.0, 98.9, True),
        (100.0, 99.0, False),  # exatamente 1 p.p. não é divergência
        (88.4, 88.44, False),
        (None, 50.0, False),
        (50.0, None, False),
        (None, None, False),
    ],
)
def test_divergencia_indice(publicado, calculado, esperado: bool) -> None:
    assert divergencia_indice(publicado, calculado) is esperado


@pytest.mark.parametrize(
    ("publicado", "calculado", "esperado"),
    [
        (77.8, 86.34, 86.34),
        (77.8, None, 77.8),
        (None, 50.0, 50.0),
        (None, None, None),
    ],
)
def test_indice_analise(publicado, calculado, esperado) -> None:
    assert indice_analise(publicado, calculado) == esperado
