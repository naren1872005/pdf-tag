# GUI_V1/plugins/digital/form_inliner.py
"""
Form XObjects drawn by a page are inlined into the page's own content before
tagging (Digital / Hybrid).

Why: a Form XObject is ONE painting operation for the page. Designers often
put unrelated things in one form (InDesign: the publisher logo in the top
corner, the series wordmark and the copyright line at the bottom of the
page). Tagged as a unit, the whole form ends up in a single tag - a <P> or
<Figure> whose box spans the full page edge - and the pieces cannot get
their own tags. Inlined, every image, path and text run of the form is
tagged on its own.

How: `/Fm Do` is replaced by
        q  <form /Matrix> cm  <form /BBox> re W n  <form content>  Q
with the form's resources copied into the page's resources under unique
names. The page looks exactly the same (same operators, same clip, same
transform). Forms that cannot be inlined without changing the appearance
are left as they are:
  * transparency groups (/Group) - their content is composited as one layer
  * forms with inline images that name a colour space from the form
  * forms used as patterns / annotation appearances (never on the page)
"""
import pikepdf
from pikepdf import Array, Dictionary, Name, Operator

# operator -> (resource category, operand index)
_RES_OPERANDS = {
    "Tf": ("/Font", 0),
    "Do": ("/XObject", 0),
    "gs": ("/ExtGState", 0),
    "cs": ("/ColorSpace", 0),
    "CS": ("/ColorSpace", 0),
    "sh": ("/Shading", 0),
}
_DEVICE_CS = {"/DeviceGray", "/DeviceRGB", "/DeviceCMYK", "/Pattern"}
_MAX_DEPTH = 4
_MAX_OPS = 200000


def _inlinable(form):
    try:
        if str(form.get("/Subtype")) != "/Form":
            return False
        if form.get("/Group") is not None:
            return False
        if form.get("/Ref") is not None or form.get("/OPI") is not None:
            return False
        return True
    except Exception:
        return False


def _rename_operands(ins, form_res, page_res, tag, new_res):
    """Rewrite resource names used by one instruction. Returns the instruction
    (possibly new) or None when it cannot be rewritten safely."""
    if isinstance(ins, pikepdf.ContentStreamInlineImage):
        try:
            cs = ins.iimage.obj.get("/CS") if hasattr(ins, "iimage") else None
        except Exception:
            cs = None
        if cs is not None and isinstance(cs, Name) and str(cs) not in _DEVICE_CS and str(cs) not in ("/G", "/RGB", "/CMYK", "/I"):
            return None
        return ins
    op = str(ins.operator)
    operands = list(ins.operands)

    def mapped(cat, name):
        if not isinstance(name, Name):
            return name
        if cat == "/ColorSpace" and str(name) in _DEVICE_CS:
            return name
        cat_dict = form_res.get(cat) if form_res is not None else None
        if cat_dict is None or name not in cat_dict:
            return name                      # not a form resource: page/default name
        new = Name(f"/TP{tag}_{str(name)[1:]}")
        new_res.setdefault(cat, {})[new] = cat_dict[name]
        return new

    if op in _RES_OPERANDS:
        cat, idx = _RES_OPERANDS[op]
        if idx < len(operands):
            operands[idx] = mapped(cat, operands[idx])
    elif op in ("scn", "SCN") and operands and isinstance(operands[-1], Name):
        operands[-1] = mapped("/Pattern", operands[-1])
    elif op in ("BDC", "DP") and len(operands) > 1 and isinstance(operands[1], Name):
        operands[1] = mapped("/Properties", operands[1])
    else:
        return ins
    return pikepdf.ContentStreamInstruction(operands, ins.operator)


def _expand(pdf, instructions, res_lookup, page_res, depth, stats, seen_stack):
    out = []
    for ins in instructions:
        if isinstance(ins, pikepdf.ContentStreamInlineImage) or str(ins.operator) != "Do" or depth >= _MAX_DEPTH:
            out.append(ins)
            continue
        name = ins.operands[0] if ins.operands else None
        xo_dict = res_lookup.get("/XObject") if res_lookup is not None else None
        form = xo_dict.get(name) if (xo_dict is not None and name is not None and name in xo_dict) else None
        if form is None or not _inlinable(form) or form.objgen in seen_stack:
            out.append(ins)
            continue
        try:
            inner = list(pikepdf.parse_content_stream(form))
        except Exception:
            out.append(ins)
            continue
        if len(inner) > _MAX_OPS:
            out.append(ins)
            continue
        form_res = form.get("/Resources")
        tag = f"{form.objgen[0]}"
        new_res = {}
        renamed = []
        ok = True
        for x in inner:
            r = _rename_operands(x, form_res, page_res, tag, new_res)
            if r is None:
                ok = False
                break
            renamed.append(r)
        if not ok:
            out.append(ins)
            continue
        # resources of the inlined form become page resources (unique names)
        for cat, entries in new_res.items():
            if cat not in page_res:
                page_res[cat] = Dictionary()
            for k, v in entries.items():
                if k not in page_res[cat]:
                    page_res[cat][k] = v
        # nested forms: names were renamed into the page's namespace
        nested = _expand(pdf, renamed, page_res, page_res, depth + 1, stats, seen_stack | {form.objgen})
        m = form.get("/Matrix")
        bbox = form.get("/BBox")
        out.append(pikepdf.ContentStreamInstruction([], Operator("q")))
        if m is not None and [float(v) for v in m] != [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]:
            out.append(pikepdf.ContentStreamInstruction([float(v) for v in m], Operator("cm")))
        if bbox is not None:
            b = [float(v) for v in bbox]
            x0, y0, x1, y1 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
            out.append(pikepdf.ContentStreamInstruction([x0, y0, x1 - x0, y1 - y0], Operator("re")))
            out.append(pikepdf.ContentStreamInstruction([], Operator("W")))
            out.append(pikepdf.ContentStreamInstruction([], Operator("n")))
        out.extend(nested)
        out.append(pikepdf.ContentStreamInstruction([], Operator("Q")))
        stats["inlined"] += 1
    return out


def inline_forms(pdf, page_obj, instructions):
    """Returns the page instructions with its Form XObjects inlined (see module
    doc). The page's /Resources dictionary receives the forms' resources."""
    page_dict = page_obj.obj if hasattr(page_obj, "obj") else page_obj
    res = page_dict.get("/Resources")
    if res is None:
        return instructions, 0
    xo = res.get("/XObject")
    if xo is None or not any(str(xo[k].get("/Subtype")) == "/Form" for k in xo.keys()):
        return instructions, 0
    # BT ... ET never contains Do; inline only at the page's top level and below
    stats = {"inlined": 0}
    out = _expand(pdf, list(instructions), res, res, 0, stats, frozenset())
    return out, stats["inlined"]
