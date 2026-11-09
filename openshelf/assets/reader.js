'use strict';
/* Lays out one chapter at a time. Positions are stored as character offsets within
   the chapter, so a saved place survives changes of font, size or window shape. */
window.OS = (() => {
  const $ = id => document.getElementById(id);
  const book = $('os-book'), view = $('os-viewport'), sheet = $('os-bookcss'), pop = $('os-pop');
  const endnav = $('os-endnav');
  const st = {
    mode: 'paged', spread: true, margin: 56, spine: 0, page: 0, pages: 1, step: 1,
    nodes: [], total: 0, anchor: 0, highlights: [], token: 0, shown: false, popCtx: null,
    sticky: false, ignoreScroll: 0, animate: true,
  };
  let bridge = null;
  const send = (type, data) => { if (bridge) bridge.send(JSON.stringify({ type, ...(data || {}) })); };

  /* Text index ------------------------------------------------------------ */
  function indexNodes() {
    st.nodes = [];
    let total = 0;
    const walker = document.createTreeWalker(book, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      st.nodes.push({ n: walker.currentNode, start: total });
      total += walker.currentNode.nodeValue.length;
    }
    st.total = total;
  }

  function locate(offset) {
    let lo = 0, hi = st.nodes.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (st.nodes[mid].start <= offset) lo = mid; else hi = mid - 1;
    }
    return st.nodes[lo];
  }

  /* The first drawn rectangle at or after a character; collapsed whitespace has none. */
  function rectAt(offset) {
    const range = document.createRange();
    for (let c = offset, tries = 0; c < st.total && tries < 400; c++, tries++) {
      const item = locate(c);
      const local = c - item.start;
      if (local >= item.n.nodeValue.length) continue;
      range.setStart(item.n, local);
      range.setEnd(item.n, local + 1);
      const rects = range.getClientRects();
      if (rects.length && (rects[0].width || rects[0].height)) return rects[0];
    }
    return null;
  }

  const bookRect = () => book.getBoundingClientRect();

  function coordOf(rect) {
    const origin = bookRect();
    if (st.mode === 'paged') return Math.floor((rect.left - origin.left + 2) / st.step);
    return rect.top - origin.top;
  }

  /* Lowest character offset whose coordinate is at least `edge`. */
  function firstAt(edge) {
    if (!st.total) return 0;
    let lo = 0, hi = st.total;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const rect = rectAt(mid);
      if (!rect || coordOf(rect) >= edge) hi = mid; else lo = mid + 1;
    }
    return Math.min(lo, Math.max(0, st.total - 1));
  }

  const currentEdge = () => st.mode === 'paged' ? st.page : view.scrollTop;
  const nextEdge = () => st.mode === 'paged' ? st.page + 1 : view.scrollTop + view.clientHeight;

  /* Layout ---------------------------------------------------------------- */
  function layout() {
    const W = innerWidth, H = innerHeight;
    if (st.mode === 'paged') {
      const count = st.spread && W >= 1000 ? 2 : 1;
      const maxColumn = 720;
      const gap = Math.max(48, st.margin);
      let mx = Math.min(st.margin, W * 0.12);
      mx = Math.max(mx, (W - count * maxColumn - (count - 1) * gap) / 2);
      const my = 34;
      const contentW = Math.floor(W - 2 * mx);
      const contentH = Math.floor(H - 2 * my);
      Object.assign(view.style, {
        left: mx + 'px', right: mx + 'px', top: my + 'px', bottom: my + 'px',
        overflow: 'hidden', overflowY: 'hidden',
      });
      Object.assign(book.style, {
        width: contentW + 'px', height: contentH + 'px', columnCount: count, columnGap: gap + 'px',
        maxWidth: 'none', margin: '0', transition: 'none',
      });
      document.documentElement.style.setProperty('--page-h', contentH + 'px');
      endnav.style.display = 'none';
      st.step = contentW + gap;
      st.pages = Math.max(1, Math.ceil((book.scrollWidth + gap - 2) / st.step));
    } else {
      Object.assign(view.style, {
        left: '0', right: '0', top: '0', bottom: '0', overflow: 'hidden auto',
      });
      const column = Math.min(720, W - 2 * Math.min(st.margin, W * 0.12));
      Object.assign(book.style, {
        width: column + 'px', height: 'auto', columnCount: 'auto', columnGap: 'normal',
        margin: '40px auto 0', transform: 'none', transition: 'none',
      });
      endnav.style.display = 'flex';
      endnav.style.width = column + 'px';
      endnav.style.margin = '0 auto';
      document.documentElement.style.setProperty('--page-h', (H - 60) + 'px');
      st.pages = 1; st.page = 0;
    }
  }

  function place(animated) {
    if (st.mode !== 'paged') return;
    st.page = Math.max(0, Math.min(st.page, st.pages - 1));
    book.style.transition = animated && st.animate ? 'transform .24s cubic-bezier(.25,.8,.3,1)' : 'none';
    book.style.transform = `translate3d(${-st.page * st.step}px,0,0)`;
  }

  function gotoOffset(offset) {
    st.ignoreScroll = performance.now() + 400;
    const rect = rectAt(Math.min(offset, Math.max(0, st.total - 1)));
    if (st.mode === 'paged') {
      if (rect) st.page = coordOf(rect);
      place(false);
    } else if (rect) {
      view.scrollTop = Math.max(0, coordOf(rect) - 12);
    }
  }

  let reportTimer = 0;
  function report(now) {
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => {
      if (!st.shown) return;
      const first = firstAt(currentEdge());
      const end = firstAt(nextEdge());
      /* After a reflow the anchor sits mid-page; keeping it stops repeated
         font or window changes from creeping backwards through the book. */
      if (!st.sticky) st.anchor = first;
      const offset = st.anchor;
      const atEnd = st.mode === 'paged' ? st.page >= st.pages - 1
        : view.scrollTop + view.clientHeight >= view.scrollHeight - 4;
      const fraction = atEnd ? 1 : (st.total ? offset / st.total : 0);
      send('position', { spine: st.spine, offset, first, end, page: st.page, pages: st.pages, fraction });
    }, now ? 0 : 90);
  }

  /* Highlights ------------------------------------------------------------ */
  function wrap(start, end, className, id) {
    const segments = [];
    for (const item of st.nodes) {
      const itemEnd = item.start + item.n.nodeValue.length;
      if (itemEnd <= start) continue;
      if (item.start >= end) break;
      segments.push([item.n, Math.max(0, start - item.start), Math.min(item.n.nodeValue.length, end - item.start)]);
    }
    let first = null;
    for (const [node, a, b] of segments) {
      if (a >= b || !node.nodeValue.slice(a, b).trim()) continue;
      const range = document.createRange();
      range.setStart(node, a);
      range.setEnd(node, b);
      const mark = document.createElement('mark');
      mark.className = className;
      if (id != null) mark.dataset.id = id;
      range.surroundContents(mark);
      first = first || mark;
    }
    indexNodes();
    return first;
  }

  function unwrap(selector) {
    book.querySelectorAll(selector).forEach(mark => {
      const parent = mark.parentNode;
      mark.replaceWith(...mark.childNodes);
      parent.normalize();
    });
    indexNodes();
  }

  function paintHighlights() {
    unwrap('mark[data-id]');
    for (const h of st.highlights) {
      wrap(h.start, h.end, `os-${h.color}${h.note ? ' os-noted' : ''}`, h.id);
    }
  }

  function offsetOf(container, local) {
    const range = document.createRange();
    range.selectNodeContents(book);
    range.setEnd(container, local);
    return range.toString().length;
  }

  function showPop(rect, ctx) {
    st.popCtx = ctx;
    $('os-del').hidden = !ctx.id;
    pop.hidden = false;
    const width = pop.offsetWidth, height = pop.offsetHeight;
    const x = Math.max(8, Math.min(innerWidth - width - 8, rect.left + rect.width / 2 - width / 2));
    const above = rect.top - height - 10;
    pop.style.left = x + 'px';
    pop.style.top = (above > 6 ? above : Math.min(innerHeight - height - 6, rect.bottom + 10)) + 'px';
  }

  const hidePop = () => { pop.hidden = true; st.popCtx = null; };

  function selectionContext() {
    const selection = getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!book.contains(range.commonAncestorContainer)) return null;
    const text = range.toString();
    if (!text.trim()) return null;
    return { start: offsetOf(range.startContainer, range.startOffset),
             end: offsetOf(range.endContainer, range.endOffset), text, rect: range.getBoundingClientRect() };
  }

  pop.addEventListener('mousedown', e => e.preventDefault());
  pop.addEventListener('click', e => {
    e.stopPropagation();
    const ctx = st.popCtx;
    if (!ctx) return;
    const color = e.target.dataset && e.target.dataset.color;
    if (color) {
      if (ctx.id) send('hl_color', { id: ctx.id, color });
      else send('hl_add', { spine: st.spine, start: ctx.start, end: ctx.end, text: ctx.text, color });
    } else if (e.target.id === 'os-note') {
      if (ctx.id) send('hl_note', { id: ctx.id });
      else send('hl_add', { spine: st.spine, start: ctx.start, end: ctx.end, text: ctx.text, color: 'yellow', note: true });
    } else if (e.target.id === 'os-copy') {
      send('copy', { text: ctx.text });
    } else if (e.target.id === 'os-del' && ctx.id) {
      send('hl_delete', { id: ctx.id });
    } else {
      return;
    }
    getSelection().removeAllRanges();
    hidePop();
  });

  let suppressClick = false;
  document.addEventListener('mouseup', e => {
    if (pop.contains(e.target)) return;
    const ctx = selectionContext();
    if (ctx) { suppressClick = true; showPop(ctx.rect, ctx); }
  });

  document.addEventListener('click', e => {
    if (pop.contains(e.target)) return;
    if (suppressClick) { suppressClick = false; return; }
    if (!pop.hidden) { hidePop(); return; }
    const link = e.target.closest && e.target.closest('a');
    if (link && book.contains(link)) {
      if (link.dataset.osExt) { e.preventDefault(); send('external', { url: link.dataset.osExt }); return; }
      if (link.dataset.osSpine !== undefined) {
        e.preventDefault();
        send('link', { spine: Number(link.dataset.osSpine), fragment: link.dataset.osFrag || '' });
        return;
      }
    }
    const mark = e.target.closest && e.target.closest('mark[data-id]');
    if (mark) {
      const h = st.highlights.find(x => String(x.id) === mark.dataset.id);
      if (h) { showPop(mark.getBoundingClientRect(), { id: h.id, text: h.text }); return; }
    }
    if (e.target.closest && e.target.closest('button')) return;
    if (st.mode === 'paged') {
      const x = e.clientX / innerWidth;
      if (x < 0.28) prev(); else if (x > 0.72) next();
    }
  });

  /* Navigation ------------------------------------------------------------ */
  function next() {
    hidePop();
    st.sticky = false;
    if (st.mode === 'paged') {
      if (st.page < st.pages - 1) { st.page++; place(true); report(); }
      else send('need_chapter', { spine: st.spine + 1, at: 'start' });
    } else if (view.scrollTop + view.clientHeight >= view.scrollHeight - 4) {
      send('need_chapter', { spine: st.spine + 1, at: 'start' });
    } else {
      view.scrollBy({ top: view.clientHeight * 0.9, behavior: 'smooth' });
    }
  }

  function prev() {
    hidePop();
    st.sticky = false;
    if (st.mode === 'paged') {
      if (st.page > 0) { st.page--; place(true); report(); }
      else send('need_chapter', { spine: st.spine - 1, at: 'end' });
    } else if (view.scrollTop <= 2) {
      send('need_chapter', { spine: st.spine - 1, at: 'end' });
    } else {
      view.scrollBy({ top: -view.clientHeight * 0.9, behavior: 'smooth' });
    }
  }

  $('os-nextch').addEventListener('click', () => send('need_chapter', { spine: st.spine + 1, at: 'start' }));
  $('os-prevch').addEventListener('click', () => send('need_chapter', { spine: st.spine - 1, at: 'start' }));
  view.addEventListener('scroll', () => {
    if (st.mode === 'paged') return;
    if (performance.now() > st.ignoreScroll) st.sticky = false;
    report();
  });

  let wheelSum = 0, wheelLock = 0;
  document.addEventListener('wheel', e => {
    if (st.mode !== 'paged') return;
    const now = performance.now();
    if (now < wheelLock) return;
    wheelSum += Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (Math.abs(wheelSum) >= 50) {
      if (wheelSum > 0) next(); else prev();
      wheelSum = 0;
      wheelLock = now + 260;
    }
  }, { passive: true });

  function fragmentTarget(fragment) {
    const element = fragment && document.getElementById(fragment);
    if (!element || !book.contains(element)) return false;
    const rect = element.getBoundingClientRect();
    if (st.mode === 'paged') { st.page = coordOf(rect); place(false); }
    else view.scrollTop = Math.max(0, coordOf(rect) - 12);
    return true;
  }

  function flash(query, near) {
    const haystack = book.textContent.toLowerCase();
    const needle = query.toLowerCase();
    let at = haystack.indexOf(needle, Math.max(0, near - 200));
    if (at < 0) at = haystack.indexOf(needle);
    if (at < 0) return false;
    wrap(at, at + needle.length, 'os-find');
    gotoOffset(at);
    setTimeout(() => unwrap('mark.os-find'), 3500);
    return true;
  }

  function target(t) {
    if (!t) return;
    if (t.find && flash(t.find, t.offset || 0)) return;
    if (t.fragment && fragmentTarget(t.fragment)) return;
    if (t.at === 'end') {
      if (st.mode === 'paged') { st.page = st.pages - 1; place(false); }
      else view.scrollTop = view.scrollHeight;
      return;
    }
    if (t.fraction !== undefined) { gotoOffset(Math.floor(st.total * t.fraction)); return; }
    if (t.offset !== undefined) { gotoOffset(t.offset); return; }
    st.page = 0; place(false); view.scrollTop = 0;
  }

  async function settleImages() {
    const images = [...book.querySelectorAll('img')];
    if (!images.length) return;
    await Promise.race([
      Promise.all(images.map(img => img.decode ? img.decode().catch(() => {}) : null)),
      new Promise(resolve => setTimeout(resolve, 1800)),
    ]);
  }

  function render(payload) {
    const token = ++st.token;
    const turning = payload.turn && st.animate && st.mode === 'paged' && st.shown
      && typeof document.startViewTransition === 'function';
    if (!turning) return swapChapter(payload, token);
    /* The browser holds the old page on screen until the new chapter is laid out,
       then slides between the two, so a chapter break feels like any other page turn. */
    document.documentElement.dataset.turn = payload.turn;
    const transition = document.startViewTransition(() => swapChapter(payload, token));
    transition.finished.catch(() => {}).finally(() => {
      if (token === st.token) delete document.documentElement.dataset.turn;
    });
  }

  async function swapChapter(payload, token) {
    hidePop();
    st.shown = false;
    st.sticky = false;
    book.style.visibility = 'hidden';
    sheet.textContent = payload.css || '';
    book.innerHTML = payload.html;
    st.spine = payload.spine;
    st.highlights = payload.highlights || [];
    st.page = 0;
    layout();
    place(false);
    await settleImages();
    if (token !== st.token) return;
    indexNodes();
    paintHighlights();
    layout();
    target(payload.target);
    book.style.visibility = 'visible';
    st.shown = true;
    report(true);
  }

  function applySettings(s) {
    const root = document.documentElement.style;
    root.setProperty('--bg', s.bg);
    root.setProperty('--fg', s.fg);
    root.setProperty('--link', s.link);
    root.setProperty('--line', s.line);
    root.setProperty('--fs', s.size + 'px');
    root.setProperty('--lh', s.lineHeight);
    root.setProperty('--font', s.font);
    root.setProperty('--align', s.justify ? 'justify' : 'left');
    const offset = st.shown ? st.anchor : null;
    st.mode = s.mode;
    st.spread = s.spread;
    st.margin = s.margin;
    st.animate = s.animate;
    book.style.transform = 'none';
    layout();
    if (offset !== null) { st.sticky = true; gotoOffset(offset); report(); }
  }

  let resizeTimer = 0;
  addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!st.shown) return;
      const offset = st.anchor;
      st.sticky = true;
      layout();
      gotoOffset(offset);
      report();
    }, 60);
  });

  function setHighlights(list) {
    st.highlights = list;
    paintHighlights();
  }

  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.bridge;
    send('ready');
  });

  return { render, applySettings, next, prev, setHighlights, state: () => ({ ...st, nodes: st.nodes.length }), goTo: target };
})();
