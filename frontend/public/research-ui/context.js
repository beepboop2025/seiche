/* VENDORED. Source: beepboop2025/liquilens-site research-ui/context.js at commit
   dfb8f0899344b473c179d48ca08e94220333e868 (branch feat/site-redesign-20261112, draft PR #223), blob
   177f59d5b8cf9b0e620809f65464b6dbaf12a847. Every byte after this comment is that blob,
   unedited. Do not edit here: re-vendor from the source (and update this header) whenever
   the family file changes. On Seiche pages it reads no API: return link, prefetch, current tab. */
/* One institution context across every tab.
 *
 * A reader who opens an institution anywhere carries it in ?institution=<slug>.
 * Header links keep it, and LiquiLens pages show a context bar that opens the
 * same institution in each tab that can read it. Pages also prefetch the next
 * page on intent and keep the current tab in view, so moving between tabs is
 * quick. Names come from public files or the ontology and fall back to the slug.
 */
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const ONTOLOGY_API = 'https://api.liquilens.in/api/experimental/v1/ontology';
const NAME_KEY = 'liquilens.context-name.v1:';

export function readInstitutionContext(href) {
  try {
    const values = new URL(href).searchParams.getAll('institution');
    return values.length === 1 && SLUG.test(values[0])
      && values[0].length <= 96 ? values[0] : null;
  } catch { return null; }
}

/** The tabs that can open one institution, in reading order. Pure, so it is tested without a browser. */
export function contextDestinations(slug, {snapshots = []} = {}) {
  if (typeof slug !== 'string' || !SLUG.test(slug) || slug.length > 96) return [];
  const id = encodeURIComponent(slug);
  const rows = [
    {key: 'evidence', label: 'Evidence', path: '/', href: `/?institution=${id}#institution-title`},
    {key: 'banking', label: 'Banking research', path: '/banking/', href: `/banking/?institution=${id}`},
    {key: 'monitoring', label: 'Monitoring', path: '/banking/monitoring/', href: `/banking/monitoring/?institution=${id}`},
  ];
  if (snapshots.includes(slug)) {
    rows.push({key: 'snapshot', label: 'Filing snapshot', path: `/banking/institutions/${slug}/`, href: `/banking/institutions/${id}/`});
  }
  rows.push({key: 'ontology', label: 'Ontology record', path: '/ontology/', href: `/ontology/?q=${id}#lookup`});
  return rows;
}

/** Whether a link is a same-site page worth prefetching on intent. */
export function prefetchable(href, base) {
  let url;
  let here;
  try {
    url = new URL(href, base);
    here = new URL(base);
  } catch { return false; }
  if (url.origin !== here.origin || !/^https?:$/.test(url.protocol)) return false;
  if (url.pathname === here.pathname && url.search === here.search) return false;
  return /\/$|\.html$/.test(url.pathname);
}

function readName(slug) {
  try { return sessionStorage.getItem(NAME_KEY + slug) || ''; } catch { return ''; }
}

function rememberName(slug, name) {
  try { sessionStorage.setItem(NAME_KEY + slug, name); } catch { /* A blocked store keeps the slug. */ }
}

async function json(url, signal) {
  const response = await fetch(url, {credentials: 'omit', signal});
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

let snapshotRequest = null;
/** Institutions with a published filing snapshot page, read once from the public manifest. */
export function loadSnapshots() {
  snapshotRequest ||= json('/banking/institutions/manifest.json')
    .then(body => (Array.isArray(body?.records) ? body.records : [])
      .filter(row => typeof row?.slug === 'string' && SLUG.test(row.slug))
      .map(row => ({slug: row.slug, name: typeof row.name === 'string' ? row.name.slice(0, 160) : ''})))
    .catch(() => []);
  return snapshotRequest;
}

async function institutionName(slug, snapshots) {
  const cached = readName(slug);
  if (cached) return cached;
  const listed = snapshots.find(row => row.slug === slug)?.name;
  if (listed) { rememberName(slug, listed); return listed; }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const hit = await json(`${ONTOLOGY_API}/resolve?q=${encodeURIComponent(slug)}`, controller.signal);
    if (hit?.status !== 'resolved' || !/^[a-z]+:[a-z0-9-]+\/[a-z0-9-]+\/[a-z0-9-]+$/.test(hit.id || '')) return '';
    const object = await json(`${ONTOLOGY_API}/objects/${hit.id}`, controller.signal);
    const name = object?.status === 'found' && typeof object.label === 'string' ? object.label.slice(0, 160) : '';
    if (name) rememberName(slug, name);
    return name;
  } catch { return ''; } finally { clearTimeout(timer); }
}

