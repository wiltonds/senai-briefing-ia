/* Briefing executivo SENAI — página (JavaScript puro).
 *
 * Todos os dados vêm de /api/escopos, /api/escopo/{escopo} e /api/regioes.
 * A página NÃO calcula números: só exibe as strings recebidas. A única conta é a
 * largura da barra do KPI, derivada de pct_barra (0–150) para a escala da barra.
 * O escopo fica na URL (#AL) para link direto e para o botão voltar do navegador.
 */
(function () {
  'use strict';

  var COR = {
    atingido: '#0aac23', atencao: '#fbb315', critico: '#dc3c3d',
    sem_dado: '#C5CEDA', neutro: '#9AA5B4'
  };
  var PALAVRA = { atingido: 'Atingido', atencao: 'Atenção', critico: 'Crítico', sem_dado: 'Sem dado' };
  var REGIOES = ['Norte', 'Nordeste', 'Centro-Oeste', 'Sudeste', 'Sul'];
  // Grade do protótipo: posição aproximada de cada UF no mapa (6 colunas).
  var GRADE = [
    ['', '', 'RR', 'AP', '', ''],
    ['', 'AM', 'PA', 'MA', 'CE', 'RN'],
    ['AC', 'RO', 'MT', 'TO', 'PI', 'PB'],
    ['', '', 'GO', 'DF', 'BA', 'PE'],
    ['', 'MS', 'MG', 'ES', 'SE', 'AL'],
    ['', '', 'SP', 'RJ', '', ''],
    ['', '', 'PR', '', '', ''],
    ['', '', 'SC', '', '', ''],
    ['', '', 'RS', '', '', '']
  ];
  var BARRA_MAX = 150; // pct_barra vai de 0 a 150; o traço da meta (100) fica em 2/3.

  var estado = {
    escopos: null,   // [{escopo, nome}]
    escopo: 'BR',
    doc: null,       // JSON do escopo atual
    regioes: null,   // JSON de /api/regioes
    aberto: null,    // índice do KPI com detalhe aberto
    regiao: null,    // região selecionada no mapa (null = Brasil)
    pedido: 0        // descarta respostas antigas
  };

  var $ = function (id) { return document.getElementById(id); };

  /** Cria elemento; filhos string viram nós de texto (nunca HTML). */
  function el(tag, attrs) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'class') n.className = v;
        else if (k === 'style') n.setAttribute('style', v);
        else if (k.indexOf('on') === 0) n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v === true ? '' : v);
      });
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) c.forEach(function (x) { if (x) n.appendChild(typeof x === 'string' ? document.createTextNode(x) : x); });
      else n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return n;
  }

  function limpar(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function ponto(status) { return el('span', { class: 'ponto', style: 'background:' + (COR[status] || COR.neutro), 'aria-hidden': 'true' }); }

  // ------------------------------------------------------------------ dados
  function buscar(url) {
    return fetch(url, { headers: { Accept: 'application/json' } }).then(function (r) {
      if (!r.ok) { var e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
      return r.json();
    });
  }

  function escopoDaUrl() {
    var h = decodeURIComponent((location.hash || '').replace(/^#/, '')).toUpperCase();
    var validos = (estado.escopos || []).map(function (e) { return e.escopo; });
    return validos.indexOf(h) >= 0 ? h : 'BR';
  }

  function irPara(escopo) {
    if (('#' + escopo) === location.hash) carregar();
    else location.hash = escopo; // dispara hashchange → carregar()
  }

  function carregar() {
    var escopo = escopoDaUrl();
    var meu = ++estado.pedido;
    var mudou = escopo !== estado.escopo;
    estado.escopo = escopo;
    if (mudou) { estado.aberto = null; estado.regiao = null; }
    $('escopo').value = escopo;
    mostrarCarregando();

    var pedidos = [buscar('/api/escopo/' + encodeURIComponent(escopo))];
    if (escopo === 'BR' && !estado.regioes) pedidos.push(buscar('/api/regioes'));
    Promise.all(pedidos).then(function (res) {
      if (meu !== estado.pedido) return;
      estado.doc = res[0];
      if (res[1]) estado.regioes = res[1];
      renderizar();
    }).catch(function (err) {
      if (meu !== estado.pedido) return;
      mostrarErro(err);
    });
  }

  function iniciar() {
    mostrarCarregando();
    buscar('/api/escopos').then(function (lista) {
      estado.escopos = lista;
      montarSeletor(lista);
      estado.escopo = null; // força o primeiro carregamento
      carregar();
    }).catch(mostrarErro);
  }

  // ------------------------------------------------------------------ estados
  function mostrarCarregando() {
    var s = limpar($('estado'));
    s.className = 'cartao estado';
    s.appendChild(el('span', null, 'Carregando o briefing…'));
    s.hidden = false;
    $('conteudo').hidden = true;
  }

  function mostrarErro(err) {
    var s = limpar($('estado'));
    s.className = 'cartao estado estado-erro';
    var semDado = err && err.status === 404;
    s.appendChild(el('strong', null, semDado ? 'Os dados deste briefing ainda não foram gerados.'
      : 'Não foi possível carregar o briefing.'));
    s.appendChild(el('span', null, semDado ? 'Peça à equipe para gerar os dados do produto e tente de novo.'
      : 'Verifique sua conexão com a rede e tente de novo.'));
    s.appendChild(el('button', { type: 'button', class: 'btn btn-primario', onclick: function () {
      if (estado.escopos) carregar(); else iniciar();
    } }, 'Tentar de novo'));
    s.hidden = false;
    $('conteudo').hidden = true;
  }

  // ------------------------------------------------------------------ cabeçalho e barra
  function montarSeletor(lista) {
    var sel = limpar($('escopo'));
    var br = lista.filter(function (e) { return e.escopo === 'BR'; })[0];
    var drs = lista.filter(function (e) { return e.escopo !== 'BR'; })
      .sort(function (a, b) { return a.nome.localeCompare(b.nome, 'pt-BR'); });
    if (br) sel.appendChild(el('option', { value: 'BR' }, br.nome + ' (Departamento Nacional)'));
    drs.forEach(function (e) { sel.appendChild(el('option', { value: e.escopo }, e.nome)); });
  }

  function renderCabecalho(meta) {
    var selo = $('selo-dados');
    var partes = [];
    if (meta.mes_referencia) partes.push('Dados até ' + meta.mes_referencia);
    if (meta.data_coleta) partes.push('atualizado em ' + meta.data_coleta);
    selo.textContent = partes.join(' · ');
    selo.hidden = partes.length === 0;

    $('voltar').hidden = meta.escopo === 'BR';
    var pdf = $('exportar');
    if (meta.tem_pdf) {
      pdf.href = '/api/pdf/' + encodeURIComponent(meta.escopo);
      pdf.hidden = false;
    } else {
      pdf.removeAttribute('href');
      pdf.hidden = true;
    }
    $('confianca-coleta').textContent = meta.data_coleta ? ' em ' + meta.data_coleta : '';

    var rod = $('rodape');
    rod.textContent = 'Fontes: Plano Estratégico Sistêmico e Atendimento Social (Portal da Transparência SENAI); Relatório de Gestão SENAI 2025.'
      + (meta.revisado_por ? ' Revisado por: ' + meta.revisado_por + '.' : '');
    document.title = 'Briefing executivo SENAI · ' + meta.nome;
  }

  // ------------------------------------------------------------------ render
  function renderizar() {
    var d = estado.doc;
    renderCabecalho(d.meta);
    renderBriefingTopo(d);
    renderKpis(d.kpis);
    renderDetalhe();
    renderBriefingBlocos(d.briefing);
    renderVisaoBR();
    renderVisaoDR();
    $('estado').hidden = true;
    $('conteudo').hidden = false;
  }

  function renderBriefingTopo(d) {
    var alvo = limpar($('briefing-topo'));
    if (d.briefing) {
      alvo.appendChild(el('section', { class: 'msg-central', 'aria-label': 'Mensagem central' },
        el('div', { class: 'msg-central-rot' }, 'Mensagem central · ' + d.meta.nome),
        el('p', null, d.briefing.mensagem_central)));
    } else {
      alvo.appendChild(el('section', { class: 'cartao aviso', role: 'note' },
        d.meta.escopo === 'BR' ? 'Briefing ainda não gerado para o Brasil'
          : 'Briefing ainda não gerado para este DR'));
    }
  }

  function renderKpis(kpis) {
    var alvo = limpar($('kpis'));
    kpis.forEach(function (k, i) {
      var aberto = estado.aberto === i;
      var largura = (Math.min(Math.max(k.pct_barra, 0), BARRA_MAX) / BARRA_MAX * 100) + '%';
      var palavra = k.rotulo_status || PALAVRA[k.status] || '';
      alvo.appendChild(el('button', {
        type: 'button', class: 'kpi', 'aria-expanded': aberto ? 'true' : 'false', 'aria-controls': 'detalhe',
        onclick: function () { estado.aberto = aberto ? null : i; renderKpis(estado.doc.kpis); renderDetalhe(); }
      },
        el('span', { class: 'kpi-rot' }, k.rotulo),
        el('span', { class: 'kpi-valor', title: k.fonte }, k.valor),
        el('span', { class: 'kpi-comp', title: k.nome_oficial || null }, k.comparacao),
        el('span', { class: 'barra-kpi', 'aria-hidden': 'true' },
          el('span', { class: 'barra-kpi-ench', style: 'width:' + largura + ';background:' + (COR[k.status] || COR.neutro) }),
          el('span', { class: 'barra-kpi-meta' })),
        el('span', { class: 'kpi-rodape' },
          el('span', { class: 'status' }, ponto(k.status), palavra),
          el('span', { class: 'kpi-acao' }, aberto ? 'Ocultar' : 'Ver detalhe')),
        el('span', { class: 'sr-only' }, k.fonte)
      ));
    });
  }

  function renderDetalhe() {
    var box = limpar($('detalhe'));
    var i = estado.aberto;
    if (i === null || !estado.doc.kpis[i]) { box.hidden = true; return; }
    var det = estado.doc.kpis[i].detalhe;
    box.appendChild(el('div', { class: 'detalhe-cab' },
      el('h3', null, det.titulo),
      el('button', { type: 'button', class: 'btn-link', onclick: function () {
        estado.aberto = null; renderKpis(estado.doc.kpis); renderDetalhe();
        var cards = $('kpis').children; if (cards[i]) cards[i].focus();
      } }, 'Fechar')));
    box.appendChild(el('ul', null, det.linhas.map(function (l) {
      return el('li', null,
        el('span', null, ponto(l.status),
          el('span', { title: l.nome_oficial || null, class: l.nome_oficial ? 'tem-dica' : null }, l.nome),
          l.status !== 'neutro' && PALAVRA[l.status] ? el('span', { class: 'sr-only' }, '(' + PALAVRA[l.status] + ')') : null),
        el('span', { class: 'detalhe-valor' }, l.valor));
    })));
    if (det.nota) box.appendChild(el('div', { class: 'detalhe-nota' }, det.nota));
    box.hidden = false;
  }

  function item(t, x, extra) {
    return el('article', { class: 'item' }, el('div', { class: 'item-t' }, t), el('div', { class: 'item-x' }, x), extra || null);
  }

  function renderBriefingBlocos(b) {
    var alvo = limpar($('briefing-blocos'));
    alvo.hidden = !b;
    if (!b) return;
    var col = el('section', { class: 'briefing-col', 'aria-label': 'Briefing' },
      el('div', { class: 'duas' },
        el('div', { class: 'lista-bloco' },
          el('h2', { class: 'sec' }, ponto('atingido'), 'Destaques'),
          b.destaques.map(function (d) { return item(d.titulo, d.texto); })),
        el('div', { class: 'lista-bloco' },
          el('h2', { class: 'sec' }, ponto('atencao'), 'Pontos de atenção'),
          b.pontos_atencao.map(function (d) { return item(d.titulo, d.texto); }))),
      el('h2', { class: 'sec sec-recs' }, 'Recomendações práticas'),
      el('div', { class: 'recs' }, b.recomendacoes.map(function (r) {
        return item(r.titulo, r.texto, el('div', { class: 'item-quem' }, 'Quem: ' + r.quem + ' · Quando: ' + r.quando));
      })));
    alvo.appendChild(col);
    // Passo 3: a seção "Pergunte ao briefing" entra aqui, ao lado da coluna.
  }

  // ------------------------------------------------------------------ visão Brasil
  function renderVisaoBR() {
    var sec = limpar($('visao-br'));
    var d = estado.doc;
    if (d.meta.escopo !== 'BR' || !d.mapa || !estado.regioes) { sec.hidden = true; return; }
    var reg = estado.regiao;
    var dadosReg = reg ? estado.regioes.regioes.filter(function (r) { return r.regiao === reg; })[0] : null;
    var resumo = dadosReg || estado.regioes.brasil;
    var porUf = {};
    d.mapa.forEach(function (m) { porUf[m.uf] = m; });

    sec.appendChild(el('div', { class: 'visao-cab' },
      el('h2', null, resumo.titulo),
      el('p', { class: 'como-ler' }, el('strong', null, 'Como ler:'),
        ' cada quadrado é um DR, na posição aproximada do mapa; a cor mostra a empregabilidade de egressos em relação à meta (2025). Escolha uma região ou clique em um estado.')));

    sec.appendChild(el('div', { class: 'regioes', role: 'group', 'aria-label': 'Escolher região' },
      ['Brasil'].concat(REGIOES).map(function (n) {
        var ativo = (reg || 'Brasil') === n;
        return el('button', { type: 'button', class: 'btn-regiao', 'aria-pressed': ativo ? 'true' : 'false',
          onclick: function () { estado.regiao = n === 'Brasil' ? null : n; renderVisaoBR(); focarRegiao(n); } }, n);
      })));

    var grade = el('div', { class: 'mapa', role: 'group', 'aria-label': 'Mapa dos DRs por estado' });
    GRADE.forEach(function (linha) {
      linha.forEach(function (uf) {
        var m = uf && porUf[uf];
        if (!m) { grade.appendChild(el('span', { 'aria-hidden': 'true' })); return; }
        var semDado = m.status === 'sem_dado';
        var dica = m.nome + ': empregabilidade ' + (semDado ? 'sem dado' : 'em ' + m.valor + ' da meta') + '. Clique para abrir.';
        grade.appendChild(el('button', {
          type: 'button', class: 'uf' + (reg && m.regiao !== reg ? ' fora' : ''),
          style: 'background:' + (COR[m.status] || COR.sem_dado), title: dica, 'aria-label': dica,
          onclick: function () { irPara(uf); }
        }, el('span', { class: 'uf-sigla' }, uf), el('span', { class: 'uf-valor' }, semDado ? 's/d' : m.valor)));
      });
    });

    var legenda = el('div', { class: 'legenda-mapa' },
      [['atingido', 'Atingido (≥100%)'], ['atencao', 'Atenção (80–99,9%)'], ['critico', 'Crítico (<80%)'], ['sem_dado', 'Sem dado']]
        .map(function (p) { return el('span', null, el('span', { class: 'quad', style: 'background:' + COR[p[0]], 'aria-hidden': 'true' }), p[1]); }));

    var drs = (dadosReg ? dadosReg.drs : estado.regioes.brasil.drs);
    var listaTitulo = dadosReg ? 'DRs do ' + reg + ' (clique para abrir o briefing)' : estado.regioes.brasil.lista_titulo;

    sec.appendChild(el('div', { class: 'br-corpo' },
      el('div', { class: 'mapa-col' }, grade, legenda),
      el('div', { class: 'reg-col' },
        el('div', { class: 'numeros' }, resumo.numeros.map(function (n) {
          return el('div', { class: 'numero' },
            el('span', { class: 'numero-rot' }, n.rotulo),
            el('span', { class: 'numero-val' }, n.valor),
            el('span', { class: 'numero-nota' }, n.nota));
        })),
        el('h3', { class: 'lista-titulo' }, listaTitulo),
        el('div', { class: 'drs' }, drs.map(function (x) {
          var emp = x.status === 'sem_dado' ? 'Empregabilidade: sem dado' : 'Empregabilidade: ' + x.empregabilidade + ' da meta';
          return el('button', { type: 'button', class: 'dr', onclick: function () { irPara(x.uf); } },
            el('span', { class: 'dr-nome' }, ponto(x.status), x.nome,
              el('span', { class: 'sr-only' }, '(' + (PALAVRA[x.status] || '') + ')')),
            el('span', { class: 'dr-info' }, emp),
            el('span', { class: 'dr-info' }, x.atingidos + ' indicadores atingidos · ' + x.criticos));
        })))));
    sec.hidden = false;
  }

  function focarRegiao(n) {
    var bts = document.querySelectorAll('.btn-regiao');
    for (var i = 0; i < bts.length; i++) if (bts[i].textContent === n) { bts[i].focus(); return; }
  }

  // ------------------------------------------------------------------ visão DR
  function renderVisaoDR() {
    var sec = limpar($('visao-dr'));
    var d = estado.doc;
    if (d.meta.escopo === 'BR' || !d.grafico) { sec.hidden = true; return; }
    sec.appendChild(el('img', { src: '/' + d.grafico.figura, width: '520', height: '390',
      alt: 'Dispersão do índice de matrículas pelo índice de empregabilidade dos 27 DRs' }));
    sec.appendChild(el('div', { class: 'visao-dr-texto' },
      el('h2', null, d.grafico.manchete),
      el('p', { class: 'como-ler' }, el('strong', null, 'Como ler:'),
        ' cada ponto é um DR. Para a direita, mais matrículas em relação à meta; para cima, mais empregabilidade. A área sombreada reúne quem entrega volume sem resultado.')));
    sec.hidden = false;
  }

  // ------------------------------------------------------------------ eventos
  $('escopo').addEventListener('change', function (e) { irPara(e.target.value); });
  $('voltar').addEventListener('click', function () { irPara('BR'); });
  $('btn-selo').addEventListener('click', function () {
    var painel = $('confianca');
    painel.hidden = !painel.hidden;
    this.setAttribute('aria-expanded', painel.hidden ? 'false' : 'true');
  });
  window.addEventListener('hashchange', function () { if (estado.escopos) carregar(); });

  iniciar();
}());
