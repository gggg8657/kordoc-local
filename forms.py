"""연구원 양식(HWPX) 등록·분석·작성 — app.py 가 쓴다.

kind = "fill" (칸 채우기형): 출장 정산서·제출서류·이력서처럼 칸이 정해진 양식. kordoc fill 이 찾은 칸 이름(라벨)에
                              값을 넣고, "(사유)"처럼 칸 안에 제목만 있는 큰 칸은 제목 아래에 글을 붙인다.
kind = "body" (본문 작성형): 보고서 양식처럼 단계별 문단 모양(Ⅰ 장 제목 막대·□·○·-·※·표 제목·표)이 견본으로 들어 있는 양식.
                              견본 문단 XML 을 복제해 글만 바꿔 새 본문을 짠다. 그래서 글꼴·크기·여백·장 막대 모양이 양식 그대로다.
양식 앞부분(표지·제목·날짜 줄)은 그 자리의 안내 글(예: "OO본부", "2023. 00")을 LLM 이 실제 값으로 바꾼다.
HWP·DOC 는 받지 않는다 — kordoc 은 HWPX 일 때만 원본 서식을 지킨다(HWP 를 채우면 글꼴·칸 크기가 바뀐다)."""
import html as H
import json
import os
import re
import secrets
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(ROOT, "forms")
SEC = "Contents/section0.xml"
ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"
MARKS = [("l1", "□"), ("l1", "■"), ("l2", "○"), ("l2", "ㅇ"), ("l2", "◦"), ("l3", "-"), ("l3", "–"), ("l4", "·"), ("l4", "∙"), ("note", "※")]
BODY_ROLES = ("chapter", "l1", "l2", "l3", "l4", "note", "caption", "table", "refbox")
START_ROLES = ("chapter", "l1", "l2", "l3", "l4", "note")  # 본문 시작 — 표지·제목 상자(표)는 앞부분에 둔다
FALLBACK = {"l4": "l3", "l3": "l2", "l2": "l1", "note": "l3", "caption": "note", "chapter": "l1", "refbox": "chapter"}
HOWTO = "한글에서 [파일 → 다른 이름으로 저장 → 파일 형식: HWPX 문서(*.hwpx)]로 저장한 뒤 올려 주세요."


# ── HWPX 입출력 ───────────────────────────────────────────────────────────
def load(path):
    with zipfile.ZipFile(path) as z:
        return z.infolist(), {i.filename: z.read(i) for i in z.infolist()}


def save(path, infos, files):
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        for info in infos: z.writestr(info, files[info.filename])  # ZipInfo 그대로 → mimetype 무압축·순서 유지
    os.replace(tmp, path)


TOK = re.compile(r"<hp:p\b[^>]*?(/?)>|</hp:p>")


def blocks(sec):
    """섹션의 최상위 문단 [(시작, 끝)] — 표 안 문단은 바깥 문단에 포함"""
    out, depth, start = [], 0, 0
    for m in TOK.finditer(sec):
        s = m.group(0)
        if s.startswith("</"):
            depth -= 1
            if depth == 0: out.append((start, m.end()))
        elif m.group(1) == "/":
            if depth == 0: out.append((m.start(), m.end()))
        else:
            if depth == 0: start = m.start()
            depth += 1
    return out


