---
title: Briefing Executivo SENAI
emoji: 📊
colorFrom: blue
colorTo: gray
sdk: static
pinned: false
---

# Briefing Executivo SENAI

Painel e briefing executivo do desempenho do SENAI (Brasil e os 27 Departamentos
Regionais), a partir de dados públicos do Portal da Transparência. Os textos são
gerados com IA e só vão ao ar depois de passar por um validador de fatos e por
revisão humana.

O Space é estático (`sdk: static`) e é a pasta `site/`, gerada por
`python -m src.publicar --revisado-por "Nome"` (com o próprio `site/README.md`).
Localmente, `uvicorn app.main:app` serve a mesma página lendo de `site/`.
