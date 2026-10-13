# GUI_V1/export/struct_postpass.py
"""
Final structure-tree rules applied to the finished tagged PDF, the same for
Digital, Hybrid and Scanned documents (they work on the PDF itself: marked
content, structure elements, annotations).

1. Links
   * one link annotation = exactly one OBJR (duplicates removed, the Link with
     the link text keeps it); a Link with no content and no OBJR is removed
   * [Scanned] a /Link annotation that is in no Link yet joins the text under
     its rectangle (that text becomes a Link > [text, OBJR]); links lying on
     artifacts (running headers/footers) are removed from the page
   * every link annotation gets its alternate description (/Contents) = the
     link's visible text without leader dots ("Introduction 1"); never empty
   * [Scanned] an internal link on a footnote/endnote number is wrapped in
     Reference (Reference > Link), as in the digital flow

2. List labels
   An Lbl whose content is not readable text - a drawn shape (path / image),
   or only symbols such as "." "*" "," "•" - gets its content wrapped in
   Lbl > Span with /ActualText in words: "dot", "star", "comma", "bullet" ...
   A number or letter (also when drawn as a shape, e.g. a numbered circle)
   gets that number / letter: "2", "a", "A". Readable labels ("1.", "a)")
   are left unchanged.
"""
import re

import pikepdf
from pikepdf import Array, Dictionary, Name

# ---------------------------------------------------------------- vocabulary
SYMBOL_WORDS = {
    ".": "dot", ",": "comma", "*": "star", "∗": "star", "✱": "star", "★": "star", "☆": "star",
    "•": "bullet", "●": "bullet", "◦": "bullet", "○": "bullet", "·": "bullet",
    "∙": "bullet", "‧": "bullet", "◉": "bullet", "⦁": "bullet", "o": "bullet",
    "■": "square", "□": "square", "▪": "square", "▫": "square", "◾": "square", "◽": "square",
    "◆": "diamond", "◇": "diamond", "❖": "diamond", "♦": "diamond",
    "-": "dash", "–": "dash", "—": "dash", "⁃": "dash", "‒": "dash",
    "✓": "check mark", "✔": "check mark", "☑": "check mark", "✅": "check mark",
    "►": "arrow", "▸": "arrow", "▶": "arrow", "➢": "arrow", "➤": "arrow",
    "→": "arrow", "‣": "arrow", ">": "arrow", "›": "arrow", "»": "arrow",
    "+": "plus", "#": "number sign", ":": "colon", ";": "semicolon", ")": "closing parenthesis",
    "(": "opening parenthesis", "†": "dagger", "‡": "double dagger", "§": "section sign",
    "¶": "pilcrow",
}


def label_words(text):
    """'•' -> 'bullet', '.' -> 'dot', '*' -> 'star', '2' -> '2', '' -> 'bullet'."""
    t = (text or "").strip()
    if not t:
        return "bullet"
    if any(c.isalnum() for c in t) and not (len(t) == 1 and t in SYMBOL_WORDS and not t.isalnum()):
        # a number / letter label keeps its own text ("2", "a", "A", "iv")
        alnum = "".join(c for c in t if c.isalnum())
        if alnum and alnum.lower() != "o":
            return alnum
    words = []
    for c in t:
        if c.isspace():
            continue
        w = SYMBOL_WORDS.get(c)
        if w is None:
            w = "bullet"
        if not words or words[-1] != w:
            words.append(w)
    return " ".join(words) or "bullet"


# ---------------------------------------------------------------- helpers
def _kids(e):
    k = e.get("/K")
    if k is None:
        return []
    if isinstance(k, Array):
        return list(k)
    return [k]


def _is_elem(x):
    return isinstance(x, Dictionary) and "/S" in x and str(x.get("/Type", "/StructElem")) not in ("/OBJR", "/MCR")


def _is_objr(x):
    return isinstance(x, Dictionary) and str(x.get("/Type", "")) == "/OBJR"


def _is_mcid(x):
    if isinstance(x, int):
        return True
    try:
        return not isinstance(x, (Dictionary, Array)) and isinstance(int(x), int)
    except Exception:
        return False


def _key(o):
    try:
        return o.objgen if o.is_indirect else ("d", id(o))
    except Exception:
        return ("d", id(o))


