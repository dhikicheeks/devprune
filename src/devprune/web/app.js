(() => {
'use strict';
const $ = s => document.querySelector(s);
const TOKEN = document.body.dataset.token;
const ROOTS = JSON.parse(document.body.dataset.roots || '[]');
const FRAMEWORKS = ['Next.js', 'Node.js', 'Laravel', 'Flutter', 'Global'];
const ICONS = {'Next.js': '▲', 'Node.js': '⬢', 'Laravel': '🔶', 'Flutter': '🐦', 'Global': '🌐'};

const FW_COLOR = {'Next.js': 'c1', 'Node.js': 'c2', 'Laravel': 'c3', 'Flutter': 'c4', 'Global': 'c5'};
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const segs = {};

let items = [];
const sel = new Set();
let cachedTs = null;
let scanPoll = null;
let cleanPoll = null;
let wantFull = false;
let holdInfo = null;   // last /api/trash answer: what DevPrune is holding

const fmt = kb => {
  let b = kb * 1024, i = 0;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  while (b >= 1024 && i < 4) { b /= 1024; i++; }
  return b.toFixed(i ? 1 : 0) + ' ' + u[i];
};
const sum = list => list.reduce((s, i) => s + i.kb, 0);
const ago = ts => {
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  if (m < 1) return 'just now';
  if (m < 60) return m + ' min ago';
  if (m < 1440) return Math.round(m / 60) + ' h ago';
  return Math.round(m / 1440) + ' d ago';
};
const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');
const oldData = () => cachedTs ? 'Data from ' + ago(cachedTs) : 'Previous data';

async function api(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'X-Token': TOKEN, 'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function checkbox(checked, onchange) {
  const c = el('input');
  c.type = 'checkbox';
  c.checked = checked;
  c.onchange = onchange;
  return c;
}

function status(text, cls) {
  if (items.length && cls !== 'res-bad') { $('#banner').textContent = oldData() + ' — ' + text; return; }
  $('#banner').textContent = '';
  const m = el('div', cls || '', text);
  m.id = 'msg';
  $('#out').replaceChildren(m);
}

function jumpTo(fw) {
  const target = [...document.querySelectorAll('.fw')].find(s => s.dataset.fw === fw);
  if (target) target.scrollIntoView({behavior: reduceMotion ? 'auto' : 'smooth', block: 'start'});
}

function makeSeg(fw) {
  const seg = el('button', 'seg ' + FW_COLOR[fw]);
  seg.type = 'button';
  seg.append(el('i'));
  seg.style.flexGrow = '0';
  seg.onclick = () => jumpTo(fw);
  const dot = el('span', 'dot ' + FW_COLOR[fw]);
  const name = el('span', '', fw === 'Global' ? 'Global caches' : fw);
  const size = el('b');
  const leg = el('button', 'lg');
  leg.type = 'button';
  leg.append(dot, name, size);
  leg.onclick = () => jumpTo(fw);
  $('#ovbar').append(seg);
  $('#ovleg').insertBefore(leg, $('#ovnote'));
  return {seg, leg, size};
}

function renderOverview() {
  const box = $('#overview');
  const fws = FRAMEWORKS.filter(f => items.some(i => i.framework === f));
  box.hidden = !fws.length;
  for (const k of Object.keys(segs)) {
    if (!fws.includes(k)) { segs[k].seg.remove(); segs[k].leg.remove(); delete segs[k]; }
  }
  const fresh = fws.filter(fw => !segs[fw]);
  fresh.forEach(fw => { segs[fw] = makeSeg(fw); });
  // transitions need one painted frame between creation and the new value
  requestAnimationFrame(() => {
    for (const fw of fws) {
      const s = segs[fw];
      if (!s) continue; // removed by a newer render before this frame ran
      const list = items.filter(i => i.framework === fw);
      const tot = sum(list);
      const chosen = sum(list.filter(i => sel.has(i.id)));
      s.seg.style.flexGrow = String(Math.max(tot, 1));
      s.seg.firstChild.style.width = (tot ? Math.round(chosen / tot * 100) : 0) + '%';
      s.seg.title = fw + ': ' + fmt(tot) + (chosen ? ' (' + fmt(chosen) + ' selected)' : '');
      s.size.textContent = fmt(tot);
    }
  });
}

function riskSummary(chosen) {
  const n = r => chosen.filter(i => i.risk === r).length;
  const box = $('#selrisk');
  box.replaceChildren();
  box.className = '';
  const careful = n('careful'), medium = n('medium');
  if (!careful && !medium) return;
  const parts = [];
  if (careful) parts.push(plural(careful, 'careful item'));
  if (medium) parts.push(plural(medium, 'medium-risk item'));
  box.className = 'riskbox ' + (careful ? 'careful' : 'medium');
  box.textContent = 'Includes ' + parts.join(' and ') + (careful ? '. Check the notes first.' : '. Dependencies will need reinstalling.');
}

function renderSelected(chosen) {
  const box = $('#sellist');
  box.replaceChildren();
  if (!chosen.length) {
    box.append(el('div', 'empty', 'Nothing selected yet. Tick items on the left, or press “Select safe”.'));
    return;
  }
  [...chosen].sort((a, b) => b.kb - a.kb).forEach(i => {
    const row = el('div', 'sel');
    const info = el('div', 'si');
    const name = el('div', 'sn');
    name.append(el('span', 'rd ' + i.risk), i.label);
    info.append(name, el('div', 'sg', i.group));
    const x = el('button', 'x', '✕');
    x.type = 'button';
    x.title = 'Remove from selection';
    x.setAttribute('aria-label', 'Remove ' + i.label + ' from selection');
    x.onclick = () => { sel.delete(i.id); render(); };
    row.append(info, el('span', 'sv', fmt(i.kb)), x);
    box.append(row);
  });
}

function updateTotals() {
  renderOverview();
  const chosen = items.filter(i => sel.has(i.id));
  const total = sum(items);
  const pct = total ? Math.round(sum(chosen) / total * 100) : 0;
  $('#tot').replaceChildren(
    el('div', 'big', chosen.length ? fmt(sum(chosen)) + ' selected' : 'Nothing selected'),
    el('div', 'sm', chosen.length ? plural(chosen.length, 'item') + ' · ' + pct + '% of ' + fmt(total) + ' found' : fmt(total) + ' found'),
  );
  riskSummary(chosen);
  renderSelected(chosen);
  updateCleanButton();
  $('#clean').disabled = !chosen.length || !!cleanPoll;
}

function updateCleanButton() {
  const dry = $('#dry').checked;
  const perm = $('#perm').checked;
  const chosen = items.filter(i => sel.has(i.id));
  const amount = chosen.length ? ' ' + fmt(sum(chosen)) : '';
  $('#clean').textContent = dry ? 'Simulate' : perm ? 'Delete' + amount + ' permanently' : 'Clean' + amount;
  $('#clean').className = !dry && perm ? 'danger' : '';
}

const collapsed = new Set();
let query = '';

function shownItems() {
  const t = query.trim().toLowerCase();
  if (!t) return items;
  return items.filter(i => (i.label + ' ' + i.group + ' ' + i.framework + ' ' + i.path).toLowerCase().includes(t));
}


function toggleOn(head, key, results) {
  head.onclick = e => {
    if (e.target.tagName === 'INPUT') return;
    collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key);
    render(results);
  };
}

function safeHint(list) {
  const safeKb = sum(list.filter(i => i.risk === 'safe' && i.exists !== false));
  if (!safeKb || safeKb >= sum(list)) return el('span', 'hs');
  return el('span', 'hs', fmt(safeKb) + ' safe');
}

function setAll(list, on) {
  list.forEach(i => on ? sel.add(i.id) : sel.delete(i.id));
}

function render(results) {
  const out = $('#out');
  out.replaceChildren();
  $('#tools').hidden = !items.length;
  const shown = shownItems();
  if (!shown.length) {
    const m = el('div', '', items.length ? 'No items match “' + query.trim() + '”.' : 'Nothing to clean.');
    m.id = 'msg';
    out.append(m);
    updateTotals();
    return;
  }
  const maxKb = Math.max(...items.map(i => i.kb), 1);
  const total = sum(items);
  const tree = {};
  shown.forEach(i => ((tree[i.framework] ??= {})[i.group] ??= []).push(i));
  for (const fw of FRAMEWORKS.filter(f => tree[f])) {
    const groups = tree[fw];
    const all = Object.values(groups).flat();
    const fwKey = 'fw:' + fw;
    const shut = collapsed.has(fwKey);
    const sec = el('section', 'fw ' + FW_COLOR[fw]);
    sec.dataset.fw = fw;
    const head = el('div', 'fwh');
    head.append(
      el('span', 'chev', shut ? '▸' : '▾'),
      checkbox(all.every(i => sel.has(i.id)), e => { setAll(all, e.target.checked); render(results); }),
      el('span', 'fwn', ICONS[fw] + ' ' + (fw === 'Global' ? 'Global caches & system' : fw)),
      el('span', 'cnt', plural(Object.keys(groups).length, fw === 'Global' ? 'group' : 'project') + ' · ' + Math.round(sum(all) / total * 100) + '% of total'),
      el('span', 'sz', fmt(sum(all))),
    );
    toggleOn(head, fwKey, results);
    sec.append(head);
    if (!shut) {
      const order = Object.keys(groups).sort((a, b) => sum(groups[b]) - sum(groups[a]));
      for (const g of order) {
        const list = groups[g];
        const gKey = 'g:' + fw + '/' + g;
        const gShut = collapsed.has(gKey);
        const card = el('div', 'card grp');
        const gh = el('div', 'gh');
        gh.append(
          el('span', 'chev', gShut ? '▸' : '▾'),
          checkbox(list.every(i => sel.has(i.id)), e => { setAll(list, e.target.checked); render(results); }),
          el('span', 'gn', g),
          el('span', 'cnt', plural(list.length, 'item')),
          safeHint(list),
          el('span', 'sz', fmt(sum(list))),
        );
        toggleOn(gh, gKey, results);
        card.append(gh);
        if (!gShut) {
          for (const i of list) {
            const row = el('div', 'item' + (i.exists === false ? ' gone' : ''));
            row.title = i.path + (i.note ? '\n' + i.note : '');
            const main = el('div', 'main');
            const line = el('div', 'line');
            const t = el('span', 't');
            t.append(el('span', 'rd ' + i.risk), i.label);
            line.append(t);
            if (i.risk !== 'safe') line.append(el('span', 'tag ' + i.risk, i.risk));
            if (i.exists === false) line.append(el('span', 'tag none', 'not found'));
            if (!canStage(i)) {
              const nu = el('span', 'tag none', 'no undo');
              nu.title = 'Cleaned by a tool command or emptied for real, so it cannot be restored';
              line.append(nu);
            }
            line.append(el('span', 'n', i.note));
            main.append(line);
            const res = results && results.find(x => x.id === i.id);
            if (res) main.append(el('div', 'res ' + (res.ok ? 'res-ok' : 'res-bad'), res.msg + (res.kb ? ' (' + fmt(res.kb) + ')' : '')));
            const size = el('div', 'size');
            const bar = el('span', 'meter');
            const fill = el('i');
            fill.style.width = Math.max(3, Math.round(i.kb / maxKb * 100)) + '%';
            bar.append(fill);
            size.append(bar, el('span', 'v', fmt(i.kb)));
            row.append(checkbox(sel.has(i.id), e => { e.target.checked ? sel.add(i.id) : sel.delete(i.id); updateTotals(); }), main, size);
            card.append(row);
          }
        }
        sec.append(card);
      }
    }
    out.append(sec);
  }
  updateTotals();
}

// ---------------------------------------------------------------- scanning
function scanIdle() {
  const b = $('#scan');
  b.textContent = 'Scan';
  b.className = '';
  b.disabled = false;
  clearInterval(scanPoll);
  scanPoll = null;
}

function attachScan() {
  const b = $('#scan');
  b.textContent = 'Cancel';
  b.className = 'danger';
  $('#clean').disabled = true;
  status('refreshing…');
  scanPoll = setInterval(async () => {
    try {
      const d = await api('/api/status');
      if (d.state === 'running') {
        status('refreshing: ' + d.phase + (d.total ? ' (' + d.done + '/' + d.total + ')' : ''));
        return;
      }
      scanIdle();
      if (d.state === 'done') {
        items = d.items;
        cachedTs = Date.now() / 1000;
        sel.clear();
        $('#banner').textContent = '';
        render();
      } else if (d.state === 'cancelled') {
        status('refresh cancelled.');
        updateTotals();
      } else {
        status('Scan failed: ' + d.error, 'res-bad');
      }
    } catch (e) {
      scanIdle();
      status('Scan failed: ' + e.message, 'res-bad');
    }
  }, 500);
}

async function scanClicked() {
  const full = wantFull;
  wantFull = false;
  const b = $('#scan');
  if (scanPoll) {
    b.disabled = true;
    b.textContent = 'Cancelling…';
    await api('/api/cancel');
    return;
  }
  try {
    const roots = $('#roots').value.split('\n').map(s => s.trim()).filter(Boolean);
    const r = await api('/api/scan', {roots, full});
    if (!r.started) {
      toast('Another operation is still running. Try again in a moment.', 'warn');
      return;
    }
    attachScan();
  } catch (e) {
    scanIdle();
    status('Scan failed: ' + e.message, 'res-bad');
  }
}

// ---------------------------------------------------------------- dialogs & toasts
const dlg = $('#dlg');
dlg.addEventListener('cancel', e => { if (dlg.dataset.phase === 'progress') e.preventDefault(); });

function openDialog(phase, node) {
  dlg.dataset.phase = phase;
  dlg.replaceChildren(node);
  if (!dlg.open) dlg.showModal();
}

function closeDialog() { if (dlg.open) dlg.close(); }

function toast(text, kind) {
  const t = el('div', 'toast ' + (kind || 'info'));
  t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  const x = el('button', 'x', '✕');
  x.type = 'button';
  x.setAttribute('aria-label', 'Dismiss');
  x.onclick = () => t.remove();
  t.append(el('span', '', text), x);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), 7000);
}

