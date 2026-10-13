import base64
import hashlib
import io
import os
import re

import fitz
import pikepdf


# ---------------------------------------------------------------------------
# Font names
# ---------------------------------------------------------------------------
_STYLE_WORDS = ("bold", "italic", "oblique", "regular", "roman", "book", "normal",
                "medium", "semibold", "demibold", "black", "heavy", "light", "it", "bd")


def _norm_family(s):
    s = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    for suf in ("psmt", "mt", "ps"):
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[: -len(suf)]
    return s


def parse_base_font(base_font):
    """'/ABCDEF+Arial,BoldItalic', 'TimesNewRomanPS-BoldMT' -> ('arial', bold, italic)."""
    name = str(base_font or "").lstrip("/")
    name = re.sub(r"^[A-Z]{6}\+", "", name)
    family, style = name, ""
    if "," in name:
        family, style = name.split(",", 1)
    elif "-" in name:
        head, tail = name.rsplit("-", 1)
        if any(w in tail.lower() for w in _STYLE_WORDS):
            family, style = head, tail
    sl, fl = style.lower(), family.lower()
    bold = bool(re.search(r"bold|black|heavy|demi", sl)) or bool(re.search(r"bold|black|heavy", fl))
    italic = bool(re.search(r"italic|oblique|^it$|bdit|boldit", sl)) or bool(re.search(r"italic|oblique", fl))
    if not style:
        fl = re.sub(r"(bold|italic|oblique|regular)", "", fl)
    return _norm_family(fl), bold, italic