def _walk(root):
    """[(elem, parent)] for every StructElem (pre-order)."""
    out = []
    stack = [(k, root) for k in reversed(_kids(root))]
    seen = set()
    while stack:
        e, p = stack.pop()
        if not _is_elem(e):
            continue
        key = _key(e)
        if key in seen:
            continue
        seen.add(key)
        out.append((e, p))
        for k in reversed(_kids(e)):
            stack.append((k, e))
    return out


class _ParentTree:
    """Flat access to the ParentTree number tree."""

    def __init__(self, pdf, root):
        self.root = root
        self.leaves = []
        pt = root.get("/ParentTree")
        self._collect(pt, 0)
        self.max_key = -1
        for nums, vi in self.leaves:
            try:
                self.max_key = max(self.max_key, int(nums[vi - 1]))
            except Exception:
                pass
        try:
            self.max_key = max(self.max_key, int(root.get("/ParentTreeNextKey", 0)) - 1)
        except Exception:
            pass

    def _collect(self, node, depth):
        if node is None or depth > 32:
            return
        nums = node.get("/Nums")
        if isinstance(nums, Array):
            for i in range(0, len(nums) - 1, 2):
                self.leaves.append((nums, i + 1))
        for kid in node.get("/Kids") or []:
            self._collect(kid, depth + 1)

    def get(self, key):
        for nums, vi in self.leaves:
            try:
                if int(nums[vi - 1]) == int(key):
                    return nums[vi]
            except Exception:
                pass
        return None

    def set_annot(self, key, elem):
        for nums, vi in self.leaves:
            try:
                if int(nums[vi - 1]) == int(key):
                    nums[vi] = elem
                    return
            except Exception:
                pass

    def new_annot_key(self, elem):
        pt = self.root.get("/ParentTree")
        self.max_key += 1
        key = self.max_key
        if pt is not None and isinstance(pt.get("/Nums"), Array):
            nums = pt.Nums
            nums.append(key)
            nums.append(elem)
            self.leaves.append((nums, len(nums) - 1))
        self.root.ParentTreeNextKey = key + 1
        return key

    def set_mcid(self, page, mcid, elem):
        sp = page.get("/StructParents")
        if sp is None:
            return
        arr = self.get(int(sp))
        if isinstance(arr, Array):
            while len(arr) <= int(mcid):
                arr.append(None)
            arr[int(mcid)] = elem


def _page_mcid_content(pdf):
    """{page_index: {mcid: {"text": str, "glyphs": [Glyph], "graphic": bool, "bbox": (...)}}}"""
    from plugins.digital.content_geometry import ContentInterpreter, flatten_glyphs
    result = {}
    for pi, pg in enumerate(pdf.pages):
        info = {}
        try:
            instrs = list(pikepdf.parse_content_stream(pg))
        except Exception:
            result[pi] = info
            continue
        at, stack = {}, []
        for i, ins in enumerate(instrs):
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                at[i] = stack[-1] if stack else None
                continue
            op = str(ins.operator)
            if op == "BDC":
                props = ins.operands[1] if len(ins.operands) > 1 else None
                m = None
                if isinstance(props, Dictionary) and "/MCID" in props:
                    try:
                        m = int(props.MCID)
                    except Exception:
                        m = None
                stack.append(m if m is not None else (stack[-1] if stack else None))
            elif op == "BMC":
                stack.append(stack[-1] if stack else None)
            elif op == "EMC":
                if stack:
                    stack.pop()
            at[i] = stack[-1] if stack else None
        try:
            mb = [float(v) for v in pg.obj.get("/MediaBox", [0, 0, 612, 792])]
            units = ContentInterpreter(pg.obj.get("/Resources"), page_box=tuple(mb)).run(instrs)
        except Exception:
            units = []
        for u in units:
            m = at.get(u.start)
            if m is None:
                continue
            d = info.setdefault(m, {"text": "", "glyphs": [], "graphic": False, "bbox": None})
            if u.kind in ("text", "form"):
                gl = [g for g, _ in flatten_glyphs(u)]
                d["glyphs"].extend(gl)
                d["text"] += "".join(g.uni or "" for g in gl)
                if u.kind == "form" and any(x.kind in ("path", "image", "shade") for x in (u.inner or [])):
                    d["graphic"] = True
            elif u.kind in ("path", "image", "shade"):
                d["graphic"] = True
            if u.bbox:
                b = d["bbox"]
                d["bbox"] = tuple(u.bbox) if b is None else (min(b[0], u.bbox[0]), min(b[1], u.bbox[1]),
                                                              max(b[2], u.bbox[2]), max(b[3], u.bbox[3]))
        result[pi] = info
    return result