function button(text, cls, onclick) {
  const b = el('button', cls || '', text);
  b.type = 'button';
  b.onclick = onclick;
  return b;
}

function dlgHead(kind, title, sub) {
  const head = el('div', 'dhead');
  const text = el('div', 'dtext');
  text.append(el('div', 'dtitle', title), el('div', 'dsub', sub || ''));
  head.append(el('div', 'badge ' + kind, {ok: '✓', warn: '!', err: '✕', info: 'i', run: ''}[kind]), text);
  return head;
}

function dlgButtons(...btns) {
  const row = el('div', 'dbtns');
  row.append(...btns);
  return row;
}

function itemList(list, max) {
  const box = el('div', 'plist');
  list.slice(0, max).forEach(i => {
    const row = el('div', 'prow');
    const name = el('div', 'pn');
    name.append(el('span', 'rd ' + i.risk), i.label, el('span', 'pg', ' · ' + i.group));
    row.append(name, el('span', 'pv', fmt(i.kb)));
    box.append(row);
  });
  if (list.length > max) box.append(el('div', 'prow more', '+ ' + plural(list.length - max, 'more item')));
  return box;
}

// A confirmation dialog whose body `build` fills in. Resolves true on confirm and false on Cancel/Esc.
// The dialog is replaced in place by whatever comes next, never closed and reopened (D-17).
function askConfirm(build, okText, danger) {
  return new Promise(resolve => {
    let settled = false;
    const settle = v => { if (!settled) { settled = true; resolve(v); } };
    // a 'close' that arrives while a dialog is open belongs to an earlier dialog, so ignore it
    dlg.addEventListener('close', () => { if (!dlg.open) settle(false); }, {once: true});
    const box = el('div', 'dbox');
    build(box);
    const cancel = button('Cancel', 'sec', () => { settle(false); closeDialog(); });
    box.append(dlgButtons(cancel, button(okText, danger ? 'danger' : '', () => settle(true))));
    openDialog('confirm', box);
    cancel.focus();
  });
}