TT = re.compile(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>|<hp:t\s*/>", re.S)


def text_of(xml):
    return "".join(H.unescape(re.sub(r"<[^>]+>", "", m.group(1) or "")) for m in TT.finditer(xml))


def first_text(xml):
    for m in TT.finditer(xml):
        t = H.unescape(re.sub(r"<[^>]+>", "", m.group(1) or "")).strip()
        if t: return t
    return ""


def classify(xml):
    if "<hp:tbl" in xml:
        t0 = first_text(xml)
        if t0[:1] in ROMAN: return "chapter"
        if t0 in ("참고", "참 고", "붙임", "별첨"): return "refbox"
        return "table"
    t = text_of(xml).strip()
    if t.startswith("<") and t.endswith(">"): return "caption"
    for role, mk in MARKS:
        if t.startswith(mk): return role
    return "plain"


def no_cache(xml):
    """줄 배치 캐시 제거 — 글이 바뀐 문단은 한글이 다시 조판한다"""
    return re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", xml, flags=re.S)


def set_text(xml, new, keep_marker=None):
    """문단(또는 셀) 글을 바꾼다. 첫 글자 run 의 글자모양을 쓰고 나머지 hp:t 는 비운다.
       keep_marker 가 있으면 그 기호(와 뒤따르는 공백·탭)까지는 원래 run 그대로 두고 그 뒤를 바꾼다."""
    ts = list(TT.finditer(xml))
    esc = H.escape(new, quote=False)
    if not ts:  # 빈 문단: 첫 run 에 글자 칸을 만든다
        if not new: return xml
        m = re.search(r"<hp:run\b([^>]*?)/>", xml)
        if m: return xml[:m.start()] + f"<hp:run{m.group(1)}><hp:t>{esc}</hp:t></hp:run>" + xml[m.end():]
        i = xml.find("</hp:run>")
        return xml[:i] + f"<hp:t>{esc}</hp:t>" + xml[i:] if i >= 0 else xml
    target, prefix = 0, ""
    if keep_marker:
        for k, m in enumerate(ts):
            raw = m.group(1) or ""
            i = H.unescape(raw).find(keep_marker)
            if i >= 0:
                # raw 안에서 기호 위치 찾기(엔티티 없는 기호라 그대로 찾힌다)
                j = raw.find(keep_marker) + len(keep_marker)
                tail = re.match(r"(?:\s|&#160;|&nbsp;|<hp:tab\b[^>]*/>|<hp:fwSpace\s*/>)*", raw[j:])
                prefix, target = raw[:j + tail.end()], k
                if not tail.group(0): prefix += " "
                break
    out, last = [], 0
    for k, m in enumerate(ts):
        out.append(xml[last:m.start()])
        if k < target: out.append(m.group(0))
        else: out.append("<hp:t>" + (prefix + esc if k == target else "") + "</hp:t>")
        last = m.end()
    out.append(xml[last:])
    return "".join(out)


# ── 분석·등록 ─────────────────────────────────────────────────────────────
def analyze(path, kordoc):
    """양식 종류·견본·앞부분·칸 목록. kordoc: app.kordoc(*args) → (code, out)"""
    _, files = load(path)
    sec = files[SEC].decode("utf-8")
    bl = blocks(sec)
    roles = [classify(sec[s:e]) for s, e in bl]
    counts = {r: roles.count(r) for r in BODY_ROLES if r in roles}
    first = next((i for i, r in enumerate(roles) if r in START_ROLES), len(bl))
    front = [{"i": i, "text": text_of(sec[s:e]).strip()} for i, (s, e) in enumerate(bl[:first])]
    front = [f for f in front if f["text"]]
    code, out = kordoc("fill", path, "--dry-run", "--silent")
    try:
        dry = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        dry = {}
    # 누름틀(이름 붙은 입력 칸)을 먼저, 그다음 표의 라벨 칸 — 이름이 겹치면 누름틀 쪽만
    fields = [{"label": c["name"], "value": c.get("placeholder") or c.get("value") or ""} for c in dry.get("clickHereFields") or []]
    seen = {x["label"] for x in fields}
    fields += [x for x in dry.get("fields") or [] if x["label"] not in seen]
    marked = sum(v for k, v in counts.items() if k in ("chapter", "l1", "l2", "l3", "l4"))
    boxes = [t for t in big_boxes(sec)]
    kind = "body" if marked >= 3 else "fill"
    return {"kind": kind, "roles": counts, "front": front, "fields": [{"label": f["label"], "hint": f.get("value", "")} for f in fields],
            "boxes": boxes, "blocks": len(bl)}


BOX = re.compile(r"^\(\s*([^()]{1,12})\s*\)")


def big_boxes(sec):
    """'(사유)'처럼 칸 안에 제목만 쓰인 큰 칸 — 제목 아래에 글을 붙일 자리"""
    out = []
    for m in re.finditer(r"<hp:tc\b.*?</hp:tc>", sec, re.S):
        t = first_text(m.group(0))
        b = BOX.match(t)
        if b and t == b.group(0): out.append(b.group(1).strip())
    return list(dict.fromkeys(out))


def register(name, data, kordoc):
    os.makedirs(DIR, exist_ok=True)
    fid = secrets.token_hex(4)
    path = os.path.join(DIR, fid + ".hwpx")
    with open(path, "wb") as f: f.write(data)
    try:
        load(path)[1][SEC]
    except Exception:
        os.remove(path); raise ValueError("HWPX 문서가 아닙니다. " + HOWTO)
    meta = {"id": fid, "name": name, **analyze(path, kordoc)}
    with open(os.path.join(DIR, fid + ".json"), "w", encoding="utf-8") as f: json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


def listing():
    if not os.path.isdir(DIR): return []
    out = []
    for n in sorted(os.listdir(DIR)):
        if n.endswith(".json"):
            with open(os.path.join(DIR, n), encoding="utf-8") as f: out.append(json.load(f))
    return out


def get(fid):
    if not re.fullmatch(r"[0-9a-f]{8}", fid or ""): raise ValueError("잘못된 양식 ID")
    with open(os.path.join(DIR, fid + ".json"), encoding="utf-8") as f: meta = json.load(f)
    return meta, os.path.join(DIR, fid + ".hwpx")


def remove(fid):
    meta, path = get(fid)
    for p in (path, path[:-5] + ".json"):
        if os.path.exists(p): os.remove(p)
    return {"ok": True}


def set_kind(fid, kind):
    if kind not in ("fill", "body"): raise ValueError("kind")
    meta, _ = get(fid); meta["kind"] = kind
    with open(os.path.join(DIR, fid + ".json"), "w", encoding="utf-8") as f: json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


# ── LLM 프롬프트 재료 ─────────────────────────────────────────────────────
def prompt_spec(meta):
    """양식 설명 — LLM 사용자 메시지에 붙인다"""
    if meta["kind"] == "fill":
        lines = [f'- "{f["label"]}"' + (f' (지금 적힌 안내: {f["hint"]})' if f["hint"] else "") for f in meta["fields"]]
        lines += [f'- "({b})" — 큰 칸, 여러 줄 글' for b in meta["boxes"]]
        return "[양식 칸]\n" + "\n".join(lines)
    front = "\n".join(f'- {f["i"]}: {f["text"]}' for f in meta["front"])
    have = [r for r in ("chapter", "l1", "l2", "l3", "l4", "note", "caption", "table", "refbox") if r in meta["roles"]]
    sym = {"chapter": "Ⅰ. 장 제목", "l1": "□ 1단계", "l2": "○ 2단계", "l3": "- 3단계", "l4": "· 4단계", "note": "※ 참고·주석",
           "caption": "<표 제목>", "table": "| 표 | (GFM 파이프 표)", "refbox": "[참고] 참고 상자 제목"}
    return f"[양식 앞부분 — 번호: 지금 적힌 안내 글]\n{front}\n\n[본문에 쓸 수 있는 줄 모양]\n" + "\n".join("- " + sym[r] for r in have)


def parse_json(answer):
    m = re.search(r"\{.*\}", answer, re.S)
    if not m: raise ValueError("LLM 응답에 JSON 이 없습니다")
    return json.loads(m.group(0))


# ── 칸 채우기형 ───────────────────────────────────────────────────────────
def fill(meta, path, values, out, kordoc, workdir):
    vals = {k: str(v).strip() for k, v in values.items() if str(v or "").strip()}
    boxes = {b: vals.pop(f"({b})", vals.pop(b, None)) for b in meta.get("boxes", [])}
    vj = os.path.join(workdir, "02_fields.json")
    with open(vj, "w", encoding="utf-8") as f: json.dump(vals, f, ensure_ascii=False, indent=1)
    code, msg = kordoc("fill", path, "-j", vj, "-o", out) if vals else (0, "채울 칸 없음")
    if not vals: import shutil; shutil.copy(path, out)
    if code != 0: raise RuntimeError(f"kordoc fill 실패: {msg[-300:]}")
    if any(boxes.values()):
        infos, files = load(out); sec = files[SEC].decode("utf-8")
        for b, txt in boxes.items():
            if txt: sec = box_append(sec, b, txt)
        files[SEC] = sec.encode("utf-8"); save(out, infos, files)
    return {"filled": len(vals) + sum(1 for v in boxes.values() if v), "log": msg}


def box_append(sec, title, txt):
    """'(사유)' 칸: 제목 문단 바로 뒤에 글 문단들을 넣는다(제목 문단 모양 복제)"""
    for m in re.finditer(r"<hp:tc\b.*?</hp:tc>", sec, re.S):
        tc = m.group(0)
        if not BOX.match(first_text(tc)) or BOX.match(first_text(tc)).group(1).strip() != title: continue
        pm = re.search(r"<hp:p\b.*?</hp:p>", tc, re.S)
        if not pm: continue
        p = no_cache(pm.group(0))
        new = "".join(set_text(p, ln) for ln in txt.split("\n") if ln.strip())
        tc2 = tc[:pm.end()] + new + tc[pm.end():]
        return sec[:m.start()] + tc2 + sec[m.end():]
    return sec


# ── 본문 작성형 ───────────────────────────────────────────────────────────
def examples(sec):
    """본문 견본 — 본문 시작(첫 장 막대·기호 줄) 이후에서만 고른다(앞부분 표지·제목 상자는 견본이 아니다)"""
    bl = blocks(sec)
    first = next((i for i, (s, e) in enumerate(bl) if classify(sec[s:e]) in START_ROLES), len(bl))
    ex = {}
    for s, e in bl[first:]:
        r = classify(sec[s:e])
        if r != "plain" and r not in ex: ex[r] = no_cache(sec[s:e])
    plain = next((no_cache(sec[s:e]) for s, e in bl[first:] if classify(sec[s:e]) == "plain" and text_of(sec[s:e]).strip()), None)
    if plain: ex["plain"] = plain
    return ex, bl, first


def pick(ex, role):
    while role not in ex and role in FALLBACK: role = FALLBACK[role]
    return ex.get(role) or ex.get("l1") or next(iter(ex.values()))


def chapter(ex, num, title):
    xml = ex["chapter"] if "chapter" in ex else None
    if not xml: return set_text(pick(ex, "l1"), f"{num}. {title}")
    ts = [m for m in TT.finditer(xml) if H.unescape(re.sub(r"<[^>]+>", "", m.group(1) or "")).strip()]
    out, last = [], 0
    done_num = done_title = False
    for m in TT.finditer(xml):
        t = H.unescape(re.sub(r"<[^>]+>", "", m.group(1) or "")).strip()
        out.append(xml[last:m.start()])
        if t and t[:1] in ROMAN and not done_num: out.append(f"<hp:t>{num}</hp:t>"); done_num = True
        elif t and not done_title: out.append(f"<hp:t>{H.escape(title, quote=False)}</hp:t>"); done_title = True
        elif t: out.append("<hp:t></hp:t>")
        else: out.append(m.group(0))
        last = m.end()
    out.append(xml[last:])
    return new_ids("".join(out))


def new_ids(xml):
    return re.sub(r'(<hp:(?:tbl|pic|rect|container)\b[^>]*?\bid=")\d+(")', lambda m: m.group(1) + str(secrets.randbelow(2**31 - 1) + 1) + m.group(2), xml)


class Header:
    """header.xml 편집 — 표 칸 테두리(borderFill) 추가용"""
    def __init__(self, xml): self.xml, self.cache = xml, {}

    def border(self, base_id, sides):
        """base_id 테두리를 복제하고 left/right/top/bottom 선을 sides[...](다른 borderFill 의 선 태그)로 바꾼 새 id"""
        key = (base_id, tuple(sorted(sides.items())))
        if key in self.cache: return self.cache[key]
        b = re.search(rf'<hh:borderFill id="{base_id}".*?</hh:borderFill>', self.xml, re.S).group(0)
        for side, tag in sides.items():
            b = re.sub(rf"<hh:{side}Border\b[^>]*/>", tag.replace(re.match(r"<hh:(\w+)Border", tag).group(1) + "Border", side + "Border", 1), b, 1)
        nid = max(int(x) for x in re.findall(r'<hh:borderFill id="(\d+)"', self.xml)) + 1
        b = re.sub(r'<hh:borderFill id="\d+"', f'<hh:borderFill id="{nid}"', b, 1)
        self.xml = self.xml.replace("</hh:borderFills>", b + "</hh:borderFills>", 1)
        self.xml = re.sub(r'<hh:borderFills itemCnt="(\d+)"', lambda m: f'<hh:borderFills itemCnt="{int(m.group(1)) + 1}"', self.xml, 1)
        self.cache[key] = nid
        return nid

    def side(self, bid, side):
        b = re.search(rf'<hh:borderFill id="{bid}".*?</hh:borderFill>', self.xml, re.S).group(0)
        return re.search(rf"<hh:{side}Border\b[^>]*/>", b).group(0)


def _cell_p(tc):
    """칸 글자 모양 견본 — 글이 있는 첫 문단(빈 문단은 글꼴이 다를 수 있다)"""
    ps = re.findall(r"<hp:p\b.*?</hp:p>", tc, re.S)
    return no_cache(next((p for p in ps if text_of(p).strip()), ps[0]))


def table(ex, rows, hdr):
    """견본 표의 위치별 모양으로 R×C 표를 새로 짠다.
       테두리: 바깥 위·아래·왼쪽·오른쪽 선과 안쪽 가로·세로 선을 견본 모서리 칸에서 읽어 칸마다 조합.
       머리줄(첫 행)·첫 열은 견본 첫 열 칸 모양(바탕색·글꼴), 나머지는 견본 내용 칸 모양."""
    if "table" not in ex:
        return "".join(set_text(pick(ex, "l2"), " / ".join(r)) for r in rows)
    xml = ex["table"]
    tm = re.search(r"<hp:tbl\b.*</hp:tbl>", xml, re.S)
    tbl = tm.group(0)
    grid = [g for g in (re.findall(r"<hp:tc\b.*?</hp:tc>", tr, re.S) for tr in re.findall(r"<hp:tr>.*?</hp:tr>", tbl, re.S)) if g]
    bf = lambda tc: int(re.search(r'borderFillIDRef="(\d+)"', tc).group(1))
    lab, con = grid[0][0], grid[0][min(1, len(grid[0]) - 1)]
    tl, tr_, bl_ = bf(grid[0][0]), bf(grid[0][-1]), bf(grid[-1][0])
    edge = {"L": hdr.side(tl, "left"), "R": hdr.side(tr_, "right"), "T": hdr.side(tl, "top"), "B": hdr.side(bl_, "bottom"),
            "H": hdr.side(tl, "bottom"), "V": hdr.side(tl, "right") if len(grid[0]) > 1 else hdr.side(tl, "left")}
    R, C = len(rows), max(len(r) for r in rows)
    w0 = [int(re.search(r'<hp:cellSz width="(\d+)"', tc).group(1)) for tc in grid[0]]
    total = sum(w0)
    if len(w0) == C: widths = w0
    elif C > 1 and w0[0] < total / 2: widths = [w0[0]] + [(total - w0[0]) // (C - 1)] * (C - 1)
    else: widths = [total // C] * C
    rh = int(re.search(r'<hp:cellSz width="\d+" height="(\d+)"', con).group(1))
    lp, cp = _cell_p(lab), _cell_p(con)
    out_rows = []
    for r, row in enumerate(rows):
        cells = []
        for c in range(C):
            head = r == 0 or c == 0
            base = lab if head else con
            sides = {"left": edge["L"] if c == 0 else edge["V"], "right": edge["R"] if c == C - 1 else edge["V"],
                     "top": edge["T"] if r == 0 else edge["H"], "bottom": edge["B"] if r == R - 1 else edge["H"]}
            nid = hdr.border(bf(base), sides)
            sub = re.search(r"(<hp:subList\b[^>]*>)(.*?)(</hp:subList>)", base, re.S)
            p = set_text(lp if head else cp, row[c] if c < len(row) else "")
            tc = base[:sub.start()] + sub.group(1) + p + sub.group(3) + base[sub.end():]
            tc = re.sub(r'borderFillIDRef="\d+"', f'borderFillIDRef="{nid}"', tc, 1)
            tc = re.sub(r'<hp:cellAddr colAddr="\d+" rowAddr="\d+"\s*/>', f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/>', tc)
            tc = re.sub(r'<hp:cellSpan colSpan="\d+" rowSpan="\d+"\s*/>', '<hp:cellSpan colSpan="1" rowSpan="1"/>', tc)
            tc = re.sub(r'<hp:cellSz width="\d+" height="\d+"', f'<hp:cellSz width="{widths[c]}" height="{rh}"', tc)
            cells.append(tc)
        out_rows.append("<hp:tr>" + "".join(cells) + "</hp:tr>")
    head = tbl[:tbl.find("<hp:tr>")]
    head = re.sub(r'rowCnt="\d+"', f'rowCnt="{R}"', head, 1)
    head = re.sub(r'colCnt="\d+"', f'colCnt="{C}"', head, 1)
    head = re.sub(r'(<hp:sz width=")\d+(" [^>]*?height=")\d+', lambda m: f"{m.group(1)}{sum(widths)}{m.group(2)}{rh * R}", head, 1)
    tail = tbl[tbl.rfind("</hp:tr>") + len("</hp:tr>"):]
    return new_ids(xml[:tm.start()] + head + "".join(out_rows) + tail + xml[tm.end():])


CH = re.compile(rf"^([{ROMAN}]|[IVX]{{1,4}})\s*[.．]?\s*(.+)$")


def render(ex, lines, hdr):
    out, rows = [], []

    def flush():
        if rows: out.append(table(ex, rows, hdr)); rows.clear()

    for raw in lines:
        ln = str(raw).strip()
        if not ln: continue
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c): rows.append(cells)
            continue
        flush()
        m = CH.match(ln)
        if m and (m.group(1)[0] in ROMAN or ln[:4].rstrip(". ") in ("I", "II", "III", "IV", "V", "VI")):
            num = m.group(1) if m.group(1)[0] in ROMAN else ROMAN["I II III IV V VI VII VIII IX X".split().index(m.group(1))]
            out.append(chapter(ex, num, m.group(2).strip())); continue
        if ln.startswith("[참고]") and "refbox" in ex:
            out.append(_refbox(ex["refbox"], ln[4:].strip())); continue
        if ln.startswith("<") and ln.endswith(">"):
            out.append(set_text(pick(ex, "caption"), ln)); continue
        for role, mk in MARKS:
            if ln.startswith(mk):
                base, body = pick(ex, role), ln[len(mk):].strip()
                if classify(base) == role:  # 같은 단계 견본: 견본의 기호(○·ㅇ 등)와 그 run 을 그대로 쓴다
                    bm = next(m2 for r2, m2 in MARKS if r2 == role and text_of(base).strip().startswith(m2))
                    out.append(set_text(base, body, keep_marker=bm))
                else:  # 견본이 없어 다른 단계 모양을 빌림: 기호까지 새로 쓴다
                    out.append(set_text(base, f"{mk} {body}"))
                break
        else:
            out.append(set_text(ex.get("plain") or pick(ex, "l2"), ln))
    flush()
    return "".join(out)


def _refbox(xml, title):
    """참고 상자: '참고' 칸은 두고 옆 칸(제목 안내)만 바꾼다"""
    out, last, seen = [], 0, 0
    for m in TT.finditer(xml):
        t = H.unescape(re.sub(r"<[^>]+>", "", m.group(1) or "")).strip()
        out.append(xml[last:m.start()])
        if t and seen == 1: out.append(f"<hp:t>{H.escape(title, quote=False)}</hp:t>")
        elif t and seen > 1: out.append("<hp:t></hp:t>")
        else: out.append(m.group(0))
        if t: seen += 1
        last = m.end()
    out.append(xml[last:])
    return new_ids("".join(out))


def tidy(s):
    """비운 소속 때문에 남는 쉼표·괄호·공백 정리: '(2026.10.02.,  )' → '(2026.10.02.)'"""
    s = re.sub(r"[,，]\s*(?=[)\]])", "", s)
    s = re.sub(r"\(\s*\)", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def front_text(xml, new):
    """앞부분 칸 글 바꾸기. 상자(표) 안 안내가 여러 문단이면('표 지' / '(HY헤드라인M, 30pt)') 첫 문단에 새 글을 넣고
       글이 있던 나머지 문단은 지운다 — 빈 줄이 남아 표지가 다음 쪽으로 밀리지 않게."""
    sub = re.search(r"(<hp:subList\b[^>]*>)(.*?)(</hp:subList>)", xml, re.S)
    if "<hp:tbl" not in xml or not sub: return set_text(xml, new)
    ps = list(re.finditer(r"<hp:p\b.*?</hp:p>", sub.group(2), re.S))
    texted = [m for m in ps if text_of(m.group(0)).strip()]
    if len(texted) < 2: return set_text(xml, new)
    inner = sub.group(2)
    for m in reversed(texted[1:]): inner = inner[:m.start()] + inner[m.end():]
    first = texted[0]
    inner = inner[:first.start()] + set_text(first.group(0), new) + inner[first.end():]
    return xml[:sub.start(2)] + inner + xml[sub.end(2):]


def build_body(path, out, front_map, lines):
    """양식 앞부분(표지·제목·날짜)은 front_map 으로 글만 바꿔 두고, 본문 자리는 견본 복제로 새로 짠다. 양식의 원래 본문·안내 문단은 버린다."""
    infos, files = load(path)
    sec = files[SEC].decode("utf-8")
    ex, bl, first = examples(sec)
    if not ex: raise RuntimeError("양식에서 본문 견본(□ ○ - 같은 줄)을 찾지 못했습니다")
    parts = []
    for i, (s, e) in enumerate(bl[:first]):
        xml = sec[s:e]
        k = str(i)
        if k in front_map:
            xml = front_text(no_cache(xml), tidy(str(front_map[k] or "")))
        parts.append(xml)
    hdr = Header(files["Contents/header.xml"].decode("utf-8"))
    body = render(ex, lines, hdr)
    start, end = bl[0][0], bl[-1][1]
    files[SEC] = (sec[:start] + "".join(parts) + body + sec[end:]).encode("utf-8")
    files["Contents/header.xml"] = hdr.xml.encode("utf-8")
    save(out, infos, files)
    return {"front": len(front_map), "lines": len([l for l in lines if str(l).strip()])}
