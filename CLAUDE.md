# Projeto senai-briefing-ia
Pipeline: coleta → normaliza → qualidade → diagnostico → projecao → (fatos → LLM → validador → app).
Leia SPEC.md e DECISOES.md antes de mudanças relevantes.

## Convenções
- Python 3.12, pandas, logging (não print), type hints, docstrings em português.
- Módulos em src/, executáveis com `python -m src.<modulo>`; caminhos ancorados na raiz do projeto.
- Dados: data/raw (bruto, nunca alterar), data/processed (CSV utf-8-sig).
- Use `indice_analise` nas análises. Nunca trate "-" como zero.
- Todo módulo novo tem testes em tests/ (pytest).
- Não altere módulos que não foram citados no pedido.
- Nunca leia nem imprima o .env.

## Forma de trabalho
- Não leia CSVs inteiros; use head ou o schema.
- Não rode o pipeline completo sem eu pedir; eu rodo testes e scripts no terminal.
- Ao final, responda em no máximo 8 linhas: o que fez, decisões tomadas e dúvidas.
