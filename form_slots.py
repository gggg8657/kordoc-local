"""Exact, position based HWPX filling. Only text nodes change; containers stay intact."""
import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

TAGS = re.compile(r'<(/?)([\w:.-]+)\b[^>]*>', re.S)
ATTR = re.compile(r'([\w:.-]+)="([^"]*)"')


def nodes(xml):
    ET.fromstring(xml)  # Reject malformed input before touching it.
    roots, stack = [], []
    for m in TAGS.finditer(xml):
        if m[1]:
            n = stack.pop()
            n['end'], n['close'] = m.end(), m.start()
        else:
            n = dict(tag=m[2].split(':')[-1], start=m.start(), open=m.end(),
                     end=m.end(), close=m.end(), attrs=dict(ATTR.findall(m[0])), children=[])
            (stack[-1]['children'] if stack else roots).append(n)
            if not m[0].endswith('/>'): stack.append(n)
    return roots


def walk(ns, tag=None):
    for n in ns:
        if tag is None or n['tag'] == tag: yield n
        yield from walk(n['children'], tag)


def texts(n):
    # Never consume a nested table, picture, or control's text as a cell value.
    for c in n['children']:
        if c['tag'] == 't': yield c
        elif c['tag'] in ('p', 'run', 'subList'): yield from texts(c)


def text(n, xml):
    return ''.join(html.unescape(re.sub('<[^>]+>', '', xml[t['open']:t['close']])) for t in texts(n)).strip()


def norm(s):
    return re.sub(r'[\s:：]', '', s)


def label_text(value):
    """Keep Korean meaning, omit bilingual translations and footnote prose."""
    return re.sub(r'\([A-Za-z][^)]*\)', '', value.split('*', 1)[0]).strip().rstrip(':：')


def placeholder(value):
    v = re.sub(r'\s+', '', value)
    if not v: return True
    if re.fullmatch(r'(?:\d*(?:\.\d+)?(?:건|회|개|%)|20[.~/~～—–-]*)', v): return True
    if re.fullmatch(r'(?:20[.]*)(?:[~～—–-]20[.]*)?', v): return True
    if re.fullmatch(r'[^:：]{1,20}[:：]\d*(?:건|회|개|%)?', v): return True
    return False