// Tool-command items and the macOS Trash are always deleted for real; everything else can be moved aside.
const canStage = i => !i.has_cmd && i.can_stage !== false;

function confirmClean(chosen, perm) {
  const moved = perm ? [] : chosen.filter(canStage);
  const gone = chosen.filter(i => perm || !canStage(i));
  const allGone = gone.length > 0 && !moved.length;
  let badge, sub;
  if (!gone.length) {
    badge = 'info';
    sub = fmt(sum(moved)) + ' will be moved aside by DevPrune. You can restore it until you empty it; disk space is only freed when it is emptied.';
  } else if (allGone) {
    badge = 'warn';
    sub = fmt(sum(gone)) + ' will be removed permanently. This cannot be undone.';
  } else {
    badge = 'warn';
    sub = fmt(sum(moved)) + ' will be moved aside (undo possible). ' + fmt(sum(gone)) + ' will be deleted permanently (no undo).';
  }
  return askConfirm(box => {
    box.append(dlgHead(badge, (allGone ? 'Delete ' : 'Clean ') + plural(chosen.length, 'item') + '?', sub));
    const careful = chosen.filter(i => i.risk === 'careful').length;
    const medium = chosen.filter(i => i.risk === 'medium').length;
    if (careful || medium) {
      const parts = [];
      if (careful) parts.push(plural(careful, 'careful item'));
      if (medium) parts.push(plural(medium, 'medium-risk item'));
      box.append(el('div', 'riskbox ' + (careful ? 'careful' : 'medium'), 'Includes ' + parts.join(' and ') + '. Check the notes first.'));
    }
    if (moved.length && gone.length) {
      box.append(el('div', 'dsec', 'Moved aside, can be restored'), itemList(moved, 5),
        el('div', 'dsec', 'Deleted permanently'), itemList(gone, 5));
    } else {
      box.append(itemList(chosen, 6));
      if (!perm && gone.length) box.append(el('p', 'dnote', 'Items cleaned by a tool command (pnpm, npm, brew…) or the macOS Trash cannot be undone.'));
    }
  }, (allGone ? 'Delete ' : 'Clean ') + fmt(sum(chosen)), gone.length > 0);
}

