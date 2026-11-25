"""Testes de src.formato (arredondamento ROUND_HALF_UP e formatação pt-BR)."""

import pytest

import src.fatos as fatos
from src.formato import (
    arredondar,
    fmt_decimal,
    fmt_en,
    fmt_int,
    fmt_moeda,
    fmt_num,
    fmt_pct,
    fmt_vezes,
)


@pytest.mark.parametrize("valor, casas, esperado", [
    (77.25, 1, 77.3), (77.24999, 1, 77.2), (2.5, 0, 3.0), (-0.25, 1, -0.3), (85.45, 1, 85.5),
    (0.9165, 3, 0.917),
])
def test_arredondar_half_up(valor, casas, esperado):
    assert arredondar(valor, casas) == esperado


def test_python_round_seria_diferente():
    # Motivo do módulo: o arredondamento nativo dá 77,2 para 77,25.
    assert f"{77.25:.1f}" == "77.2"
    assert fmt_pct(77.25) == "77,3%"


@pytest.mark.parametrize("func, valor, esperado", [
    (fmt_int, 2.5, "3"), (fmt_int, 1234567, "1.234.567"),
    (fmt_num, 23.45, "23,5"), (fmt_num, 23.0, "23"),
    (fmt_decimal, -0.265, "-0,27"), (fmt_vezes, 3.75, "3,8x"),
    (fmt_moeda, 1_065_000_000, "R$ 1,07 bi"), (fmt_moeda, -1_112_970.70, "-R$ 1,1 mi"),
])
def test_formatadores_usam_half_up(func, valor, esperado):
    assert func(valor) == esperado


def test_sem_menos_zero():
    assert fmt_pct(-0.04) == "0,0%"


@pytest.mark.parametrize("valor, esperado", [(12.0, "12"), (0.825, "0.83"), (-1.005, "-1.01")])
def test_fmt_en(valor, esperado):
    assert fmt_en(valor) == esperado


def test_fatos_reexporta_a_mesma_funcao():
    assert fatos.fmt_pct is fmt_pct
    assert fatos.fmt_moeda is fmt_moeda
    assert fatos.arredondar is arredondar
