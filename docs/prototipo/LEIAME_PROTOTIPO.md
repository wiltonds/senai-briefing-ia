# Protótipo aprovado (referência visual e de interação)

`prototipo_aprovado.dc.html` é o protótipo validado pelo dono do produto em 25/11/2026.
Ele está no formato do editor de design (marcações `<x-dc>`, `<sc-for>`, `<sc-if>` e `{{ }}`),
mas o layout, as cores, os textos e o comportamento valem como especificação:

- Cabeçalho azul-marinho (#1F3A5F) com selo "Dados até ... · atualizado em ...".
- Seletor de escopo (Brasil + 27 DRs), botão "← Brasil" quando em DR, selo "Números verificados" (abre painel de confiança) e "Exportar PDF".
- Mensagem central em caixa azul-marinho.
- 4 cartões-KPI clicáveis (valor, comparação, barra até 150% com marca em 100%, status com cor + palavra); clique abre painel de detalhe.
- Destaques / Pontos de atenção / Recomendações práticas (Quem · Quando).
- "Pergunte ao briefing": perguntas sugeridas + campo livre; resposta com selo de verificação e fonte; recusa quando não há dado oficial.
- Visão Brasil: mapa em quadrados por UF (cor = empregabilidade vs meta), botões de região, 3 números da região e cartões dos DRs da região (clique abre o DR).
- Visão DR: gráfico volume × resultado com manchete-conclusão.
- Status: atingido #0aac23, atenção #fbb315, crítico #dc3c3d, sem dado #C5CEDA; sempre cor + palavra.

Os números do protótipo foram digitados a partir dos dados reais; no produto eles vêm da API.