const PROGRESS_TITLE = {
  clean: (dry, perm) => dry ? 'Simulating…' : perm ? 'Deleting…' : 'Cleaning…',
  empty: () => 'Emptying…',
  restore: () => 'Restoring…',
};

function showProgress(kind, dry, chosen, resumed, perm) {
  const box = el('div', 'dbox');
  const head = dlgHead('run', PROGRESS_TITLE[kind](dry, perm), 'Starting…');
  const sub = head.querySelector('.dsub');
  const bar = el('div', 'pbar');
  const fill = el('i', 'pfill');
  const pulse = el('i', 'ppulse');
  bar.append(fill, pulse);
  const stats = el('div', 'pstats');
  const list = el('div', 'plist live');
  const rows = chosen.map(i => {
    const row = el('div', 'prow queued');
    const main = el('div', 'pmain');
    const name = el('div', 'pn');
    name.append(i.label, el('span', 'pg', ' · ' + i.group));
    const msg = el('div', 'pm');
    main.append(name, msg);
    const st = el('span', 'st');
    row.append(st, main, el('span', 'pv', fmt(i.kb)));
    list.append(row);
    return {row, st, msg};
  });
  const stop = button('Stop after this item', 'sec', async () => {
    stop.disabled = true;
    stop.textContent = 'Stopping…';
    try { await api('/api/clean_stop'); } catch (e) { /* the poll will report the real state */ }
  });
  box.append(head, bar, stats);
  if (resumed) box.append(el('p', 'dnote', 'Reconnected: this was already running before the page was reloaded.'));
  box.append(list, dlgButtons(stop));
  openDialog('progress', box);

  return {
    update(d, secs) {
      const res = d.results || [];
      const total = d.total || chosen.length;
      const running = d.state === 'running';
      fill.style.width = (total ? res.length / total * 100 : 0) + '%';
      pulse.style.left = (total ? res.length / total * 100 : 0) + '%';
      pulse.style.width = (running && total ? 100 / total : 0) + '%';
      sub.textContent = running && d.current ? 'Working on ' + d.current : 'Finishing…';
      const parts = [res.length + ' of ' + total];
      if (kind === 'empty') parts.push('freed ' + fmt(d.freed_kb || 0));
      if (kind === 'clean') {
        if (d.freed_kb || !d.held_kb) parts.push((dry ? 'would free ' : 'freed ') + fmt(d.freed_kb || 0));
        if (d.held_kb) parts.push((dry ? 'would move aside ' : 'moved aside ') + fmt(d.held_kb));
      }
      parts.push(secs + ' s');
      stats.textContent = parts.join(' · ');
      rows.forEach((r, k) => {
        const x = res[k];
        const state = x ? (x.ok ? 'ok' : 'fail') : (running && k === res.length ? 'run' : 'queued');
        if (r.row.dataset.state === state) return;
        r.row.dataset.state = state;
        r.row.className = 'prow ' + state;
        r.st.textContent = {ok: '✓', fail: '✕'}[state] || '';
        r.msg.textContent = x && !x.ok ? x.msg : '';
        if (state === 'run') r.row.scrollIntoView({block: 'nearest'});
      });
    },
  };
}