def _elem_page(e, pages_by_key):
    cur, hops = e, 0
    while cur is not None and hops < 64:
        pg = cur.get("/Pg")
        if pg is not None:
            return pages_by_key.get(pg.objgen)
        cur = cur.get("/P")
        hops += 1
    return None


def _link_alt(text, annot):
    from plugins.digital.digital_stream_tagger import DigitalStreamTagger as T
    kind = "external"
    try:
        a = annot.get("/A")
        if annot.get("/Dest") is not None or (a is not None and str(a.get("/S")) in ("/GoTo", "/GoToR")):
            kind = "internal"
        tgt = str(a.get("/URI")) if a is not None and "/URI" in a else ""
    except Exception:
        tgt = ""
    return T.link_alt(text, {"kind": kind, "target": tgt})


# ---------------------------------------------------------------- 1. links
def fix_links(pdf, scanned=False, artifact_boxes=None):
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return {}
    stats = {"duplicate_objr_removed": 0, "empty_links_removed": 0, "annots_attached": 0,
             "artifact_links_removed": 0, "alt_set": 0, "note_refs_wrapped": 0}
    ptree = _ParentTree(pdf, root)
    pages_by_key = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
    content = _page_mcid_content(pdf)

    def elems():
        return _walk(root)

    # ---- a. one annotation = one OBJR
    refs = {}
    for e, p in elems():
        for x in _kids(e):
            if _is_objr(x) and x.get("/Obj") is not None:
                refs.setdefault(_key(x.Obj), []).append((e, x))

    def n_mcids(e):
        return sum(1 for x in _kids(e) if _is_mcid(x))

    for ak, lst in refs.items():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda t: (str(t[0].get("/S")) == "/Link", n_mcids(t[0])), reverse=True)
        keep_e, keep_x = lst[0]
        for e, x in lst[1:]:
            e.K = Array([k for k in _kids(e) if not (_is_objr(k) and k.get("/Obj") is not None and _key(k.Obj) == ak)])
            stats["duplicate_objr_removed"] += 1
        try:
            annot = keep_x.Obj
            if "/StructParent" in annot:
                ptree.set_annot(int(annot.StructParent), keep_e)
        except Exception:
            pass

    # ---- b. remove empty Links (no content, no OBJR, no kids)
    changed = True
    while changed:
        changed = False
        for e, p in elems():
            if str(e.get("/S")) == "/Link" and not _kids(e):
                p.K = Array([k for k in _kids(p) if not (_is_elem(k) and _key(k) == _key(e))])
                stats["empty_links_removed"] += 1
                changed = True
                break

    # mcid -> owner element per page
    owner = {}
    for e, p in elems():
        pi = _elem_page(e, pages_by_key)
        for x in _kids(e):
            if _is_mcid(x) and pi is not None:
                owner[(pi, int(x))] = (e, p)

    claimed = set()
    for e, p in elems():
        for x in _kids(e):
            if _is_objr(x) and x.get("/Obj") is not None:
                claimed.add(_key(x.Obj))

    # ---- c. [scanned] unclaimed link annotations join the text under them
    if scanned:
        for pi, pg in enumerate(pdf.pages):
            annots = pg.obj.get("/Annots")
            if not isinstance(annots, Array):
                continue
            remove = []
            for a in list(annots):
                try:
                    if str(a.get("/Subtype")) != "/Link" or _key(a) in claimed:
                        continue
                    r = [float(v) for v in a.Rect]
                    r = [min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])]
                except Exception:
                    continue
                under = {}
                for m, d in content.get(pi, {}).items():
                    gl = [g for g in d["glyphs"] if (g.uni or "").strip()]
                    if not gl:
                        continue
                    inside = sum(1 for g in gl if r[0] - 1 <= g.cx <= r[2] + 1 and r[1] - 1 <= g.cy <= r[3] + 1)
                    if inside:
                        under[m] = (inside, len(gl))
                if not under:
                    if any(not (r[2] < b[0] or r[0] > b[2] or r[3] < b[1] or r[1] > b[3]) for b in (artifact_boxes or {}).get(pi, [])):
                        remove.append(a)
                        stats["artifact_links_removed"] += 1
                    continue
                full = [m for m, (i, n) in under.items() if i >= 0.6 * n and (pi, m) in owner]
                best = max(under, key=lambda m: under[m][0])
                if (pi, best) not in owner:
                    continue
                own, own_parent = owner[(pi, best)]
                if str(own.get("/S")) == "/Link":
                    link = own
                    sp = ptree.new_annot_key(link)
                    a.StructParent = sp
                    link.K = Array([Dictionary(Type=Name("/OBJR"), Obj=a, Pg=pg.obj)] + _kids(link))
                else:
                    mine = [m for m in full if _key(owner[(pi, m)][0]) == _key(own)]
                    link = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/Link"), P=own, Pg=pg.obj))
                    sp = ptree.new_annot_key(link)
                    a.StructParent = sp
                    lk = [pdf.make_indirect(Dictionary(Type=Name("/OBJR"), Obj=a, Pg=pg.obj))]
                    new_k, placed = [], False
                    for x in _kids(own):
                        if _is_mcid(x) and int(x) in mine:
                            lk.append(int(x))
                            ptree.set_mcid(pg.obj, int(x), link)
                            owner[(pi, int(x))] = (link, own)
                            if not placed:
                                new_k.append(link)
                                placed = True
                            continue
                        new_k.append(x)
                        if not placed and _is_mcid(x) and int(x) == best:
                            new_k.append(link)
                            placed = True
                    if not placed:
                        new_k.append(link)
                    link.K = Array(lk)
                    own.K = Array(new_k)
                claimed.add(_key(a))
                stats["annots_attached"] += 1
            if remove:
                keys = {_key(a) for a in remove}
                pg.obj.Annots = Array([a for a in annots if _key(a) not in keys])

    # ---- d. alternate description of every link annotation (+ e. note refs)
    from plugins.digital.digital_stream_tagger import DigitalStreamTagger as T
    for e, p in elems():
        if str(e.get("/S")) != "/Link":
            continue
        pi = _elem_page(e, pages_by_key)
        parts = []
        for x in _kids(e):
            if _is_mcid(x) and pi is not None:
                parts.append((content.get(pi, {}).get(int(x)) or {}).get("text", ""))
        text = " ".join(t.strip() for t in parts if t.strip())
        for x in _kids(e):
            if not _is_objr(x) or x.get("/Obj") is None:
                continue
            annot = x.Obj
            cur = str(annot.get("/Contents", "") or "").strip()
            if scanned and text:
                alt = _link_alt(text, annot)
            elif cur:
                alt = T.clean_alt(cur) or cur
            else:
                alt = _link_alt(text, annot)
            if alt and alt != cur:
                annot.Contents = pikepdf.String(alt)
                stats["alt_set"] += 1
            if "/Alt" in e:
                del e["/Alt"]
        if scanned and text and T._looks_like_note_ref(text) and str(p.get("/S")) not in ("/Reference", "/TOCI", "/Link", "/Lbl"):
            internal = False
            for x in _kids(e):
                if _is_objr(x) and x.get("/Obj") is not None:
                    a = x.Obj
                    act = a.get("/A")
                    if a.get("/Dest") is not None or (act is not None and str(act.get("/S")) == "/GoTo"):
                        internal = True
            if internal:
                ref = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/Reference"), P=p,
                                                   Pg=e.get("/Pg", p.get("/Pg")), K=Array([e])))
                p.K = Array([ref if (_is_elem(k) and _key(k) == _key(e)) else k for k in _kids(p)])
                e.P = ref
                stats["note_refs_wrapped"] += 1
    return stats


