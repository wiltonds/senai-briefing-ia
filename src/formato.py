"""Arredondamento e formatação numérica pt-BR — implementação única do projeto.

Todo número exibido (ficha de fatos, diagnóstico, PDF) passa por aqui, para que o
mesmo valor tenha sempre o mesmo texto. Regras:

- Arredondamento ``ROUND_HALF_UP`` (meio para longe do zero), via ``Decimal`` sobre a
  representação curta do float: 77.25 -> 77,3 (o ``f"{77.25:.1f}"`` do Python daria 77,2).
- Arredonda-se uma única vez, na exibição: tabelas intermediárias guardam o valor
  com precisão total (arredondar antes e de novo depois produz divergências).
- Padrão brasileiro: 39,8%; 7.476; R$ 1,06 bi.

``src.fatos`` reexporta estas funções (``from src.fatos import fmt_pct`` continua
válido). Elas ficam neste módulo porque ``src.fatos`` importa ``src.diagnostico``,
e o diagnóstico também precisa formatar (evita importação circular).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _decimal(valor: float, casas: int) -> Decimal:
    """Valor como Decimal arredondado (ROUND_HALF_UP) com ``casas`` decimais."""
    quantum = Decimal(1).scaleb(-casas)
    return Decimal(repr(float(valor))).quantize(quantum, rounding=ROUND_HALF_UP)


def arredondar(valor: float, casas: int = 1) -> float:
    """Arredonda com ROUND_HALF_UP: arredondar(77.25, 1) -> 77.3; arredondar(-0.25, 1) -> -0.3."""
    return float(_decimal(valor, casas))


def _milhar_br(texto_en: str) -> str:
    """Troca separadores do formato en-US ("1,234.5") pelo pt-BR ("1.234,5")."""
    return texto_en.replace(",", "_").replace(".", ",").replace("_", ".")


def _fixo(valor: float, casas: int) -> str:
    """Número arredondado (ROUND_HALF_UP) com milhar e ``casas`` fixas, em pt-BR."""
    d = _decimal(valor, casas)
    if d == 0:
        d = abs(d)  # evita "-0,0"
    return _milhar_br(f"{d:,.{casas}f}")


def fmt_pct(valor: float, casas: int = 1) -> str:
    """Percentual pt-BR: 39.8 -> "39,8%"; 77.25 -> "77,3%"."""
    return _fixo(valor, casas) + "%"


def fmt_int(valor: float) -> str:
    """Inteiro pt-BR com milhar: 7476 -> "7.476"; 2.5 -> "3"."""
    return _fixo(valor, 0)


def fmt_num(valor: float) -> str:
    """Número pt-BR: inteiro sem casas ("7.476"); fracionário com 1 casa ("23,5")."""
    if float(valor).is_integer():
        return fmt_int(valor)
    return _fixo(valor, 1)


def fmt_moeda(valor: float) -> str:
    """Moeda compacta pt-BR: "R$ 1,06 bi" (2 casas), "R$ 336,0 mi" e "R$ 796,5 mil" (1 casa).

    Negativos com "-R$"; abaixo de mil, valor com centavos ("R$ 512,00").
    """
    sinal = "-" if valor < 0 else ""
    v = abs(valor)
    for limite, sufixo, casas in ((1e9, " bi", 2), (1e6, " mi", 1), (1e3, " mil", 1)):
        if v >= limite:
            return f"{sinal}R$ {_fixo(v / limite, casas)}{sufixo}"
    return f"{sinal}R$ {_fixo(v, 2)}"


def fmt_decimal(valor: float, casas: int = 2) -> str:
    """Decimal pt-BR com casas fixas: -0.2612 -> "-0,26"."""
    return _fixo(valor, casas)


def fmt_vezes(valor: float) -> str:
    """Múltiplo do ritmo atual: 3.76 -> "3,8x"."""
    return fmt_decimal(valor, 1) + "x"


def fmt_en(valor: float, casas: int = 2) -> str:
    """Número técnico (en-US, sem milhar) para evidências legíveis por máquina.

    Inteiro sem casas ("12"); demais com ``casas`` fixas e ROUND_HALF_UP ("0.25").
    """
    if float(valor).is_integer():
        return f"{_decimal(valor, 0):f}"
    return f"{_decimal(valor, casas):f}"


def eh_monetario(unidade: Any) -> bool:
    """Indica se a unidade do indicador é monetária ("(R$) Monetário")."""
    return isinstance(unidade, str) and "R$" in unidade


def eh_percentual(unidade: Any) -> bool:
    """Indica se a unidade do indicador é percentual."""
    return isinstance(unidade, str) and "%" in unidade


def fmt_valor(valor: float, unidade: Any) -> str:
    """Formata um valor numérico conforme a unidade do indicador."""
    if eh_monetario(unidade):
        return fmt_moeda(valor)
    if eh_percentual(unidade):
        return fmt_pct(valor)
    return fmt_num(valor)
