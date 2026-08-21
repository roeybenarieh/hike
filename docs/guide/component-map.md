# Component Map

// # TODO: for each node add a link to the propper documentation(if there is no documentation, add it)

<style>
/* Dark default (slate scheme) */
:root {
  --cmap-bg: #0d0d1a;
  --cmap-border: rgba(255,255,255,0.07);
  --cmap-search-border: rgba(255,255,255,0.15);
  --cmap-text: #e2e8f0;
  --cmap-muted: #64748b;
  --cmap-code: #94a3b8;
}
/* Light override */
body[data-md-color-scheme="default"] {
  --cmap-bg: #f1f5f9;
  --cmap-border: rgba(0,0,0,0.09);
  --cmap-search-border: rgba(0,0,0,0.18);
  --cmap-text: #1e293b;
  --cmap-muted: #475569;
  --cmap-code: #64748b;
}
/* hide native browser clear button */
#hike-cmap-search::-webkit-search-cancel-button { display: none; }
/* fullscreen */
#hike-cmap-wrap:-webkit-full-screen,
#hike-cmap-wrap:fullscreen { background: var(--cmap-bg); padding: 1rem; overflow: auto; }
#hike-cmap-wrap:-webkit-full-screen #hike-cmap,
#hike-cmap-wrap:fullscreen #hike-cmap { height: calc(100vh - 130px) !important; }
</style>

<div id="hike-cmap-wrap" style="position:relative;margin:1.5rem -1.1rem;width:calc(100% + 2.2rem);font-family:inherit;">
  <div style="margin-bottom:8px;display:flex;align-items:center;gap:10px;">
    <div style="position:relative;flex:1;">
      <input id="hike-cmap-search" type="search" placeholder="Search objects…" autocomplete="off" spellcheck="false"
        style="width:100%;box-sizing:border-box;padding:8px 32px 8px 14px;background:var(--cmap-bg);border:1px solid var(--cmap-search-border);border-radius:6px;color:var(--cmap-text);font-size:0.85rem;font-family:monospace;outline:none;transition:border-color .15s;" />
      <button id="hike-cmap-clear" aria-label="Clear search"
        style="display:none;position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--cmap-muted);font-size:1.1rem;line-height:1;padding:2px 4px;">&#10005;</button>
    </div>
    <span id="hike-cmap-search-count" style="color:var(--cmap-muted);font-size:0.78rem;min-width:70px;text-align:right;"></span>
  </div>
  <div style="position:relative;">
    <div id="hike-cmap" style="width:100%;height:900px;background:var(--cmap-bg);border-radius:10px;border:1px solid var(--cmap-border);"></div>
    <div style="position:absolute;bottom:12px;right:12px;display:flex;flex-direction:column;gap:12px;align-items:center;">
      <button id="hike-cmap-reset" aria-label="Reset view" title="Reset view"
        style="display:none;align-items:center;justify-content:center;background:var(--cmap-bg);border:1px solid var(--cmap-border);border-radius:6px;padding:12px 16px;cursor:pointer;color:var(--cmap-muted);transition:opacity .15s;opacity:0.85;">
        <svg width="30" height="30" viewBox="-17 -17 34 34" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="0" cy="0" r="4"/>
          <line x1="0" y1="-17" x2="0" y2="-10"/>
          <line x1="0" y1="10" x2="0" y2="17"/>
          <line x1="-17" y1="0" x2="-10" y2="0"/>
          <line x1="10" y1="0" x2="17" y2="0"/>
        </svg>
      </button>
      <button id="hike-cmap-fullscreen" aria-label="Full screen" title="Full screen"
        style="display:flex;align-items:center;justify-content:center;background:var(--cmap-bg);border:1px solid var(--cmap-border);border-radius:6px;padding:12px 16px;cursor:pointer;color:var(--cmap-muted);transition:opacity .15s;opacity:0.85;">
        <svg id="hike-cmap-fs-icon" width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15,3 21,3 21,9"/><polyline points="9,21 3,21 3,15"/>
          <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
        </svg>
      </button>
    </div>
  </div>
  <div id="hike-cmap-info" style="display:none;margin-top:8px;padding:12px 18px;background:var(--cmap-bg);border-radius:8px;border:1px solid var(--cmap-border);font-size:0.82rem;font-family:monospace;color:var(--cmap-text);">
    <span id="hike-cmap-info-name" style="font-weight:700;margin-right:10px;"></span>
    <code id="hike-cmap-info-imp" style="color:var(--cmap-code);white-space:pre;"></code>
  </div>
  <div style="margin-top:8px;padding:10px 16px;background:var(--cmap-bg);border-radius:8px;border:1px solid var(--cmap-border);display:flex;flex-direction:column;gap:8px;font-size:0.75rem;color:var(--cmap-muted);">
    <div style="display:flex;gap:18px;align-items:center;">
      <span>&#9135;&#9135; extends / implements</span>
      <span style="letter-spacing:1px;">- - - uses / depends on</span>
    </div>
    <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;">
      <span data-cat="core"        style="cursor:pointer;transition:opacity .15s;"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#2e1065;border:1.5px solid #7c3aed;margin-right:4px;vertical-align:middle;"></span>Core DDD</span>
      <span data-cat="rules"       style="cursor:pointer;transition:opacity .15s;"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#0f2457;border:1.5px solid #3b82f6;margin-right:4px;vertical-align:middle;"></span>Rules</span>
      <span data-cat="specs"       style="cursor:pointer;transition:opacity .15s;"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#021b1a;border:1.5px solid #0d9488;margin-right:4px;vertical-align:middle;"></span>Specifications</span>
      <span data-cat="persistence" style="cursor:pointer;transition:opacity .15s;"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#1c0e04;border:1.5px solid #b45309;margin-right:4px;vertical-align:middle;"></span>Persistence</span>
      <span data-cat="events"      style="cursor:pointer;transition:opacity .15s;"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#1f0010;border:1.5px solid #e11d48;margin-right:4px;vertical-align:middle;"></span>Events</span>
    </div>
  </div>