def table_slots(tbl, xml, section, known, named):
    rows = [r for r in tbl['children'] if r['tag'] == 'tr']
    cells = []
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(c for c in row['children'] if c['tag'] == 'tc'):
            addr = next((n['attrs'] for n in cell['children'] if n['tag'] == 'cellAddr'), {})
            span = next((n['attrs'] for n in cell['children'] if n['tag'] == 'cellSpan'), {})
            cells.append(dict(node=cell, r=int(addr.get('rowAddr',ri)), c=int(addr.get('colAddr',ci)),
                              rs=int(span.get('rowSpan',1)), cs=int(span.get('colSpan',1)), value=text(cell,xml)))
    out, used = [], set()
    width = int(tbl['attrs'].get('colCnt') or max((x['c']+x['cs'] for x in cells), default=0))
    def add(label, dest, prefix='', suffix=''):
        if norm(label) in named or list(walk(dest['node']['children'],'tbl')): return
        ts = list(texts(dest['node']))
        run = next(walk(dest['node']['children'],'run'),None)
        if not ts and not run: return
        out.append(dict(label=label, section=section, texts=ts, target=run, prefix=prefix,
                        suffix=suffix, hint=dest['value'], location=f"행 {dest['r']+1}, 열 {dest['c']+1}"))
        used.add(dest['node']['start'])
    def is_label(cell):
        v = cell['value']
        return bool(v) and not placeholder(v) and len(label_text(v)) <= 80 and not v.startswith(('□','■','※'))
    for cell in cells:
        v = cell['value']
        box = re.fullmatch(r'\(\s*([^()]{1,12})\s*\)',v)
        if box: add('('+box[1].strip()+')',cell,prefix=v+'\n'); continue
        if not is_label(cell): continue
        dest = next((x for x in cells if x['r']==cell['r'] and x['c']==cell['c']+cell['cs']),None)
        if not dest or dest['node']['start'] in used: continue
        # Existing prose is editable only with an explicit known label or bilingual field label.
        dv = dest['value']
        eligible = not dv or placeholder(dv) or norm(v) in known or bool(re.search(r'\([A-Za-z]',v))
        if not eligible or (dv and is_label(dest) and norm(dv) in known): continue
        if re.match(r'[^:：]+[:：]',dv): continue  # Has its own subfield label.
        if cell['cs'] > dest['cs'] and cell['rs'] > 1: continue  # Section label, not a value label.
        if dv and is_label(dest) and not re.search(r'\([A-Za-z]',v): continue
        add(label_text(v),dest)
    for cell in cells:
        if cell['node']['start'] in used or not placeholder(cell['value']): continue
        # A merged section label stays active down through its rowSpan.
        categories = [x for x in cells if is_label(x) and x['rs']>1 and x['r']<=cell['r']<x['r']+x['rs'] and x['c']+x['cs']<=cell['c'] and x['node']['start'] not in used]
        categories.sort(key=lambda x:x['c'])
        start = max((x['r'] for x in categories), default=max(0,cell['r']-3))
        headers = [x for x in cells if is_label(x) and start<=x['r']<cell['r'] and
                   x['c']<=cell['c'] and x['c']+x['cs']>=cell['c']+cell['cs'] and x['cs']<width and x['node']['start'] not in used]
        if not headers: continue
        # Read enclosing headers in row order, then the subfield in the value cell.
        headers.sort(key=lambda x:(x['r'],-x['cs']))
        parts = [label_text(x['value']) for x in categories+headers]
        inline = re.fullmatch(r'([^:：]{1,20}[:：]\s*)(.*)',cell['value'])
        prefix = inline[1] if inline else ''
        if inline: parts.append(label_text(inline[1]))
        parts = list(dict.fromkeys(parts))
        suffix_match = re.search(r'(건|회|개|%)\s*$',cell['value'])
        add(' / '.join(parts),cell,prefix=prefix,suffix=suffix_match[1] if suffix_match else '')
    grouped = defaultdict(list)
    for slot in out: grouped[norm(slot['label'])].append(slot)
    for group in grouped.values():
        # Repeated data rows beneath a single header have distinct, stable keys.
        # Same label in independent label/value rows remains ambiguous.
        if len(group)>1 and (' / ' in group[0]['label'] or all(s.get('suffix') for s in group)):
            for i,slot in enumerate(group,1): slot['label'] += f' [{i}]'
    return out


def scan(xml, section, known=()):
    tree = nodes(xml)
    slots = []
    known = {norm(k) for k in known}
    def add(label, ts, target=None, prefix=''):
        if label and (ts or target):
            slots.append(dict(label=label, section=section, texts=ts, target=target, prefix=prefix))
    allnodes = list(walk(tree))
    for fb in (n for n in allnodes if n['tag'] == 'fieldBegin' and n['attrs'].get('type') == 'CLICK_HERE'):
        name = html.unescape(fb['attrs'].get('name', ''))
        fe = next((n for n in allnodes if n['tag'] == 'fieldEnd' and n['start'] > fb['end'] and n['attrs'].get('beginIDRef') == fb['attrs'].get('id')), None)
        if not fe: continue
        ts = [n for n in allnodes if n['tag'] == 't' and fb['end'] <= n['start'] < fe['start']]
        run = next((n for n in allnodes if n['tag'] == 'run' and fb['end'] <= n['start'] < fe['start']), None)
        if not ts and not run:
            enclosing = next((n for n in allnodes if n['tag'] == 'run' and n['start'] < fb['start'] and fe['end'] < n['end']), None)
            if enclosing:
                pos = fe['start']
                if xml[:pos].endswith('<hp:ctrl>'): pos -= len('<hp:ctrl>')
                run = dict(start=pos, insert=pos)
        add(name, ts, run)
        if slots and slots[-1]['label'] == name: slots[-1]['field'] = fb
    named = {norm(s['label']) for s in slots}
    table_texts = set()
    for tbl in walk(tree, 'tbl'):
        slots += table_slots(tbl, xml, section, known, named)
        table_texts.update(n['start'] for n in walk(tbl['children'], 't'))
    # Same-cell label: value. Keep the original label and replace only the value.
    for p in walk(tree, 'p'):
        ts = list(texts(p))
        if len(ts) != 1 or ts[0]['start'] in table_texts: continue
        raw = text(p, xml)
        m = re.fullmatch(r'([^:：\n]{1,30}[:：]\s*)(.*)', raw)
        if m and norm(m[1]) not in named:
            add(m[1].rstrip().rstrip(':：').strip(), ts, prefix=m[1])
    # Remove duplicate discoveries of precisely the same target.
    unique = {}
    for s in slots:
        key = (norm(s['label']), tuple(t['start'] for t in s['texts']), s['target']['start'] if s['target'] else -1)
        unique[key] = s
    return list(unique.values())


