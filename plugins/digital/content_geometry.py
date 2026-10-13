# GUI/plugins/digital/content_geometry.py
"""
Content-stream geometry interpreter for native (Digital / Hybrid) PDFs.

Walks a page's content stream exactly the way a PDF viewer does and produces a
list of "units" — the smallest pieces of content that can be wrapped in a
marked-content sequence without changing how the page looks:

    text   : one text-showing operator (Tj, TJ, ', ")  -> per-glyph boxes + unicode
    path   : path construction ops ... paint op          -> bbox
    image  : Do (image XObject) or inline image          -> bbox
    form   : Do (form XObject)                           -> bbox + glyphs/images inside it
    shade  : sh                                          -> bbox (page / clip)

All coordinates are PDF user space (bottom-left origin, same space as the
page MediaBox), so they can be compared directly with semantic JSON bboxes
once the CropBox offset is added to those.

Nothing here modifies the PDF. It only measures.
"""
import math
import decimal
import pikepdf
from pikepdf import Name

try:
    import pymupdf as _fitz
except Exception:  # pragma: no cover
    try:
        import fitz as _fitz
    except Exception:
        _fitz = None


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

PATH_CONSTRUCT = {"m", "l", "c", "v", "y", "h", "re"}
PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
TEXT_SHOW = {"Tj", "TJ", "'", '"'}


def mat_mul(a, b):
    """a x b for PDF 6-element matrices."""
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


def apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def rect_through(m, x0, y0, x1, y1):
    pts = [apply(m, x0, y0), apply(m, x1, y0), apply(m, x0, y1), apply(m, x1, y1)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def _parse_hex(s):
    s = s.strip().strip("<>").strip()
    return s


def _hex_to_unicode(h):
    h = _parse_hex(h)
    if not h:
        return ""
    try:
        raw = bytes.fromhex(h)
        return raw.decode("utf-16-be", errors="ignore")
    except Exception:
        return ""


def parse_tounicode(data):
    """Parses a ToUnicode CMap: bfchar, bfrange (both forms) and codespace byte width."""
    cmap = {}
    code_bytes = None
    try:
        text = data.decode("latin-1")
    except Exception:
        return cmap, code_bytes

    import re
    tokens = re.findall(r"<[0-9A-Fa-f\s]*>|\[|\]|begincodespacerange|endcodespacerange|beginbfchar|endbfchar|beginbfrange|endbfrange", text)
    mode = None
    buf = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("begincodespacerange", "beginbfchar", "beginbfrange"):
            mode = t
            buf = []
        elif t in ("endcodespacerange", "endbfchar", "endbfrange"):
            if mode == "begincodespacerange":
                for j in range(0, len(buf) - 1, 2):
                    n = len(_parse_hex(buf[j])) // 2
                    code_bytes = n if code_bytes is None else max(code_bytes, n)
            mode = None
            buf = []
        elif mode == "begincodespacerange":
            buf.append(t)
        elif mode == "beginbfchar":
            buf.append(t)
            if len(buf) == 2:
                try:
                    cmap[int(_parse_hex(buf[0]) or "0", 16)] = _hex_to_unicode(buf[1])
                except ValueError:
                    pass
                buf = []
        elif mode == "beginbfrange":
            if t == "[":
                # array form: <lo> <hi> [<u1> <u2> ...]
                arr = []
                i += 1
                while i < len(tokens) and tokens[i] != "]":
                    arr.append(tokens[i])
                    i += 1
                if len(buf) >= 2:
                    try:
                        lo = int(_parse_hex(buf[0]), 16)
                        for k, u in enumerate(arr):
                            cmap[lo + k] = _hex_to_unicode(u)
                    except ValueError:
                        pass
                buf = []
            else:
                buf.append(t)
                if len(buf) == 3:
                    try:
                        lo = int(_parse_hex(buf[0]), 16)
                        hi = int(_parse_hex(buf[1]), 16)
                        dst = _parse_hex(buf[2])
                        base = int(dst, 16) if dst else 0
                        nbytes = max(len(dst) // 2, 2)
                        for k in range(0, min(hi - lo, 65535) + 1):
                            v = base + k
                            try:
                                cmap[lo + k] = v.to_bytes(nbytes, "big").decode("utf-16-be", errors="ignore")
                            except Exception:
                                pass
                    except ValueError:
                        pass
                    buf = []
        i += 1
    return cmap, code_bytes


def _glyph_name_to_unicode(name):
    if not name:
        return ""
    if _fitz is not None:
        try:
            u = _fitz.glyph_name_to_unicode(name)
            if u and u != 65533:
                return chr(u)
        except Exception:
            pass
    if name.startswith("uni") and len(name) >= 7:
        try:
            return chr(int(name[3:7], 16))
        except ValueError:
            pass
    return ""


class FontInfo:
    """Metrics + decoding for one font resource."""

    _cache = {}

    def __init__(self, font_obj):
        self.subtype = str(font_obj.get("/Subtype", ""))
        self.two_byte = self.subtype == "/Type0"
        self.widths = {}
        self.default_width = 0.0
        self.scale = 0.001  # glyph space -> text space
        self.ascent = 0.8
        self.descent = -0.2
        self.to_unicode = {}
        self.encoding_names = {}
        self.fallback = None
        self.vertical = False
        self.codec = None

        try:
            if self.subtype == "/Type0":
                enc = font_obj.get("/Encoding")
                if isinstance(enc, Name) and str(enc).endswith("-V"):
                    self.vertical = True
                desc = font_obj.get("/DescendantFonts")
                cid = desc[0] if desc is not None and len(desc) else None
                if cid is not None:
                    self.default_width = _num(cid.get("/DW", 1000), 1000.0)
                    w = cid.get("/W")
                    if w is not None:
                        self._parse_cid_widths(w)
                    self._read_descriptor(cid.get("/FontDescriptor"))
            else:
                if self.subtype == "/Type3":
                    fm = font_obj.get("/FontMatrix")
                    if fm is not None and len(fm) == 6:
                        fmv = [_num(v) for v in fm]
                        self.scale = abs(fmv[0]) or 0.001
                        bb = font_obj.get("/FontBBox")
                        if bb is not None and len(bb) == 4:
                            y0 = _num(bb[1]) * fmv[3]
                            y1 = _num(bb[3]) * fmv[3]
                            lo, hi = min(y0, y1), max(y0, y1)
                            if -1.5 < lo < hi < 2.5 and hi - lo > 0.3:
                                self.descent, self.ascent = lo, hi
                first = int(_num(font_obj.get("/FirstChar", 0)))
                ws = font_obj.get("/Widths")
                if ws is not None:
                    for k, w in enumerate(ws):
                        self.widths[first + k] = _num(w)
                if self.subtype != "/Type3":
                    self._read_descriptor(font_obj.get("/FontDescriptor"))
                enc = font_obj.get("/Encoding")
                base_enc = enc if isinstance(enc, Name) else (enc.get("/BaseEncoding") if isinstance(enc, pikepdf.Dictionary) else None)
                self.codec = {"/WinAnsiEncoding": "cp1252", "/MacRomanEncoding": "mac_roman"}.get(str(base_enc) if base_enc is not None else "", "cp1252" if self.subtype in ("/Type1", "/TrueType", "/MMType1") else None)
                if isinstance(enc, pikepdf.Dictionary) and "/Differences" in enc:
                    code = 0
                    for item in enc.Differences:
                        if isinstance(item, Name):
                            self.encoding_names[code] = str(item)[1:]
                            code += 1
                        else:
                            code = int(_num(item))
                if not self.widths:
                    base = str(font_obj.get("/BaseFont", "/Helvetica"))[1:]
                    try:
                        self.fallback = _fitz.Font(base) if _fitz else None
                    except Exception:
                        try:
                            self.fallback = _fitz.Font("helv") if _fitz else None
                        except Exception:
                            self.fallback = None
        except Exception:
            pass

        tu = font_obj.get("/ToUnicode")
        if isinstance(tu, pikepdf.Stream):
            try:
                cmap, nb = parse_tounicode(tu.read_bytes())
                self.to_unicode = cmap
                # NOTE: the CMap's codespacerange is NOT used to decide the code
                # width. Simple fonts (Type1, TrueType, Type3, MMType1) always use
                # 1-byte codes (ISO 32000 9.6/9.7); many generators still declare
                # <0000> <FFFF> in their ToUnicode CMaps. Only Type0 is multi-byte.
            except Exception:
                pass

    def _read_descriptor(self, fd):
        if fd is None:
            return
        try:
            a = _num(fd.get("/Ascent", 0)) / 1000.0
            d = _num(fd.get("/Descent", 0)) / 1000.0
            if 0.3 < a < 2.0:
                self.ascent = a
            if -1.0 < d <= 0.0:
                self.descent = d if d < 0 else self.descent
            mw = fd.get("/MissingWidth")
            if mw is not None and not self.default_width:
                self.default_width = _num(mw)
        except Exception:
            pass

    def _parse_cid_widths(self, w):
        items = list(w)
        i = 0
        while i < len(items):
            try:
                c_first = int(_num(items[i]))
                nxt = items[i + 1]
                if isinstance(nxt, pikepdf.Array):
                    for k, val in enumerate(nxt):
                        self.widths[c_first + k] = _num(val)
                    i += 2
                else:
                    c_last = int(_num(nxt))
                    val = _num(items[i + 2])
                    for c in range(c_first, min(c_last, c_first + 65535) + 1):
                        self.widths[c] = val
                    i += 3
            except Exception:
                break

    @classmethod
    def get(cls, font_obj):
        try:
            key = font_obj.objgen if font_obj.is_indirect else id(font_obj)
        except Exception:
            key = id(font_obj)
        fi = cls._cache.get(key)
        if fi is None:
            fi = FontInfo(font_obj)
            cls._cache[key] = fi
        return fi

    def codes(self, raw):
        if self.two_byte:
            return [((raw[i] << 8) | (raw[i + 1] if i + 1 < len(raw) else 0), 2) for i in range(0, len(raw), 2)]
        return [(b, 1) for b in raw]

    def width(self, code):
        """Glyph advance in text-space units (before font size)."""
        if code in self.widths:
            return self.widths[code] * self.scale
        if self.fallback is not None:
            try:
                return self.fallback.glyph_advance(code)
            except Exception:
                pass
        return (self.default_width or 500.0) * self.scale if self.subtype != "/Type3" else (self.default_width * self.scale)

    def unicode(self, code):
        u = self.to_unicode.get(code)
        if u:
            return u
        name = self.encoding_names.get(code)
        if name:
            u = _glyph_name_to_unicode(name)
            if u:
                return u
        if not self.two_byte and 32 <= code < 127:
            return chr(code)
        if not self.two_byte and self.codec and code >= 128:
            try:
                return bytes([code]).decode(self.codec)
            except Exception:
                pass
        return ""


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

class Glyph:
    __slots__ = ("bbox", "cx", "cy", "code", "uni", "height", "string_index", "byte_start", "byte_end")

    def __init__(self, bbox, code, uni, height, string_index, byte_start, byte_end):
        self.bbox = bbox
        self.cx = (bbox[0] + bbox[2]) / 2.0
        self.cy = (bbox[1] + bbox[3]) / 2.0
        self.code = code
        self.uni = uni
        self.height = height
        self.string_index = string_index  # index of the string inside a TJ array (0 for Tj)
        self.byte_start = byte_start
        self.byte_end = byte_end


class Unit:
    __slots__ = ("kind", "start", "end", "bbox", "glyphs", "invisible", "xobj_name", "inner", "q_depth", "in_bt")

    def __init__(self, kind, start, end, bbox, glyphs=None, invisible=False, xobj_name=None, inner=None, q_depth=0, in_bt=False):
        self.kind = kind          # text | path | image | form | shade
        self.start = start        # first instruction index (inclusive)
        self.end = end            # last instruction index (inclusive)
        self.bbox = bbox
        self.glyphs = glyphs or []
        self.invisible = invisible
        self.xobj_name = xobj_name
        self.inner = inner        # for forms: list of inner units (geometry in page space)
        self.q_depth = q_depth
        self.in_bt = in_bt


def _union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class ContentInterpreter:
    """Interprets one content stream (page or form) and returns its units."""

    def __init__(self, resources, base_ctm=IDENTITY, page_box=None, depth=0, visited=None):
        self.resources = resources if resources is not None else pikepdf.Dictionary()
        self.base_ctm = base_ctm
        self.page_box = page_box or (0.0, 0.0, 612.0, 792.0)
        self.depth = depth
        self.visited = visited if visited is not None else set()

    def _font(self, name):
        try:
            fonts = self.resources.get("/Font")
            if fonts is not None and name in fonts:
                return FontInfo.get(fonts[name])
        except Exception:
            pass
        return None

    def _xobject(self, name):
        try:
            xo = self.resources.get("/XObject")
            if xo is not None and name in xo:
                return xo[name]
        except Exception:
            pass
        return None

    def run(self, instructions):
        units = []
        ctm = self.base_ctm
        gs_stack = []
        # text state (part of graphics state)
        font = None
        font_name = None
        tfs = 1.0
        tc = 0.0
        tw = 0.0
        th = 1.0
        tl = 0.0
        ts = 0.0
        tr = 0
        tm = IDENTITY
        tlm = IDENTITY
        in_bt = False

        path_start = None
        path_pts = []
        cur_pt = (0.0, 0.0)

        for idx, inst in enumerate(instructions):
            if isinstance(inst, pikepdf.ContentStreamInlineImage):
                bbox = rect_through(ctm, 0, 0, 1, 1)
                units.append(Unit("image", idx, idx, bbox, q_depth=len(gs_stack), in_bt=in_bt))
                continue
            op = str(inst.operator)
            ops = inst.operands

            if op == "q":
                gs_stack.append((ctm, font, font_name, tfs, tc, tw, th, tl, ts, tr))
            elif op == "Q":
                if gs_stack:
                    ctm, font, font_name, tfs, tc, tw, th, tl, ts, tr = gs_stack.pop()
            elif op == "cm" and len(ops) == 6:
                ctm = mat_mul(tuple(_num(v) for v in ops), ctm)
            elif op == "BT":
                in_bt = True
                tm = IDENTITY
                tlm = IDENTITY
            elif op == "ET":
                in_bt = False
            elif op == "Tf" and len(ops) >= 2:
                font_name = ops[0]
                font = self._font(font_name)
                tfs = _num(ops[1], 1.0)
            elif op == "Tc" and ops:
                tc = _num(ops[0])
            elif op == "Tw" and ops:
                tw = _num(ops[0])
            elif op == "Tz" and ops:
                th = _num(ops[0], 100.0) / 100.0
            elif op == "TL" and ops:
                tl = _num(ops[0])
            elif op == "Ts" and ops:
                ts = _num(ops[0])
            elif op == "Tr" and ops:
                tr = int(_num(ops[0]))
            elif op == "Tm" and len(ops) == 6:
                tm = tuple(_num(v) for v in ops)
                tlm = tm
            elif op in ("Td", "TD") and len(ops) == 2:
                tx, ty = _num(ops[0]), _num(ops[1])
                if op == "TD":
                    tl = -ty
                tlm = mat_mul((1, 0, 0, 1, tx, ty), tlm)
                tm = tlm
            elif op == "T*":
                tlm = mat_mul((1, 0, 0, 1, 0, -tl), tlm)
                tm = tlm

            # ---------------- text showing ----------------
            if op in TEXT_SHOW:
                if op == "'":
                    tlm = mat_mul((1, 0, 0, 1, 0, -tl), tlm)
                    tm = tlm
                    items = [ops[0]] if ops else []
                elif op == '"':
                    if len(ops) >= 3:
                        tw = _num(ops[0])
                        tc = _num(ops[1])
                    tlm = mat_mul((1, 0, 0, 1, 0, -tl), tlm)
                    tm = tlm
                    items = [ops[2]] if len(ops) >= 3 else []
                elif op == "Tj":
                    items = [ops[0]] if ops else []
                else:
                    items = list(ops[0]) if ops else []

                glyphs = []
                fi = font
                asc = fi.ascent if fi else 0.8
                desc = fi.descent if fi else -0.2
                s_index = -1
                for item in items:
                    if isinstance(item, pikepdf.String):
                        s_index += 1
                        raw = bytes(item)
                        codes = fi.codes(raw) if fi else [(b, 1) for b in raw]
                        pos = 0
                        for code, nb in codes:
                            w0 = fi.width(code) if fi else 0.5
                            trm = mat_mul((tfs * th, 0, 0, tfs, 0, ts), mat_mul(tm, ctm))
                            gb = rect_through(trm, 0, desc, max(w0, 0.0) if w0 else 0.0001, asc)
                            uni = fi.unicode(code) if fi else (chr(code) if 32 <= code < 127 else "")
                            hgt = abs(math.hypot(trm[2], trm[3])) * (asc - desc)
                            glyphs.append(Glyph(gb, code, uni, hgt, s_index, pos, pos + nb))
                            pos += nb
                            adv = (w0 * tfs + tc + (tw if (nb == 1 and code == 32) else 0.0)) * th
                            tm = mat_mul((1, 0, 0, 1, adv, 0), tm)
                    else:
                        try:
                            n = _num(item)
                        except Exception:
                            n = 0.0
                        tm = mat_mul((1, 0, 0, 1, -n / 1000.0 * tfs * th, 0), tm)
                bbox = _union([g.bbox for g in glyphs])
                invisible = tr in (3, 7)
                units.append(Unit("text", idx, idx, bbox, glyphs=glyphs, invisible=invisible,
                                  q_depth=len(gs_stack), in_bt=in_bt))
                continue

            # ---------------- paths ----------------
            if op in PATH_CONSTRUCT:
                if path_start is None:
                    path_start = idx
                    path_pts = []
                vals = [_num(v) for v in ops]
                if op == "re" and len(vals) == 4:
                    x, y, w, h = vals
                    for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                        path_pts.append(apply(ctm, px, py))
                else:
                    for k in range(0, len(vals) - 1, 2):
                        path_pts.append(apply(ctm, vals[k], vals[k + 1]))
                continue
            if op in ("W", "W*"):
                continue
            if op in PATH_PAINT:
                if path_start is not None:
                    if op != "n":
                        if path_pts:
                            xs = [p[0] for p in path_pts]
                            ys = [p[1] for p in path_pts]
                            bbox = (min(xs), min(ys), max(xs), max(ys))
                        else:
                            bbox = None
                        units.append(Unit("path", path_start, idx, bbox, q_depth=len(gs_stack), in_bt=in_bt))
                    path_start = None
                    path_pts = []
                continue

            if op == "sh":
                units.append(Unit("shade", idx, idx, self.page_box, q_depth=len(gs_stack), in_bt=in_bt))
                continue

            if op == "Do" and ops:
                name = ops[0]
                xo = self._xobject(name)
                sub = str(xo.get("/Subtype", "")) if xo is not None else ""
                if sub == "/Image":
                    units.append(Unit("image", idx, idx, rect_through(ctm, 0, 0, 1, 1), xobj_name=str(name),
                                      q_depth=len(gs_stack), in_bt=in_bt))
                elif sub == "/Form":
                    m = xo.get("/Matrix")
                    fm = tuple(_num(v) for v in m) if m is not None and len(m) == 6 else IDENTITY
                    fctm = mat_mul(fm, ctm)
                    bb = xo.get("/BBox")
                    bbox = rect_through(fctm, *[_num(v) for v in bb]) if bb is not None and len(bb) == 4 else None
                    inner = []
                    key = None
                    try:
                        key = xo.objgen
                    except Exception:
                        pass
                    if self.depth < 12 and key not in self.visited:
                        try:
                            sub_visited = set(self.visited)
                            if key is not None:
                                sub_visited.add(key)
                            res = xo.get("/Resources", self.resources)
                            sub = ContentInterpreter(res, fctm, self.page_box, self.depth + 1, sub_visited)
                            inner = sub.run(list(pikepdf.parse_content_stream(xo)))
                        except Exception:
                            inner = []
                    units.append(Unit("form", idx, idx, bbox, xobj_name=str(name), inner=inner,
                                      q_depth=len(gs_stack), in_bt=in_bt))
                continue
        return units


def flatten_glyphs(unit):
    """All glyphs of a unit (descending into forms). Returns list of (glyph, invisible)."""
    out = []
    if unit.kind == "text":
        out.extend((g, unit.invisible) for g in unit.glyphs)
    elif unit.kind == "form" and unit.inner:
        for u in unit.inner:
            out.extend(flatten_glyphs(u))
    return out


def flatten_images(unit):
    out = []
    if unit.kind == "image":
        out.append(unit.bbox)
    elif unit.kind == "form" and unit.inner:
        for u in unit.inner:
            out.extend(flatten_images(u))
    return out