function problemList(bad, byId) {
  const problems = el('div', 'plist bad');
  bad.forEach(r => {
    const i = byId.get(r.id);
    const row = el('div', 'prow');
    const main = el('div', 'pmain');
    main.append(el('div', 'pn', i ? i.label + ' · ' + i.group : r.id), el('div', 'pm', r.msg));
    row.append(el('span', 'st fail', '✕'), main);
    problems.append(row);
  });
  return problems;
}

function showResult(d, kind, dry, chosen, secs, again) {
  if (kind !== 'clean') { showTrashResult(d, kind, chosen, secs); return; }
  const box = el('div', 'dbox');
  if (d.state === 'error') {
    box.append(dlgHead('err', 'Cleaning failed', d.error || 'Unknown error'), dlgButtons(button('Close', '', closeDialog)));
    openDialog('result', box);
    return;
  }
  const byId = new Map(chosen.map(i => [i.id, i]));
  const res = d.results || [];
  const good = res.filter(r => r.ok);
  const bad = res.filter(r => !r.ok);
  const notRun = chosen.length - res.length;
  const held = d.held_kb || 0;
  const freed = fmt(d.freed_kb || 0);
  // moved aside is not freed: say which is which
  const outcome = !held ? 'Freed ' + freed : (d.freed_kb ? 'Moved aside ' + fmt(held) + ', freed ' + freed : 'Moved aside ' + fmt(held));
  let badge, title, sub;
  if (dry) {
    badge = 'info';
    title = 'Simulation finished';
    sub = (held ? 'Would move aside ' + fmt(held) + (d.freed_kb ? ' and free ' + freed : '') : 'Would free ' + freed) +
      ' from ' + plural(good.length, 'item') + '. Nothing was changed.';
  } else if (d.state === 'stopped') {
    badge = 'warn';
    title = 'Stopped after ' + res.length + ' of ' + chosen.length;
    sub = outcome + ' before stopping.';
  } else if (!bad.length) {
    badge = 'ok';
    title = outcome;
    sub = plural(good.length, 'item') + ' cleaned in ' + secs + ' s.' + (held ? ' Disk space is freed when you empty what DevPrune is holding.' : '');
  } else if (good.length) {
    badge = 'warn';
    title = outcome + ', ' + bad.length + ' failed';
    sub = plural(good.length, 'item') + ' cleaned, ' + plural(bad.length, 'item') + ' could not be.';
  } else {
    badge = 'err';
    title = 'Nothing could be cleaned';
    sub = plural(bad.length, 'item') + ' failed.';
  }
  box.append(dlgHead(badge, title, sub));

  const chips = el('div', 'chips');
  chips.append(el('span', 'chip ok', '✓ ' + good.length + (dry ? ' simulated' : ' cleaned')));
  if (bad.length) chips.append(el('span', 'chip err', '✕ ' + bad.length + ' failed'));
  if (notRun > 0) chips.append(el('span', 'chip', '○ ' + notRun + ' not processed'));
  box.append(chips);

  if (bad.length) box.append(problemList(bad, byId), el('p', 'dnote', 'Failed items stay selected so you can try again.'));
  if (good.length) {
    const more = el('details', 'dd');
    more.append(el('summary', '', 'Show ' + plural(good.length, 'item')));
    more.append(itemList(good.map(r => ({...byId.get(r.id), kb: dry ? byId.get(r.id).kb : (r.kb || r.held_kb || 0)})).sort((a, b) => b.kb - a.kb), 12));
    box.append(more);
  }
  const stagedCount = good.filter(r => r.staged).length;
  const holding = !dry && held > 0 && d.run && stagedCount > 0;
  if (holding) box.append(el('p', 'dnote', 'DevPrune keeps these until you empty them. You can restore or empty them any time from the bar at the top of the page.'));
  const offerReal = dry && good.length && again;
  const btns = [button('Close', offerReal || holding ? 'sec' : '', closeDialog)];
  if (offerReal) {
    const perm = $('#perm').checked;
    btns.push(button(perm ? 'Delete for real…' : 'Clean for real…', perm ? 'danger' : '', () => {
      // no closeDialog(): the confirm dialog replaces this one in place
      $('#dry').checked = false;
      updateCleanButton();
      $('#clean').click();
    }));
  }
  if (holding) {
    btns.push(button('Restore', 'sec', () => restoreFlow(d.run, null)));
    btns.push(button('Empty now · frees ' + fmt(held), 'danger', () => emptyFlow(d.run, null, {count: stagedCount, kb: held})));
  }
  box.append(dlgButtons(...btns));
  openDialog('result', box);
  box.querySelector('.dbtns button').focus();  // Close, never a destructive button
}