def fill_files(files, values, known=()):
    sections = sorted(k for k in files if re.fullmatch(r'Contents/section\d+\.xml', k))
    xmls = {k: files[k].decode('utf-8') for k in sections}
    candidates = defaultdict(list)
    for k, xml in xmls.items():
        for s in scan(xml, k, known): candidates[norm(s['label'])].append(s)
    changes = defaultdict(list)
    filled, ambiguous, unmatched, empty = [], [], [], []
    supplied = {norm(k) for k in values}
    missing = [ss[0]['label'] for key, ss in candidates.items() if key not in supplied]
    occupied = set()
    for label, value in values.items():
        if value is None or value == '': empty.append(label); continue
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError(f'양식 값은 문자열 또는 숫자여야 합니다: {label}')
        slots = candidates.get(norm(label), [])
        if not slots: unmatched.append(label); continue
        if len(slots) != 1: ambiguous.append(label); continue
        s = slots[0]
        ts = s['texts']
        keys = {(s['section'], t['start']) for t in ts} or {(s['section'], s['target']['start'])}
        if occupied & keys: ambiguous.append(label); continue
        occupied.update(keys)
        value = str(value).strip()
        if s.get('suffix') and re.fullmatch(r'[+-]?[\d,]+(?:\.\d+)?', value): value += s['suffix']
        escaped = '<hp:lineBreak/>'.join(html.escape(line, quote=False) for line in (s['prefix'] + value).split('\n'))
        xml = xmls[s['section']]
        if ts:
            for i, t in enumerate(ts):
                original = xml[t['start']:t['end']]
                replacement = re.sub(r'/>$', '>', original) + (escaped if i == 0 else '') + '</hp:t>' if original.endswith('/>') else xml[t['start']:t['open']] + (escaped if i == 0 else '') + xml[t['close']:t['end']]
                changes[s['section']].append((t['start'], t['end'], replacement))
        else:
            run = s['target']
            if 'insert' in run:
                changes[s['section']].append((run['insert'], run['insert'], '<hp:t>' + escaped + '</hp:t>'))
            elif xml[run['start']:run['end']].endswith('/>'):
                changes[s['section']].append((run['start'], run['end'], xml[run['start']:run['end']][:-2] + '><hp:t>' + escaped + '</hp:t></hp:run>'))
            else: changes[s['section']].append((run['close'], run['close'], '<hp:t>' + escaped + '</hp:t>'))
        if s.get('field'):
            fb = s['field']
            tag = xml[fb['start']:fb['open']]
            if re.search(r'\bdirty="', tag): tag = re.sub(r'\bdirty="[^"]*"', 'dirty="1"', tag)
            else: tag = re.sub(r'(\s*/?>)$', r' dirty="1"\1', tag)
            changes[s['section']].append((fb['start'], fb['open'], tag))
        filled.append(label)
    for section, splices in changes.items():
        xml = xmls[section]
        ps = list(walk(nodes(xml), 'p'))
        caches = {}
        for start, _, _ in splices:
            containing = [p for p in ps if p['start'] <= start < p['end']]
            if containing:
                parent = min(containing, key=lambda p: p['end'] - p['start'])
                for cache in parent['children']:
                    if cache['tag'] == 'linesegarray': caches[cache['start']] = (cache['start'], cache['end'], '')
        for start, end, replacement in sorted(splices + list(caches.values()), reverse=True):
            xml = xml[:start] + replacement + xml[end:]
        ET.fromstring(xml)
        files[section] = xml.encode('utf-8')
    return dict(filled=filled, ambiguous=ambiguous, unmatched=unmatched, empty=empty, missing=missing)