# ---------------------------------------------------------------- 2. labels
def wrap_unreadable_labels(pdf):
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    ptree = _ParentTree(pdf, root)
    pages = list(pdf.pages)
    pages_by_key = {p.obj.objgen: i for i, p in enumerate(pages)}
    content = _page_mcid_content(pdf)
    n = 0
    for e, p in _walk(root):
        if str(e.get("/S")) != "/Lbl":
            continue
        pi = _elem_page(e, pages_by_key)
        if pi is None:
            continue
        kids = _kids(e)
        mcids = [int(x) for x in kids if _is_mcid(x)]
        if not mcids:
            continue
        text, graphic = "", False
        for m in mcids:
            d = content.get(pi, {}).get(m)
            if d is None:
                continue
            text += d["text"]
            graphic = graphic or d["graphic"]
        visible = "".join(c for c in text if not c.isspace())
        readable = bool(visible) and any(c.isalnum() for c in visible) and not graphic
        if readable:
            continue
        hint = str(e.get("/ActualText", "") or "").strip()
        alnum_vis = "".join(c for c in visible if c.isalnum())
        if alnum_vis:
            source = alnum_vis                    # a number / letter drawn inside a shape
        elif hint and any(c.isalnum() for c in hint):
            source = hint                         # e.g. numbered badge drawn as a path: "2"
        elif visible:
            source = visible                      # ".", "*", "•"
        else:
            source = hint                         # drawn bullet: label from the extractor
        actual = label_words(source)
        pg = pages[pi]
        span = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/Span"), P=e,
                                            Pg=e.get("/Pg", pg.obj), ActualText=pikepdf.String(actual)))
        span.K = Array(mcids)
        for m in mcids:
            ptree.set_mcid(pg.obj, m, span)
        others = [x for x in kids if not _is_mcid(x)]
        e.K = Array([span] + others)
        if "/ActualText" in e:
            del e["/ActualText"]
        n += 1
    return n