function liquiLensBar(institution) {
  const bar = document.createElement('div');
  bar.className = 'research-context';
  bar.dataset.institutionContext = '';
  bar.setAttribute('role', 'region');
  bar.setAttribute('aria-label', 'Selected institution across tabs');
  const label = document.createElement('span');
  label.className = 'research-context__label';
  label.textContent = 'Selected institution';
  const name = document.createElement('strong');
  name.className = 'research-context__name';
  name.textContent = institution;
  const nav = document.createElement('nav');
  nav.className = 'research-context__links';
  nav.setAttribute('aria-label', 'Open this institution in');
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.textContent = 'Clear';
  clear.setAttribute('aria-label', 'Clear the selected institution');
  clear.addEventListener('click', () => {
    const url = new URL(location.href);
    url.searchParams.delete('institution');
    history.replaceState(null, '', url);
    syncInstitutionContext();
  });
  const render = snapshots => {
    const links = contextDestinations(institution, {snapshots: snapshots.map(row => row.slug)}).map(row => {
      const link = document.createElement('a');
      link.href = row.href;
      link.textContent = row.label;
      if (row.path === location.pathname) link.setAttribute('aria-current', 'page');
      return link;
    });
    nav.replaceChildren(...links);
  };
  render([]);
  bar.append(label, name, nav, clear);
  loadSnapshots().then(async snapshots => {
    if (!bar.isConnected) return;
    render(snapshots);
    const known = await institutionName(institution, snapshots);
    if (known && bar.isConnected) name.textContent = known;
  });
  return bar;
}

export function syncInstitutionContext() {
  if (typeof document === 'undefined') return;
  const institution = readInstitutionContext(location.href);
  const header = document.querySelector('.research-header');
  if (header) {
    header.querySelector('[data-institution-context]')?.remove();
    const product = header.querySelector('.research-network [aria-current="page"]')?.textContent.trim();
    const links = [...header.querySelectorAll('.research-network a, .research-tabs a, .research-identity')];
    for (const link of links) {
      const url = new URL(link.href, location.href);
      if (institution && !(product === 'Undertow' && url.pathname === '/app/')) url.searchParams.set('institution', institution);
      else url.searchParams.delete('institution');
      link.href = url.href;
    }
    if (institution && product === 'LiquiLens') {
      header.append(liquiLensBar(institution));
    } else if (institution) {
      const context = document.createElement('div');
      context.className = 'research-context';
      context.dataset.institutionContext = '';
      const label = document.createElement('span');
      label.textContent = 'Institution review';
      const back = document.createElement('a');
      back.href = 'https://liquilens.in/?institution=' + encodeURIComponent(institution);
      back.textContent = 'Return to institution evidence';
      const clear = document.createElement('button');
      clear.type = 'button'; clear.textContent = 'Clear';
      clear.setAttribute('aria-label', 'Clear institution review context');
      clear.addEventListener('click', () => {
        const url = new URL(location.href); url.searchParams.delete('institution');
        history.replaceState(null, '', url);
        syncInstitutionContext();
      });
      context.append(label, back, clear); header.append(context);
    }
  }
}

/* Prefetch a same-site page when a reader shows intent, so the next tab opens at once. */
function prefetchOnIntent() {
  const saving = navigator.connection?.saveData || /(^|-)2g$/.test(navigator.connection?.effectiveType || '');
  if (saving) return;
  const done = new Set();
  const hint = event => {
    const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
    if (!link || link.target === '_blank' || link.hasAttribute('download') || done.size >= 24) return;
    if (!prefetchable(link.href, location.href)) return;
    const url = new URL(link.href, location.href);
    url.hash = '';
    if (done.has(url.href)) return;
    done.add(url.href);
    const prefetch = document.createElement('link');
    prefetch.rel = 'prefetch';
    prefetch.href = url.href;
    document.head.append(prefetch);
  };
  document.addEventListener('pointerover', hint, {passive: true});
  document.addEventListener('focusin', hint);
  document.addEventListener('touchstart', hint, {passive: true});
}

/* On narrow screens the tab row scrolls; keep the current tab in view without moving the page. */
function revealCurrentTab() {
  const current = document.querySelector('.research-header .research-tabs [aria-current="page"]');
  const row = current?.parentElement;
  if (!row || row.scrollWidth <= row.clientWidth) return;
  row.scrollLeft = Math.max(0, current.offsetLeft - (row.clientWidth - current.offsetWidth) / 2);
}

if (typeof document !== 'undefined') {
  syncInstitutionContext();
  window.addEventListener('popstate', syncInstitutionContext);
  window.addEventListener('research:context-changed', syncInstitutionContext);
  prefetchOnIntent();
  revealCurrentTab();
}