function showTrashResult(d, kind, chosen, secs) {
  const box = el('div', 'dbox');
  if (d.state === 'error') {
    box.append(dlgHead('err', kind === 'empty' ? 'Emptying failed' : 'Restoring failed', d.error || 'Unknown error'),
      dlgButtons(button('Close', '', closeDialog)));
    openDialog('result', box);
    return;
  }
  const byId = new Map(chosen.map(i => [i.id, i]));
  const res = d.results || [];
  const good = res.filter(r => r.ok);
  const bad = res.filter(r => !r.ok);
  const empty = kind === 'empty';
  const did = empty ? 'Freed ' + fmt(d.freed_kb || 0) : 'Restored ' + plural(good.length, 'item');
  let badge, title, sub;
  if (d.state === 'stopped') {
    badge = 'warn';
    title = 'Stopped after ' + res.length + ' of ' + chosen.length;
    sub = did + ' before stopping.';
  } else if (!bad.length) {
    badge = 'ok';
    title = did;
    sub = empty ? plural(good.length, 'item') + ' emptied in ' + secs + ' s.' : 'They are back where they were. Scan again to see them in the list.';
  } else if (good.length) {
    badge = 'warn';
    title = did + ', ' + bad.length + ' failed';
    sub = plural(bad.length, 'item') + ' could not be ' + (empty ? 'emptied' : 'restored') + ' and stay in DevPrune.';
  } else {
    badge = 'err';
    title = empty ? 'Nothing could be emptied' : 'Nothing could be restored';
    sub = plural(bad.length, 'item') + ' failed.';
  }
  box.append(dlgHead(badge, title, sub));
  if (bad.length) box.append(problemList(bad, byId));
  const btns = [button('Close', d.rescan ? 'sec' : '', closeDialog)];
  if (d.rescan) btns.push(button('Scan now', '', () => { closeDialog(); $('#scan').click(); }));
  box.append(dlgButtons(...btns));
  openDialog('result', box);
  box.querySelector('.dbtns button').focus();
}

// ---------------------------------------------------------------- cleaning
function setBusy(on) {
  $('#scan').disabled = on;
  $('#full').disabled = on;
  if (on) $('#clean').disabled = true;
}

const busy = () => !!(cleanPoll || scanPoll);

// Follow a job (clean, empty or restore) until it ends. `fresh` is true when this page started it.
function followJob(kind, dry, chosen, fresh, perm) {
  const view = showProgress(kind, dry, chosen, !fresh, perm);
  setBusy(true);
  let polling = false;
  const finish = () => {
    clearInterval(cleanPoll);
    cleanPoll = null;
    setBusy(false);
    updateCleanButton();
    updateTotals();
  };
  cleanPoll = setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      const d = await api('/api/clean_status');
      const secs = Math.max(0, Math.round(Date.now() / 1000 - d.started));
      view.update(d, secs);
      if (d.state === 'running') return;
      finish();
      api('/api/clean_ack').catch(() => {});
      showResult(d, kind, dry, chosen, secs, fresh);
      refreshHold();
      if (d.state === 'error' || kind !== 'clean') return;
      if (!dry) {
        // drop what was cleaned first, so the list and summary reflect reality when the dialog closes;
        // failed items stay (still selected) with their error shown inline
        const okIds = new Set(d.results.filter(x => x.ok).map(x => x.id));
        items = items.filter(i => !okIds.has(i.id));
        okIds.forEach(id => sel.delete(id));
      }
      render(d.results);
    } catch (e) {
      finish();
      closeDialog();
      toast('Lost connection to DevPrune: ' + e.message, 'error');
    } finally {
      polling = false;
    }
  }, 400);
}

async function followFromStatus() {
  const c = await api('/api/clean_status');
  followJob(c.kind || 'clean', c.dry, c.plan, true, c.permanent);
}