# ---------------------------------------------------------------------------
# Font programs: installed fonts, then bundled metric-compatible fonts
# ---------------------------------------------------------------------------
def _font_dirs():
    dirs = []
    win = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if win:
        dirs.append(os.path.join(win, "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             os.path.expanduser("~/.local/share/fonts"), "/Library/Fonts",
             "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    return [d for d in dirs if os.path.isdir(d)]


_SYSTEM_INDEX = None


def _system_font_index():
    """{(family, bold, italic): (path, face_index)} of installed TrueType-outline
    fonts whose licence allows embedding. Built once per process, on first need."""
    global _SYSTEM_INDEX
    if _SYSTEM_INDEX is not None:
        return _SYSTEM_INDEX
    index, scores = {}, {}
    try:
        from fontTools.ttLib import TTFont, TTCollection
    except Exception:
        _SYSTEM_INDEX = index
        return index

    def add(path, num, f):
        try:
            if "glyf" not in f:
                return                              # CFF outlines cannot go in FontFile2
            os2 = f["OS/2"] if "OS/2" in f else None
            fs_type = getattr(os2, "fsType", 0) if os2 is not None else 0
            if fs_type & 0x000F == 0x0002:          # restricted licence: no embedding
                return
            names = f["name"]
            fam = names.getDebugName(16) or names.getDebugName(1) or ""
            ps = names.getDebugName(6) or ""
            sel = getattr(os2, "fsSelection", 0) if os2 is not None else 0
            weight = getattr(os2, "usWeightClass", 400) if os2 is not None else 400
            mac = f["head"].macStyle if "head" in f else 0
            bold = bool(sel & 0x20) or weight >= 600 or bool(mac & 1)
            italic = bool(sel & 0x01) or bool(mac & 2)
            width = getattr(os2, "usWidthClass", 5) if os2 is not None else 5
            # several faces share a family name (Light, Condensed, ...): prefer the
            # one closest to normal weight (400) / bold (700) and normal width
            for key in {(_norm_family(fam), bold, italic), parse_base_font(ps)}:
                if not key[0]:
                    continue
                score = abs(weight - (700 if key[1] else 400)) + 100 * abs(width - 5)
                if key not in index or score < scores[key]:
                    index[key] = (path, num)
                    scores[key] = score
        except Exception:
            pass

    for d in _font_dirs():
        for root, _dirs, files in os.walk(d):
            for fn in files:
                ext = fn.lower().rsplit(".", 1)[-1]
                if ext not in ("ttf", "ttc", "otf"):
                    continue
                path = os.path.join(root, fn)
                try:
                    if ext == "ttc":
                        for i, f in enumerate(TTCollection(path, lazy=True).fonts):
                            add(path, i, f)
                    else:
                        add(path, -1, TTFont(path, lazy=True))
                except Exception:
                    continue
    _SYSTEM_INDEX = index
    return index


_BUNDLED = {
    "sans": ("liberation_sans_regular", "liberation_sans_bold", "liberation_sans_italic", "liberation_sans_bolditalic"),
    "serif": ("liberation_serif_regular", "liberation_serif_bold", "liberation_serif_italic", "liberation_serif_bolditalic"),
}


def _bundled_for(family, bold, italic):
    """Liberation Sans/Serif have the same glyph widths as Arial/Helvetica and
    Times/Times New Roman."""
    if family.startswith(("arial", "helvetica", "liberationsans", "arimo")):
        kind = "sans"
    elif family.startswith(("times", "liberationserif", "tinos")):
        kind = "serif"
    else:
        return None
    mod = _BUNDLED[kind][(1 if bold else 0) + (2 if italic else 0)]
    try:
        m = __import__(f"renderer.font_data.{mod}", fromlist=["FONT_B64"])
        return base64.b64decode(m.FONT_B64), "Liberation " + ("Sans" if kind == "sans" else "Serif") + " (bundled)"
    except Exception:
        return None


def _load_face(path, num):
    from fontTools.ttLib import TTFont
    f = TTFont(path, fontNumber=num) if num is not None and num >= 0 else TTFont(path)
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Encoding / subsetting
# ---------------------------------------------------------------------------
def _code_unicodes(font_dict, first, last):
    """Unicode reached by every code first..last under the font's encoding."""
    base, diffs = "cp1252", {}
    enc = font_dict.get("/Encoding")
    if isinstance(enc, pikepdf.Name):
        base = "mac_roman" if str(enc) == "/MacRomanEncoding" else "cp1252"
    elif isinstance(enc, pikepdf.Dictionary):
        be = enc.get("/BaseEncoding")
        if be is not None and str(be) == "/MacRomanEncoding":
            base = "mac_roman"
        code = 0
        for item in enc.get("/Differences", []):
            if isinstance(item, pikepdf.Name):
                diffs[code] = str(item)[1:]
                code += 1
            else:
                try:
                    code = int(item)
                except Exception:
                    pass
    try:
        from fontTools.agl import toUnicode
    except Exception:
        toUnicode = None
    out = set()
    for c in range(first, last + 1):
        if c in diffs and toUnicode is not None:
            u = toUnicode(diffs[c])
            if u:
                out.update(ord(ch) for ch in u)
                continue
        try:
            out.add(ord(bytes([c]).decode(base)))
        except Exception:
            pass
    return out


def _is_symbolic_program(data):
    """True when the program has no Unicode cmap (Symbol, Wingdings, ...)."""
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(io.BytesIO(data), lazy=True)
        return not any(t.platformID == 3 and t.platEncID in (1, 10) for t in f["cmap"].tables)
    except Exception:
        return False


def _subset(data, font_dict, first, last, symbolic):
    """Keep only the glyphs the font's codes can reach (glyph names and cmaps are
    kept, so code -> glyph lookup is unchanged). Falls back to the full program."""
    try:
        import logging
        from fontTools import subset
        from fontTools.ttLib import TTFont
        logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
        f = TTFont(io.BytesIO(data))
        if symbolic:
            unis = set(range(first, last + 1)) | {0xF000 + c for c in range(first, last + 1)}
        else:
            unis = _code_unicodes(font_dict, first, last)
        opts = subset.Options()
        opts.notdef_outline = True
        opts.glyph_names = True
        opts.legacy_cmap = True
        opts.symbol_cmap = True
        opts.name_IDs = ["*"]
        opts.name_languages = ["*"]
        opts.layout_features = []
        opts.recalc_bounds = False
        sub = subset.Subsetter(options=opts)
        sub.populate(unicodes=unis)
        sub.subset(f)
        buf = io.BytesIO()
        f.save(buf)
        return buf.getvalue(), True
    except Exception:
        return data, False


def _subset_tag(seed):
    n = int(hashlib.md5(seed.encode("utf-8", "replace")).hexdigest(), 16)
    tag = ""
    for _ in range(6):
        tag += chr(ord("A") + n % 26)
        n //= 26
    return tag


_STD14_KEY = {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}


def _std14_fitz(base_font):
    fam, bold, italic = parse_base_font(base_font)
    key = _STD14_KEY[(bold, italic)]
    if fam.startswith("times"):
        key = {"helv": "tiro", "hebo": "tibo", "heit": "tiit", "hebi": "tibi"}[key]
    elif fam.startswith("courier"):
        key = {"helv": "cour", "hebo": "cobo", "heit": "coit", "hebi": "cobi"}[key]
    return fitz.Font(key)


class DigitalFontRepair:
    """Embeds every simple font that has no font program (PDF/UA-1 7.21.4.1).

    Font program, in order of preference:
      1. the same font installed on this computer (Windows/macOS/Linux font folders),
      2. a bundled metric-compatible font (Liberation Sans for Arial/Helvetica,
         Liberation Serif for Times/Times New Roman),
      3. the same family installed in another style.
    The font dictionary is updated IN PLACE (a font shared by many pages is embedded
    once). Its /Widths, /FirstChar, /LastChar, /Encoding and descriptor metrics are
    kept, so every glyph stays exactly where it was. The program is subset to the
    glyphs the font's codes can reach."""

    def __init__(self, pdf: pikepdf.Pdf):
        self.pdf = pdf
        self.diagnostics = []
        self._done = set()

    # ------------------------------------------------------------------ walk
    def repair_fonts(self):
        seen_res = set()

        def visit_resources(res, depth=0):
            if res is None or depth > 12 or not isinstance(res, pikepdf.Dictionary):
                return
            try:
                if res.is_indirect:
                    if res.objgen in seen_res:
                        return
                    seen_res.add(res.objgen)
            except Exception:
                pass
            fonts = res.get("/Font")
            if fonts is not None:
                for name, font in list(fonts.items()):
                    if isinstance(font, pikepdf.Dictionary):
                        self._repair_font(name, font, fonts)
                        if str(font.get("/Subtype", "")) == "/Type3":
                            visit_resources(font.get("/Resources"), depth + 1)
            for _n, xo in (res.get("/XObject") or {}).items():
                try:
                    if str(xo.get("/Subtype")) == "/Form":
                        visit_resources(xo.get("/Resources"), depth + 1)
                except Exception:
                    pass
            for _n, pat in (res.get("/Pattern") or {}).items():
                try:
                    visit_resources(pat.get("/Resources"), depth + 1)
                except Exception:
                    pass

        for page in self.pdf.pages:
            self._drop_unused_page_fonts(page)
            try:
                visit_resources(page.get("/Resources"))
            except Exception:
                pass
            for annot in page.get("/Annots") or []:
                try:
                    ap = annot.get("/AP")
                    if ap is None:
                        continue
                    for state in ap.values():
                        if isinstance(state, pikepdf.Stream):
                            visit_resources(state.get("/Resources"))
                        elif isinstance(state, pikepdf.Dictionary):
                            for st in state.values():
                                if isinstance(st, pikepdf.Stream):
                                    visit_resources(st.get("/Resources"))
                except Exception:
                    pass

    def _drop_unused_page_fonts(self, page):
        """The tool adds /helv, /fallback and /FUnicode to every page for OCR text.
        Remove them from pages whose content never selects them (only when the
        page's font dictionary is not shared with another page)."""
        try:
            res = page.get("/Resources")
            fonts = res.get("/Font") if res is not None else None
            if fonts is None:
                return
            if fonts.is_indirect:
                for other in self.pdf.pages:
                    if other.objgen == page.objgen:
                        continue
                    ores = other.get("/Resources")
                    of = ores.get("/Font") if ores is not None else None
                    if of is not None and of.is_indirect and of.objgen == fonts.objgen:
                        return
            used = set()
            for ins in pikepdf.parse_content_stream(page):
                if not isinstance(ins, pikepdf.ContentStreamInlineImage) and str(ins.operator) == "Tf" and ins.operands:
                    used.add(str(ins.operands[0]))
            for name in list(fonts.keys()):
                if name in ("/helv", "/fallback", "/FUnicode") and name not in used:
                    del fonts[name]
        except Exception:
            pass

    # --------------------------------------------------------------- checks
    def _is_embedded(self, font: pikepdf.Dictionary) -> bool:
        if font.get("/Subtype") == "/Type0":
            descendants = font.get("/DescendantFonts", [])
            if descendants:
                df = descendants[0]
                if "/FontDescriptor" in df:
                    fd = df.FontDescriptor
                    if "/FontFile" in fd or "/FontFile2" in fd or "/FontFile3" in fd:
                        return True
        if "/FontDescriptor" in font:
            fd = font.FontDescriptor
            if "/FontFile" in fd or "/FontFile2" in fd or "/FontFile3" in fd:
                return True
        return False

    # --------------------------------------------------------------- repair
    def _repair_font(self, name, font, fonts_dict):
        if font.is_indirect:
            if font.objgen in self._done:
                return
            self._done.add(font.objgen)
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type3":
            return                                  # glyphs drawn by content streams
        if subtype == "/Type0":
            emb = self._is_embedded(font)
            self.diagnostics.append({
                "resource": str(name),
                "embedded": emb,
                "action": "PRESERVED",
                "reason": "Type0 CID fonts cannot be safely replaced with 1-byte fallbacks without corrupting the text layer",
                "status": "UNCHANGED" if emb else "UNRESOLVED",
            })
            return
        if self._is_embedded(font):
            self.diagnostics.append({"resource": str(name), "embedded": True, "action": "PRESERVED", "status": "UNCHANGED"})
            return

        base_font = str(font.get("/BaseFont", ""))
        if "ZapfDingbats" in base_font:
            self._repair_zapf_dingbats(name, font, fonts_dict)
            return

        family, bold, italic = parse_base_font(base_font)
        data = source = None
        hit = _system_font_index().get((family, bold, italic)) if family else None
        if hit:
            try:
                data, source = _load_face(*hit), os.path.basename(hit[0])
            except Exception:
                data = None
        if data is None:
            b = _bundled_for(family, bold, italic)
            if b:
                data, source = b
        if data is None and family:
            for (fam, _b, _i), loc in _system_font_index().items():
                if fam == family:
                    try:
                        data, source = _load_face(*loc), os.path.basename(loc[0]) + " (closest style)"
                        break
                    except Exception:
                        continue
        if data is None:
            self.diagnostics.append({
                "resource": str(name), "original_font": base_font, "embedded": False,
                "action": "PRESERVED", "reason": "No installed or bundled font program for this font",
                "status": "UNRESOLVED",
            })
            return
        try:
            self._embed(name, font, base_font, data, source)
        except Exception as e:
            self.diagnostics.append({
                "resource": str(name), "original_font": base_font, "embedded": False,
                "action": "PRESERVED", "reason": f"embedding failed: {e}", "status": "UNRESOLVED",
            })

    def _embed(self, name, font, base_font, data, source):
        has_widths = "/Widths" in font and "/FirstChar" in font
        first = int(font.get("/FirstChar", 32)) if has_widths else 32
        last = int(font.get("/LastChar", 255)) if has_widths else 255
        symbolic_prog = _is_symbolic_program(data)
        fd = font.get("/FontDescriptor")
        symbolic = symbolic_prog
        if fd is not None:
            try:
                symbolic = symbolic_prog or bool(int(fd.get("/Flags", 32)) & 4)
            except Exception:
                pass

        prog, subsetted = _subset(data, font, first, last, symbolic)
        ff = self.pdf.make_stream(prog)
        ff.Length1 = len(prog)

        clean = re.sub(r"^[A-Z]{6}\+", "", base_font.lstrip("/"))
        seed = clean + (repr(font.objgen) if font.is_indirect else str(name))
        new_name = f"{_subset_tag(seed)}+{clean}" if subsetted else clean

        if fd is None:
            fd = self._new_descriptor(base_font, symbolic_prog)
            font.FontDescriptor = fd
        for k in ("/FontFile", "/FontFile3"):
            if k in fd:
                del fd[k]
        fd.FontFile2 = ff
        fd.FontName = pikepdf.Name("/" + new_name)

        if not has_widths:
            font.FirstChar, font.LastChar = 32, 255
            font.Widths = self._base14_widths(base_font)
        if "/Encoding" not in font and not symbolic_prog:
            font.Encoding = pikepdf.Name("/WinAnsiEncoding")
        old_subtype = str(font.get("/Subtype", ""))
        font.Subtype = pikepdf.Name("/TrueType")    # FontFile2 holds a TrueType program
        font.BaseFont = pikepdf.Name("/" + new_name)

        self.diagnostics.append({
            "resource": str(name), "original_font": base_font, "subtype": old_subtype,
            "embedded": False, "action": "EMBEDDED", "replacement": source,
            "subset": subsetted, "font_file": "FontFile2", "status": "SUCCESS",
        })

    def _new_descriptor(self, base_font, symbolic):
        _fam, bold, italic = parse_base_font(base_font)
        f = _std14_fitz(base_font)
        flags = (4 if symbolic else 32) | (64 if italic else 0) | (262144 if bold else 0)
        return self.pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"),
            FontName=pikepdf.Name("/" + base_font.strip("/")),
            Flags=flags,
            FontBBox=[int(f.bbox.x0 * 1000), int(f.bbox.y0 * 1000), int(f.bbox.x1 * 1000), int(f.bbox.y1 * 1000)],
            ItalicAngle=-12 if italic else 0,
            Ascent=int(f.ascender * 1000),
            Descent=int(f.descender * 1000),
            CapHeight=718,
            StemV=120 if bold else 85,
        ))

    @staticmethod
    def _base14_widths(base_font):
        """Standard-14 widths (what viewers used before embedding), codes 32..255."""
        f = _std14_fitz(base_font)
        widths = []
        for c in range(32, 256):
            try:
                u = ord(bytes([c]).decode("cp1252"))
            except Exception:
                u = c
            widths.append(int(round(f.glyph_advance(u) * 1000)))
        return widths

    def _repair_zapf_dingbats(self, name: str, font: pikepdf.Dictionary, fonts_dict: pikepdf.Dictionary):
        """
        Replaces unembedded ZapfDingbats with a valid Type3 font.
        Code 0x6E ('n') is rendered as a solid square (Black Square).
        Provides a ToUnicode CMap mapping 0x6E to U+25A0.
        """
        char_procs = self.pdf.make_indirect(pikepdf.Dictionary(
            n=self.pdf.make_stream(b"761 0 0 0 761 761 d1\n0 0 761 761 re f")
        ))
        widths = pikepdf.Array([761])
        encoding = self.pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Encoding"),
            Differences=pikepdf.Array([110, pikepdf.Name("/n")])
        ))
        cmap = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin\n"
            "begincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /ZapfDingbats-UTF8 def\n"
            "/CMapType 2 def\n"
            "1 begincodespacerange\n"
            "<00> <FF>\n"
            "endcodespacerange\n"
            "1 beginbfchar\n"
            "<6E> <25A0>\n"
            "endbfchar\n"
            "endcmap\n"
            "CMapName currentdict /CMap defineresource pop\n"
            "end\n"
            "end\n"
        ).encode('utf-8')
        tounicode_stream = self.pdf.make_stream(cmap)
        type3_font = self.pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type3"),
            Name=pikepdf.Name(f"/{name.strip('/')}"),
            FontBBox=pikepdf.Array([0, 0, 761, 761]),
            FontMatrix=pikepdf.Array([0.001, 0, 0, 0.001, 0, 0]),
            CharProcs=char_procs,
            Encoding=encoding,
            FirstChar=110,
            LastChar=110,
            Widths=widths,
            ToUnicode=tounicode_stream
        ))
        fonts_dict[name] = type3_font
        self.diagnostics.append({
            "resource": str(name),
            "original_font": "/ZapfDingbats",
            "subtype": "/Type3",
            "embedded": True,
            "action": "REPLACED",
            "replacement": "Type3",
            "encoding": "Custom",
            "status": "SUCCESS"
        })