</div>

<script>
(function () {
  var CY_URL = 'https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js';

  var NODES = [
    /* Core DDD */
    { data: { id: 'ValueObject',     l: 'ValueObject',           s: 'hike.value_object',               cat: 'core',        imp: 'from hike import ValueObject' } },
    { data: { id: 'EntityID',        l: 'EntityID',              s: 'hike.entity',                     cat: 'core',        imp: 'from hike import EntityID' } },
    { data: { id: 'Entity',          l: 'Entity',                s: 'hike.entity',                     cat: 'core',        imp: 'from hike import Entity' } },
    { data: { id: 'Aggregate',       l: 'Aggregate',             s: 'hike.aggregate',                  cat: 'core',        imp: 'from hike import Aggregate' } },
    { data: { id: 'DomainEvent',     l: 'DomainEvent',           s: 'hike.domain_event',               cat: 'events',      imp: 'from hike import DomainEvent' } },
    /* Rules */
    { data: { id: 'Rule',            l: 'Rule',                  s: 'hike.rules',                      cat: 'rules',       imp: 'from hike import Rule' } },
    { data: { id: 'SpecRule',        l: 'SpecificationRule',     s: 'hike.rules',                      cat: 'rules',       imp: 'from hike import SpecificationRule' } },
    /* Specifications */
    { data: { id: 'ISpec',           l: 'ISpecification',        s: 'hike.specifications',             cat: 'specs',       imp: 'from hike import ISpecification' } },
    /* Persistence */
    { data: { id: 'IRepository',     l: 'IRepository',           s: 'hike.persistence.repository',     cat: 'persistence', imp: 'from hike import IRepository' } },
    { data: { id: 'UnitOfWork',      l: 'UnitOfWork',            s: 'hike.persistence.uow',            cat: 'persistence', imp: 'from hike import UnitOfWork' } },
    /* Events */
    { data: { id: 'IEventHandler',    l: 'IEventHandler',    s: 'hike.events.interfaces', cat: 'events', imp: 'from hike.events.interfaces import IEventHandler' } },
    { data: { id: 'IEventPublisher',  l: 'IEventPublisher',  s: 'hike.events.interfaces', cat: 'events', imp: 'from hike.events.interfaces import IEventPublisher' } },
    { data: { id: 'IEventSubscriber', l: 'IEventSubscriber', s: 'hike.events.interfaces', cat: 'events', imp: 'from hike.events.interfaces import IEventSubscriber' } },
    { data: { id: 'InMemoryEventBus', l: 'InMemoryEventBus', s: 'hike.events.event_bus',  cat: 'events', imp: 'from hike.events.event_bus import InMemoryEventBus' } },
  ];

  var EDGES = [
    /* ── Inheritance / implements (solid) ──────────────────────── */
    { data: { source: 'ValueObject',      target: 'EntityID',        etype: 'inh' } },
    { data: { source: 'Entity',           target: 'Aggregate',       etype: 'inh' } },
    { data: { source: 'Rule',             target: 'SpecRule',        etype: 'inh' } },
    { data: { source: 'IEventPublisher',  target: 'InMemoryEventBus', etype: 'inh' } },
    { data: { source: 'IEventSubscriber', target: 'InMemoryEventBus', etype: 'inh' } },
    /* ── Usage / dependency (dashed) ───────────────────────────── */
    { data: { source: 'Entity',           target: 'EntityID',        etype: 'use', label: 'identifier' } },
    { data: { source: 'Aggregate',        target: 'DomainEvent',     etype: 'use', label: 'raises' } },
    { data: { source: 'Rule',             target: 'Entity',          etype: 'use', label: 'validates' } },
    { data: { source: 'Rule',             target: 'ValueObject',     etype: 'use', label: 'validates' } },
    { data: { source: 'SpecRule',         target: 'ISpec',           etype: 'use', label: 'delegates to' } },
    { data: { source: 'IRepository',      target: 'ISpec',           etype: 'use', label: 'filter with' } },
    { data: { source: 'UnitOfWork',       target: 'IRepository',     etype: 'use', label: 'scopes session' } },
    { data: { source: 'UnitOfWork',       target: 'IEventPublisher', etype: 'use', label: 'publishes before commit' } },
    { data: { source: 'IEventPublisher',  target: 'DomainEvent',     etype: 'use', label: 'publishes' } },
    { data: { source: 'IEventSubscriber', target: 'IEventHandler',   etype: 'use', label: 'delegate handling to' } },
    { data: { source: 'IEventSubscriber', target: 'DomainEvent',     etype: 'use', label: 'subscribes to' } },
  ];

  var PALETTES = {
    dark: {
      bg: '#0d0d1a',
      activeBg: '#ffffff',
      edgeInh: '#7c8fa8', edgeUse: '#4e6070',
      edgeLabel: '#64748b', edgeSelected: '#94a3b8',
      nodeText: '#e2e8f0',
      cats: {
        core:        { bg: '#1e1035', border: '#7c3aed', text: '#c4b5fd', swatchBg: '#2e1065' },
        rules:       { bg: '#0f2457', border: '#3b82f6', text: '#93c5fd', swatchBg: '#0f2457' },
        specs:       { bg: '#021b1a', border: '#0d9488', text: '#5eead4', swatchBg: '#021b1a' },
        persistence: { bg: '#1c0e04', border: '#b45309', text: '#fcd34d', swatchBg: '#1c0e04' },
        events:      { bg: '#1f0010', border: '#e11d48', text: '#fda4af', swatchBg: '#1f0010' },
      }
    },
    light: {
      bg: '#f1f5f9',
      activeBg: '#000000',
      edgeInh: '#64748b', edgeUse: '#94a3b8',
      edgeLabel: '#475569', edgeSelected: '#334155',
      nodeText: '#1e293b',
      cats: {
        core:        { bg: '#ede9fe', border: '#7c3aed', text: '#5b21b6', swatchBg: '#ede9fe' },
        rules:       { bg: '#dbeafe', border: '#3b82f6', text: '#1d4ed8', swatchBg: '#dbeafe' },
        specs:       { bg: '#ccfbf1', border: '#0d9488', text: '#0f766e', swatchBg: '#ccfbf1' },
        persistence: { bg: '#fef3c7', border: '#b45309', text: '#78350f', swatchBg: '#fef3c7' },
        events:      { bg: '#ffe4e6', border: '#e11d48', text: '#9f1239', swatchBg: '#ffe4e6' },
      }
    }
  };

  function getPalette() {
    return document.body.getAttribute('data-md-color-scheme') === 'default'
      ? PALETTES.light : PALETTES.dark;
  }

  function buildStyle(p) {
    return [
      { selector: 'core', style: {
          'active-bg-color': p.activeBg,
          'active-bg-opacity': 0.15,
          'active-bg-size': 30,
      }},
      { selector: 'node', style: {
          'shape': 'round-rectangle',
          'label': function (e) { return e.data('l') + '\n' + e.data('s'); },
          'text-wrap': 'wrap',
          'text-valign': 'center',
          'text-halign': 'center',
          'font-family': '"Fira Code", "Cascadia Code", monospace',
          'font-size': '12px',
          'line-height': 1.5,
          'width': 'label',
          'height': 'label',
          'min-width': '140px',
          'min-height': '52px',
          'padding': '14px 20px',
          'border-width': '2px',
          'color': p.nodeText,
      }},
      /* category colours */
      { selector: 'node[cat="core"]',        style: { 'background-color': p.cats.core.bg,        'border-color': p.cats.core.border,        'color': p.cats.core.text } },
      { selector: 'node[cat="rules"]',       style: { 'background-color': p.cats.rules.bg,       'border-color': p.cats.rules.border,       'color': p.cats.rules.text } },
      { selector: 'node[cat="specs"]',       style: { 'background-color': p.cats.specs.bg,       'border-color': p.cats.specs.border,       'color': p.cats.specs.text } },
      { selector: 'node[cat="persistence"]', style: { 'background-color': p.cats.persistence.bg, 'border-color': p.cats.persistence.border, 'color': p.cats.persistence.text } },
      { selector: 'node[cat="events"]',      style: { 'background-color': p.cats.events.bg,      'border-color': p.cats.events.border,      'color': p.cats.events.text } },
      /* selected / hover / search */
      { selector: 'node:selected',      style: { 'border-width': '3px', 'border-color': '#fff', 'overlay-opacity': 0 } },
      { selector: 'node.cmap-faded',    style: { 'opacity': 0.12 } },
      { selector: 'edge.cmap-faded',    style: { 'opacity': 0.05 } },
      { selector: 'node.cmap-match',    style: { 'border-width': '3px', 'opacity': 1 } },
      { selector: 'node.cmap-neighbor', style: { 'opacity': 0.5 } },
      /* ── edges – inherit (solid) ────────────────────────────────── */
      { selector: 'edge[etype="inh"]', style: {
          'curve-style': 'bezier',
          'width': 3,
          'line-color': p.edgeInh,
          'target-arrow-color': p.edgeInh,
          'target-arrow-shape': 'triangle',
          'arrow-scale': 3,
      }},
      /* edges – usage (dashed) */
      { selector: 'edge[etype="use"]', style: {
          'curve-style': 'bezier',
          'width': 3,
          'line-color': p.edgeUse,
          'line-style': 'dashed',
          'line-dash-pattern': [6, 3],
          'target-arrow-color': p.edgeUse,
          'target-arrow-shape': 'triangle',
          'arrow-scale': 3,
      }},
      /* edge labels */
      { selector: 'edge[label]', style: {
          'label': 'data(label)',
          'font-size': '21px',
          'font-family': 'system-ui, sans-serif',
          'color': p.edgeLabel,
          'text-background-color': p.bg,
          'text-background-opacity': 1,
          'text-background-padding': '2px',
          'text-rotation': 'autorotate',
      }},
      { selector: 'edge:selected', style: { 'line-color': p.edgeSelected, 'target-arrow-color': p.edgeSelected } },
    ];
  }

  function build(cy_lib) {
    var container = document.getElementById('hike-cmap');
    if (!container) return;

    var cy = cy_lib({
      container: container,
      elements: NODES.concat(EDGES),
      style: buildStyle(getPalette()),
      layout: { name: 'preset' },
      minZoom: 0.2,
      maxZoom: 4,
      wheelSensitivity: 0.15,
    });

    /* seed deterministic grid so cose always starts from the same positions */
    var nodeArr = cy.nodes().toArray();
    var cols = Math.ceil(Math.sqrt(nodeArr.length));
    nodeArr.forEach(function (n, i) {
      n.position({ x: (i % cols) * 250, y: Math.floor(i / cols) * 200 });
    });

    cy.layout({
      name: 'cose',
      animate: false,
      fit: true,
      padding: 30,
      randomize: false,
      componentSpacing: 60,
      nodeRepulsion: function () { return 4500000; },
      nodeOverlap: 20,
      idealEdgeLength: function (edge) {
        var label = edge.data('label') || '';
        /* font-size is 21 graph-units; avg char width ≈ 60% of that = ~12.6 units */
        return Math.max(320, label.length * 12.6 + 84);
      },
      edgeElasticity: function () { return 60; },
      nestingFactor: 1.0,
      gravity: 6,
      numIter: 2500,
      initialTemp: 2000,
      coolingFactor: 0.995,
      minTemp: 1.0,
      nodeDimensionsIncludeLabels: true,
    }).run();

    var positions = cy.nodes().map(function (n) { return n.position(); });
    var canvasAspect = cy.width() / cy.height();
    var bestAngle = 0, bestScore = Infinity;
    for (var deg = 0; deg < 360; deg += 2) {
      var rad = deg * Math.PI / 180;
      var cos = Math.cos(rad), sin = Math.sin(rad);
      var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      positions.forEach(function (p) {
        var rx = p.x * cos - p.y * sin;
        var ry = p.x * sin + p.y * cos;
        if (rx < minX) minX = rx; if (rx > maxX) maxX = rx;
        if (ry < minY) minY = ry; if (ry > maxY) maxY = ry;
      });
      var w = maxX - minX, h = maxY - minY;
      if (!h) continue;
      var score = Math.abs(Math.log((w / h) / canvasAspect));
      if (score < bestScore) { bestScore = score; bestAngle = rad; }
    }
    var bc = Math.cos(bestAngle), bs = Math.sin(bestAngle);
    cy.nodes().forEach(function (n) {
      var p = n.position();
      n.position({ x: p.x * bc - p.y * bs, y: p.x * bs + p.y * bc });
    });
    /* hard-enforce minimum edge length so labels are never clipped by nodes.
       Edge labels use font-size 21 (graph units); char width ~14 units for system-ui.
       Node dims in graph units: min-width 140, h-padding 20 each side. */
    function nodeHalfDiag(node) {
      var lbl = node.data('l') + '\n' + node.data('s');
      var maxCh = Math.max.apply(null, lbl.split('\n').map(function (l) { return l.length; }));
      var w = Math.max(140, maxCh * 7.2) + 40;
      return Math.sqrt(w * w + 80 * 80) / 2;
    }
    for (var pass = 0; pass < 3; pass++) {
      cy.edges('[label]').forEach(function (edge) {
        /* 14 units/char for 21px system-ui, 40 units margin each end */
        var labelW = edge.data('label').length * 14 + 80;
        var needed = labelW + nodeHalfDiag(edge.source()) + nodeHalfDiag(edge.target());
        var s = edge.source().position(), t = edge.target().position();
        var dx = t.x - s.x, dy = t.y - s.y;
        var len = Math.sqrt(dx * dx + dy * dy);
        if (len < needed) {
          var scale = needed / len;
          var mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
          edge.source().position({ x: mx - dx * scale / 2, y: my - dy * scale / 2 });
          edge.target().position({ x: mx + dx * scale / 2, y: my + dy * scale / 2 });
        }
      });
    }

    cy.fit(cy.elements(), 20);
    var initZoom = cy.zoom(), initPan = { x: cy.pan().x, y: cy.pan().y };

    function resetView(duration) {
      cy.animate({ zoom: initZoom, pan: { x: initPan.x, y: initPan.y } }, { duration: duration != null ? duration : 300 });
    }

    var resetBtn = document.getElementById('hike-cmap-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () { resetView(300); });
      cy.on('viewport', function () {
        var atInit = Math.abs(cy.zoom() - initZoom) < 0.005 &&
                     Math.abs(cy.pan().x - initPan.x) < 3 &&
                     Math.abs(cy.pan().y - initPan.y) < 3;
        resetBtn.style.display = atInit ? 'none' : 'flex';
      });
    }

    var fsBtn = document.getElementById('hike-cmap-fullscreen');
    if (fsBtn) {
      var FS_ICON_EXPAND   = '<polyline points="15,3 21,3 21,9"/><polyline points="9,21 3,21 3,15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>';
      var FS_ICON_COMPRESS = '<line x1="21" y1="3" x2="14" y2="10"/><polyline points="20,10 14,10 14,4"/><line x1="3" y1="21" x2="10" y2="14"/><polyline points="4,14 10,14 10,20"/>';

      fsBtn.addEventListener('click', function () {
        var wrap = document.getElementById('hike-cmap-wrap');
        if (!document.fullscreenElement) {
          (wrap.requestFullscreen || wrap.webkitRequestFullscreen).call(wrap);
        } else {
          (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        }
      });

      function onFsChange() {
        var fsIcon = document.getElementById('hike-cmap-fs-icon');
        if (fsIcon) fsIcon.innerHTML = document.fullscreenElement ? FS_ICON_COMPRESS : FS_ICON_EXPAND;
        cy.resize();
        cy.fit(cy.elements(), 20);
        initZoom = cy.zoom();
        initPan = { x: cy.pan().x, y: cy.pan().y };
        if (resetBtn) resetBtn.style.display = 'none';
      }
      document.addEventListener('fullscreenchange', onFsChange);
      document.addEventListener('webkitfullscreenchange', onFsChange);
    }

    var infoBox  = document.getElementById('hike-cmap-info');
    var infoName = document.getElementById('hike-cmap-info-name');
    var infoImp  = document.getElementById('hike-cmap-info-imp');

    var lastTapped = null;
    cy.on('tap', 'node', function (evt) {
      var d = evt.target.data();
      if (lastTapped === d.id) {
        lastTapped = null;
        infoBox.style.display = 'none';
        resetView(300);
        return;
      }
      lastTapped = d.id;
      infoName.textContent = d.l + '  —  ' + d.s;
      infoImp.textContent  = d.imp;
      var CAT_COLORS = { core:'#c4b5fd', rules:'#93c5fd', specs:'#5eead4', persistence:'#fcd34d', events:'#fdba74', messaging:'#f9a8d4', providers:'#9ca3af' };
      infoName.style.color = CAT_COLORS[d.cat] || '#e2e8f0';
      infoBox.style.display = 'block';
      cy.animate({ center: { eles: evt.target }, zoom: Math.max(cy.zoom(), 1.6) }, { duration: 300 });
    });

    cy.on('tap', function (evt) {
      if (evt.target === cy) infoBox.style.display = 'none';
    });

    function fuzzy(query, text) {
      var qi = 0;
      for (var i = 0; i < text.length && qi < query.length; i++) {
        if (text[i] === query[qi]) qi++;
      }
      return qi === query.length;
    }

    var searchEl  = document.getElementById('hike-cmap-search');
    var countEl   = document.getElementById('hike-cmap-search-count');
    var clearBtn  = document.getElementById('hike-cmap-clear');

    function clearSearch() {
      searchEl.value = '';
      if (clearBtn) clearBtn.style.display = 'none';
      cy.elements().removeClass('cmap-faded cmap-match cmap-neighbor');
      if (countEl) countEl.textContent = '';
    }

    if (clearBtn) clearBtn.addEventListener('click', clearSearch);

    if (searchEl) {
      var searchTimer = null;
      searchEl.addEventListener('input', function () {
        if (clearBtn) clearBtn.style.display = searchEl.value ? 'block' : 'none';
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
        var q = searchEl.value.trim().toLowerCase();
        if (!q) {
          cy.elements().removeClass('cmap-faded cmap-match cmap-neighbor');
          if (countEl) countEl.textContent = '';
          resetView(250);
          return;
        }
        var matches = cy.nodes().filter(function (n) {
          return fuzzy(q, n.data('l').toLowerCase()) ||
                 fuzzy(q, n.data('s').toLowerCase());
        });
        var neighbors = matches.neighborhood('node').not(matches);
        cy.nodes().not(matches).not(neighbors).addClass('cmap-faded').removeClass('cmap-match cmap-neighbor');
        neighbors.removeClass('cmap-faded cmap-match').addClass('cmap-neighbor');
        matches.removeClass('cmap-faded cmap-neighbor').addClass('cmap-match');
        cy.edges().addClass('cmap-faded');
        matches.connectedEdges().removeClass('cmap-faded');
        if (countEl) countEl.textContent = matches.length + ' found';
        if (matches.length) cy.animate({ fit: { eles: matches, padding: 80 } }, { duration: 250 });
        else resetView(250);
        }, 500);
      });
    }

    /* ── category legend clicks ─────────────────────────────────── */
    var activeCat = null;
    document.querySelectorAll('[data-cat]').forEach(function (el) {
      el.addEventListener('click', function () {
        var cat = el.getAttribute('data-cat');
        if (activeCat === cat) {
          activeCat = null;
          document.querySelectorAll('[data-cat]').forEach(function (e) { e.style.opacity = ''; });
          cy.elements().removeClass('cmap-faded cmap-match cmap-neighbor');
          if (countEl) countEl.textContent = '';
          resetView(250);
          return;
        }
        activeCat = cat;
        if (searchEl) { searchEl.value = ''; clearTimeout(searchTimer); if (clearBtn) clearBtn.style.display = 'none'; }
        document.querySelectorAll('[data-cat]').forEach(function (e) {
          e.style.opacity = e === el ? '1' : '0.35';
        });
        var matches = cy.nodes().filter(function (n) { return n.data('cat') === cat; });
        var neighbors = matches.neighborhood('node').not(matches);
        cy.nodes().not(matches).not(neighbors).addClass('cmap-faded').removeClass('cmap-match cmap-neighbor');
        neighbors.removeClass('cmap-faded cmap-match').addClass('cmap-neighbor');
        matches.removeClass('cmap-faded cmap-neighbor').addClass('cmap-match');
        cy.edges().addClass('cmap-faded');
        matches.connectedEdges().removeClass('cmap-faded');
        if (countEl) countEl.textContent = matches.length + ' found';
        if (matches.length) cy.animate({ fit: { eles: matches, padding: 80 } }, { duration: 250 });
        else resetView(250);
      });
    });

    /* ── theme switching ────────────────────────────────────────── */
    function applyTheme() {
      var p = getPalette();
      cy.style(buildStyle(p)).update();
      /* update legend swatch backgrounds to match current theme's node colours */
      document.querySelectorAll('[data-cat]').forEach(function (el) {
        var cat = el.getAttribute('data-cat');
        var swatch = el.querySelector('span');
        if (swatch && p.cats[cat]) swatch.style.background = p.cats[cat].swatchBg;
      });
    }

    /* apply on initial load in case page starts in light mode */
    applyTheme();

    new MutationObserver(applyTheme).observe(document.body, {
      attributes: true, attributeFilter: ['data-md-color-scheme']
    });
  }

  function init() {
    if (window.cytoscape) { build(window.cytoscape); return; }
    var s = document.createElement('script');
    s.src = CY_URL;
    s.onload = function () { build(window.cytoscape); };
    document.head.appendChild(s);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
</script>