async function cleanClicked() {
  const dry = $('#dry').checked;
  const perm = $('#perm').checked;
  const chosen = items.filter(i => sel.has(i.id)).sort((a, b) => b.kb - a.kb);
  if (!chosen.length) return;
  if (!dry && !(await confirmClean(chosen, perm))) return;
  setBusy(true);
  try {
    const r = await api('/api/clean', {ids: chosen.map(i => i.id), dry, permanent: perm});
    if (!r.started) {
      closeDialog();
      toast('Another operation is still running. Try again in a moment.', 'warn');
      setBusy(false);
      updateTotals();
      return;
    }
    followJob('clean', dry, chosen, true, perm);
  } catch (e) {
    closeDialog();
    toast('Could not start cleaning: ' + e.message, 'error');
    setBusy(false);
    updateTotals();
  }
}

// ---------------------------------------------------------------- what DevPrune is holding (D-19)
const BUSY_MSG = 'Another operation is still running. Try again in a moment.';

function renderHold(t) {
  holdInfo = t;
  const bar = $('#hold');
  bar.replaceChildren();
  bar.hidden = !t.count;
  if (!t.count) return;
  const old = t.oldest_days > 7;
  bar.className = 'card holdbar' + (old ? ' old' : '');
  const oldest = Math.min(...t.runs.map(r => r.created || Date.now() / 1000));
  const text = el('div', 'holdtxt');
  text.append(el('b', '', 'DevPrune is holding ' + plural(t.count, 'item') + (t.kb ? ' (' + fmt(t.kb) + ')' : '')),
    el('span', 'age', ' · from ' + ago(oldest) + (old ? ' · consider emptying it' : '')));
  const acts = el('div', 'holdacts');
  acts.append(button('Review…', 'sec sm', reviewDialog),
    button('Restore all', 'sec sm', () => restoreFlow(null, null)),
    button('Empty…', 'sm', () => emptyFlow(null, null)));
  bar.append(text, acts);
}

async function refreshHold() {
  try { renderHold(await api('/api/trash')); } catch (e) { /* keep what is shown */ }
}

async function emptyFlow(run, ids, info) {
  if (busy()) { toast(BUSY_MSG, 'warn'); return; }
  const n = info ? info.count : (holdInfo ? holdInfo.count : 0);
  const kb = info ? info.kb : (holdInfo ? holdInfo.kb : 0);
  const body = {};
  if (run) body.run = run;
  if (ids) body.ids = ids;
  const yes = await askConfirm(box => {
    box.append(dlgHead('warn', 'Empty ' + plural(n, 'item') + '?',
      fmt(kb) + ' will be deleted permanently and the disk space freed. This cannot be undone.'));
  }, 'Empty ' + fmt(kb), true);
  if (!yes) return;
  try {
    const r = await api('/api/trash_empty', body);
    if (!r.started) { closeDialog(); toast(r.error || 'Could not start.', 'warn'); return; }
    await followFromStatus();
  } catch (e) {
    closeDialog();
    toast('Could not empty: ' + e.message, 'error');
  }
}

async function restoreFlow(run, ids) {
  if (busy()) { toast(BUSY_MSG, 'warn'); return; }
  const body = {};
  if (run) body.run = run;
  if (ids) body.ids = ids;
  try {
    const c = await api('/api/trash_conflicts', body);
    if (!c.ok) { toast(c.error || 'Could not check.', 'error'); return; }
    if (c.items.length) {
      const yes = await askConfirm(box => {
        box.append(dlgHead('warn', plural(c.items.length, 'item') + (c.items.length === 1 ? ' already exists' : ' already exist'),
          (c.items.length === 1 ? 'This was' : 'These were') + ' probably created again after DevPrune moved the old ' + (c.items.length === 1 ? 'one' : 'ones') +
          ' aside. Restoring will delete the current version permanently and put back the one DevPrune is holding.'));
        const list = el('div', 'plist');
        c.items.slice(0, 8).forEach(x => {
          const row = el('div', 'prow');
          const name = el('div', 'pn');
          name.append(x.label, el('span', 'pg', ' · ' + x.group));
          name.title = x.path;
          row.append(name, el('span', 'pv', ''));
          list.append(row);
        });
        if (c.items.length > 8) list.append(el('div', 'prow more', '+ ' + plural(c.items.length - 8, 'more item')));
        box.append(list);
      }, 'Replace and restore', true);
      if (!yes) return;
      body.replace = true;
    }
    const r = await api('/api/trash_restore', body);
    if (!r.started) { closeDialog(); toast(r.error || 'Could not start.', 'warn'); return; }
    await followFromStatus();
  } catch (e) {
    closeDialog();
    toast('Could not restore: ' + e.message, 'error');
  }
}