# ---------------------------------------------------------------- 3. empty text tags
_TEXT_TAGS = {"/P", "/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6", "/Span", "/Caption", "/Quote", "/BlockQuote"}
_HEAD_RE = re.compile(r"^/H([1-6])$")


def _has_content(e, depth=0):
    if depth > 64:
        return False
    for x in _kids(e):
        if _is_mcid(x) or _is_objr(x):
            return True
        if isinstance(x, Dictionary) and str(x.get("/Type", "")) == "/MCR":
            return True
        if _is_elem(x) and _has_content(x, depth + 1):
            return True
    return False


def drop_empty_text_tags(pdf):
    """A heading / paragraph / span tag with nothing inside is never written
    (an empty H1 is worse than none). Afterwards the document still gets an
    H1: heading levels are shifted up, or the first paragraph becomes H1."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return {"removed": 0, "h1": ""}
    removed = 0
    changed = True
    while changed:
        changed = False
        for e, p in _walk(root):
            if str(e.get("/S")) in _TEXT_TAGS and not _has_content(e):
                p.K = Array([k for k in _kids(p) if not (_is_elem(k) and _key(k) == _key(e))])
                removed += 1
                changed = True
                break
    note = ""
    # an H1 that was planned but got no content (or was never written) is
    # replaced the same way as in the JSON H1 rule
    if True:
        elems = [e for e, _ in _walk(root)]
        heads = [(e, int(_HEAD_RE.match(str(e.get("/S"))).group(1))) for e in elems if _HEAD_RE.match(str(e.get("/S")))]
        if heads and not any(lv == 1 for _, lv in heads):
            shift = min(lv for _, lv in heads) - 1
            for e, lv in heads:
                e.S = Name(f"/H{lv - shift}")
            note = f"heading levels shifted up by {shift}"
        elif not heads:
            for e in elems:
                if str(e.get("/S")) == "/P" and _has_content(e):
                    e.S = Name("/H1")
                    note = "first paragraph promoted to H1"
                    break
    return {"removed": removed, "h1": note}


# ---------------------------------------------------------------- 4. inadmissible content
_NO_CONTENT = {"/Table", "/TR", "/THead", "/TBody", "/TFoot", "/L", "/LI", "/TOC", "/TOCI"}


def _bbox_attr(e):
    a = e.get("/A")
    cands = list(a) if isinstance(a, Array) else ([a] if a is not None else [])
    for d in cands:
        try:
            if isinstance(d, Dictionary) and "/BBox" in d:
                return [float(v) for v in d.BBox]
        except Exception:
            pass
    return None


def _elem_box(e, pi, content, depth=0):
    b = _bbox_attr(e)
    if b:
        return b
    boxes = []
    for x in _kids(e):
        if _is_mcid(x):
            d = (content.get(pi) or {}).get(int(x))
            if d and d.get("bbox"):
                boxes.append(d["bbox"])
        elif _is_elem(x) and depth < 8:
            bb = _elem_box(x, pi, content, depth + 1)
            if bb:
                boxes.append(bb)
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _dist(box, pt):
    if not box:
        return float("inf")
    dx = max(box[0] - pt[0], 0.0, pt[0] - box[2])
    dy = max(box[1] - pt[1], 0.0, pt[1] - box[3])
    return (dx * dx + dy * dy) ** 0.5


def _insert_sorted(e, m):
    ks = _kids(e)
    out, placed = [], False
    for x in ks:
        if not placed and _is_mcid(x) and int(x) > m:
            out.append(m)
            placed = True
        out.append(x)
    if not placed:
        out.append(m)
    e.K = Array(out)


def fix_inadmissible_content(pdf):
    """PDF/UA: Table, TR, THead/TBody/TFoot, L, LI, TOC and TOCI hold only tags.
    Marked content found directly in one of them is moved into the right child:
    the nearest TD/TH (tables), the LI's LBody (lists) or the TOCI's Reference."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    ptree = _ParentTree(pdf, root)
    pages = list(pdf.pages)
    pages_by_key = {p.obj.objgen: i for i, p in enumerate(pages)}
    content = None
    moved = 0

    def new_child(parent, tag, pg):
        c = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name(tag), P=parent, K=Array()))
        if pg is not None:
            c.Pg = pg
        parent.K = Array(_kids(parent) + [c])
        return c

    for e, p in _walk(root):
        S = str(e.get("/S"))
        if S not in _NO_CONTENT:
            continue
        stray = [int(x) for x in _kids(e) if _is_mcid(x)]
        if not stray:
            continue
        if content is None:
            content = _page_mcid_content(pdf)
        pi = _elem_page(e, pages_by_key)
        if pi is None:
            continue
        pg = pages[pi].obj
        e.K = Array([x for x in _kids(e) if not _is_mcid(x)])
        for m in stray:
            d = (content.get(pi) or {}).get(m) or {}
            b = d.get("bbox")
            pt = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) if b else None
            target = None
            if S in ("/Table", "/TR", "/THead", "/TBody", "/TFoot"):
                cells = [c for c, _ in _walk(e) if str(c.get("/S")) in ("/TD", "/TH")]
                if cells:
                    target = min(cells, key=lambda c: _dist(_elem_box(c, pi, content), pt) if pt else 0)
                else:
                    tr = next((c for c, _ in _walk(e) if str(c.get("/S")) == "/TR"), None) or \
                        (e if S == "/TR" else new_child(e, "/TR", pg))
                    target = new_child(tr, "/TD", pg)
            elif S in ("/L", "/LI"):
                lis = [e] if S == "/LI" else [c for c in _kids(e) if _is_elem(c) and str(c.get("/S")) == "/LI"]
                li = min(lis, key=lambda c: _dist(_elem_box(c, pi, content), pt) if pt else 0) if lis else new_child(e, "/LI", pg)
                target = next((c for c in _kids(li) if _is_elem(c) and str(c.get("/S")) == "/LBody"), None) or new_child(li, "/LBody", pg)
            else:  # TOC / TOCI
                tocis = [e] if S == "/TOCI" else [c for c in _kids(e) if _is_elem(c) and str(c.get("/S")) == "/TOCI"]
                ti = min(tocis, key=lambda c: _dist(_elem_box(c, pi, content), pt) if pt else 0) if tocis else new_child(e, "/TOCI", pg)
                target = next((c for c in _kids(ti) if _is_elem(c) and str(c.get("/S")) == "/Reference"), None) or new_child(ti, "/Reference", pg)
            _insert_sorted(target, m)
            if "/Pg" not in target:
                target.Pg = pg
            ptree.set_mcid(pg, m, target)
            moved += 1
    return moved