async function reviewDialog() {
  try {
    const t = await api('/api/trash');
    renderHold(t);
    if (!t.count) { toast('DevPrune is not holding anything.', 'info'); return; }
    const box = el('div', 'dbox');
    box.append(dlgHead('info', 'DevPrune is holding ' + plural(t.count, 'item'),
      (t.kb ? fmt(t.kb) + ' moved aside. ' : '') + 'Restore puts an item back; Empty deletes it for good.'));
    for (const run of t.runs) {
      const e = await api('/api/trash_entries', {run: run.id});
      box.append(el('div', 'dsec', 'Moved aside ' + ago(run.created)));
      const list = el('div', 'plist');
      (e.items || []).forEach(it => {
        const row = el('div', 'rrow');
        const name = el('div', 'rname pn');
        name.append(it.label, el('span', 'pg', ' · ' + it.group));
        name.title = it.orig || '';
        const acts = el('div', 'ra');
        const restore = button('Restore', 'sec sm', () => restoreFlow(run.id, [it.id]));
        restore.disabled = !it.restorable;
        acts.append(restore, button('Empty', 'sec sm', () => emptyFlow(run.id, [it.id], {count: 1, kb: it.kb})));
        row.append(name, el('span', 'pv', it.kb ? fmt(it.kb) : ''), acts);
        if (!it.restorable) row.append(el('div', 'pm', it.problem));
        list.append(row);
      });
      box.append(list);
    }
    box.append(dlgButtons(button('Close', '', closeDialog)));
    openDialog('review', box);
    box.querySelector('.dbtns button').focus();
  } catch (e) {
    toast('Could not load what DevPrune is holding: ' + e.message, 'error');
  }
}

// A page that was reloaded (or opened later) picks up where the server is: a running job or scan, or an
// outcome nobody has seen yet. Returns true when it attached to something.
async function reattach() {
  const c = await api('/api/clean_status');
  const kind = c.kind || 'clean';
  if (c.state === 'running' && c.plan.length) {
    followJob(kind, c.dry, c.plan, false, c.permanent);
    return true;
  }
  if (['done', 'stopped', 'error'].includes(c.state) && !c.acked && c.plan.length) {
    api('/api/clean_ack').catch(() => {});
    showResult(c, kind, c.dry, c.plan, Math.max(0, Math.round(c.finished - c.started)), false);
  }
  const s = await api('/api/status');
  if (s.state === 'running') {
    attachScan();
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- terminal command
function cmdMessage(text, cls) {
  const m = $('#cmdmsg');
  m.textContent = text;
  m.className = cls || '';
}

function renderCommands(st) {
  const list = $('#cmdlist');
  list.replaceChildren();
  st.commands.forEach(name => {
    const row = el('div', 'cmd');
    const rm = el('button', 'sec', 'Remove');
    rm.onclick = () => cmdCall('/api/command_remove', name);
    row.append(el('code', '', name), rm);
    list.append(row);
  });
  if (st.commands.length && !st.on_path) {
    list.append(el('p', 'hint', 'Add ' + st.dir + ' to your PATH to use it, e.g. add this line to ~/.zshrc: export PATH="$HOME/.local/bin:$PATH"'));
  }
}

async function cmdCall(path, name) {
  try {
    const r = await api(path, {name});
    if (!r.ok) { cmdMessage(r.error, 'res-bad'); return; }
    renderCommands(r);
    const made = path === '/api/command_set';
    cmdMessage(made ? (r.on_path ? 'Created. Open a new terminal tab and type: ' + name : 'Created in ' + r.dir + '.') : 'Removed: ' + name, 'res-ok');
    if (made) $('#cmdname').value = '';
  } catch (e) {
    cmdMessage('Failed: ' + e.message, 'res-bad');
  }
}

$('#cmdset').onclick = () => cmdCall('/api/command_set', $('#cmdname').value.trim());
$('#cmdname').onkeydown = e => { if (e.key === 'Enter') $('#cmdset').click(); };
api('/api/command').then(renderCommands).catch(() => {});

// ---------------------------------------------------------------- wiring
$('#roots').value = ROOTS.join('\n');
$('#q').oninput = e => { query = e.target.value; render(); };
$('#collapse').onclick = () => {
  items.forEach(i => collapsed.add('g:' + i.framework + '/' + i.group));
  render();
};
$('#expand').onclick = () => { collapsed.clear(); render(); };
$('#gear').onclick = () => {
  const s = $('#settings');
  s.hidden = !s.hidden;
  $('#gear').setAttribute('aria-expanded', String(!s.hidden));
};
$('#scan').onclick = scanClicked;
$('#full').onclick = () => { if (scanPoll || cleanPoll) return; wantFull = true; $('#scan').click(); };
$('#dry').onchange = updateCleanButton;
$('#perm').onchange = updateCleanButton;
$('#all').onclick = () => { items.forEach(i => { if (i.risk === 'safe' && i.exists !== false) sel.add(i.id); }); render(); };
$('#none').onclick = () => { sel.clear(); render(); };
$('#clean').onclick = cleanClicked;
updateCleanButton();

(async () => {
  let haveCache = false;
  try {
    const c = await api('/api/cache');
    if (c.items && c.items.length) {
      items = c.items;
      cachedTs = c.ts;
      render();
      $('#banner').textContent = oldData() + ' · not refreshed, click Scan to update';
      haveCache = true;
    }
  } catch (e) { /* no cache: fall through to a scan */ }
  refreshHold();
  try {
    if (await reattach()) return;
  } catch (e) { /* the server will answer the next click */ }
  if (!haveCache) $('#scan').click();
})();
})();
