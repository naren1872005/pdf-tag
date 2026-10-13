# GUI_V1/gui/struct_tag_editor.py
"""
Delete tags directly from a tagged PDF's structure tree (Tags panel -> Delete Tag).

A deleted tag disappears together with ALL of its descendant tags:
  * the StructElem subtree is detached from its parent's /K,
  * the ParentTree entries that pointed to it are cleared,
  * its marked-content wrappers (BDC /MCID ... EMC) are removed from the page
    content, so the content becomes untagged (it can be tagged again with the
    Tag Content tool) - no glyph is added or removed,
  * link / widget annotations it owned lose their /StructParent,
  * Note IDs are renumbered (1..N) when a Note was removed.

Targets are identified by their index path from StructTreeRoot (/K indexes), the
structure type, and - when present - the element's private node id (/TPNodeID).
"""
import os
import shutil

import pikepdf
from pikepdf import Name


class TagTarget(dict):
    """{'path': [int, ...], 'tag': '/P', 'node_ref': 'node_12' | None, 'label': '<P>'}
    path == [] means the StructTreeRoot itself (= every tag in the document)."""


def _kids_list(k):
    if k is None:
        return []
    if isinstance(k, pikepdf.Array):
        return list(k)
    return [k]


def _is_struct_elem(obj):
    try:
        return isinstance(obj, pikepdf.Dictionary) and "/S" in obj and \
            str(obj.get("/Type", "/StructElem")) not in ("/MCR", "/OBJR")
    except Exception:
        return False


def _same(a, b):
    try:
        if a.is_indirect and b.is_indirect:
            return a.objgen == b.objgen
    except Exception:
        pass
    return a is b


def _resolve(root, target):
    """Return the StructElem (or the root for path []), or None."""
    path = list(target.get("path") or [])
    tag = target.get("tag")
    if not path:
        return root
    node_ref = target.get("node_ref")
    if node_ref:
        found = []
        stack = list(_kids_list(root.get("/K")))
        seen = set()
        while stack:
            e = stack.pop()
            if not _is_struct_elem(e):
                continue
            key = e.objgen if e.is_indirect else id(e)
            if key in seen:
                continue
            seen.add(key)
            ref = e.get("/TPNodeID")
            if ref is not None and str(ref) == node_ref and (tag is None or str(e.S) == tag):
                found.append(e)
            stack.extend(_kids_list(e.get("/K")))
        if len(found) == 1:
            return found[0]
    cur = root
    for idx in path:
        kids = cur.get("/K")
        if isinstance(kids, pikepdf.Array):
            if idx < 0 or idx >= len(kids):
                return None
            cur = kids[idx]
        elif idx == 0 and kids is not None:
            cur = kids
        else:
            return None
        if not _is_struct_elem(cur):
            return None
    if tag is not None and str(cur.get("/S")) != tag:
        return None
    return cur


def _collect_subtree(elem, removed):
    """All StructElems in elem's subtree (objgen set) + OBJR annotations."""
    annots = []
    stack = [elem]
    while stack:
        e = stack.pop()
        if not _is_struct_elem(e):
            continue
        key = e.objgen if e.is_indirect else ("direct", id(e))
        if key in removed:
            continue
        removed.add(key)
        for kid in _kids_list(e.get("/K")):
            if isinstance(kid, pikepdf.Dictionary):
                t = str(kid.get("/Type", ""))
                if t == "/OBJR":
                    obj = kid.get("/Obj")
                    if obj is not None:
                        annots.append(obj)
                elif t != "/MCR":
                    stack.append(kid)
    return annots


def _detach(root, elem):
    parent = elem.get("/P")
    if parent is None:
        parent = root
    k = parent.get("/K")
    if isinstance(k, pikepdf.Array):
        keep = [x for x in k if not (isinstance(x, pikepdf.Dictionary) and _same(x, elem))]
        parent.K = pikepdf.Array(keep)
    elif k is not None and isinstance(k, pikepdf.Dictionary) and _same(k, elem):
        parent.K = pikepdf.Array()


def _number_tree_leaves(node, out, depth=0):
    """[(nums_array, index_of_value)] for every key/value pair of a number tree."""
    if node is None or depth > 32:
        return
    nums = node.get("/Nums")
    if isinstance(nums, pikepdf.Array):
        for i in range(0, len(nums) - 1, 2):
            out.append((nums, i + 1))
    for kid in node.get("/Kids") or []:
        _number_tree_leaves(kid, out, depth + 1)


def _unwrap_mcids(pdf, page, mcids):
    """Remove BDC/EMC pairs carrying one of `mcids` from the page content."""
    if not mcids:
        return 0
    instrs = list(pikepdf.parse_content_stream(page))
    out, stack, removed = [], [], 0
    for ins in instrs:
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            out.append(ins)
            continue
        op = str(ins.operator)
        if op == "BDC":
            props = ins.operands[1] if len(ins.operands) > 1 else None
            drop = False
            if isinstance(props, pikepdf.Dictionary) and "/MCID" in props:
                try:
                    drop = int(props.MCID) in mcids
                except Exception:
                    drop = False
            stack.append(drop)
            if drop:
                removed += 1
                continue
        elif op == "BMC":
            stack.append(False)
        elif op == "EMC":
            drop = stack.pop() if stack else False
            if drop:
                continue
        out.append(ins)
    if removed:
        page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
    return removed


def delete_tags_in_pdf(pdf, targets, renumber_notes=True):
    """Delete every target (and its descendants) inside an open pikepdf.Pdf.
    Returns dict(stats). Raises ValueError when nothing could be resolved."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        raise ValueError("This PDF has no tag tree.")

    # Resolve all targets first (paths refer to the tree before any change)
    resolved = []
    for t in targets:
        e = _resolve(root, t)
        if e is not None:
            resolved.append(e)
    if not resolved:
        raise ValueError("The selected tag was not found in the PDF (the tag tree may have changed). Reload and try again.")

    removed = set()
    annots = []
    delete_all = any(e is root or (hasattr(e, "objgen") and e.objgen == root.objgen) for e in resolved)
    if delete_all:
        for kid in _kids_list(root.get("/K")):
            if _is_struct_elem(kid):
                annots += _collect_subtree(kid, removed)
        root.K = pikepdf.Array()
    else:
        for e in resolved:
            key = e.objgen if e.is_indirect else ("direct", id(e))
            if key in removed:
                continue                    # already inside a deleted ancestor
            annots += _collect_subtree(e, removed)
            _detach(root, e)

    def _gone(obj):
        try:
            return isinstance(obj, pikepdf.Dictionary) and obj.is_indirect and obj.objgen in removed
        except Exception:
            return False

    # ParentTree: clear page entries / drop annotation entries of removed elements
    page_by_sp = {}
    for p in pdf.pages:
        sp = p.get("/StructParents")
        if sp is not None:
            page_by_sp[int(sp)] = p
    mcids_by_page = {}
    annot_keys = set()
    pt = root.get("/ParentTree")
    leaves = []
    _number_tree_leaves(pt, leaves)
    for nums, vi in leaves:
        key = int(nums[vi - 1])
        val = nums[vi]
        if isinstance(val, pikepdf.Array):
            changed = False
            new = []
            for mcid, entry in enumerate(val):
                if _gone(entry):
                    new.append(None)        # PDF null
                    if key in page_by_sp:
                        mcids_by_page.setdefault(key, set()).add(mcid)
                    changed = True
                else:
                    new.append(entry)
            if changed:
                nums[vi] = pikepdf.Array(new)
        elif _gone(val):
            annot_keys.add(key)
    # drop annotation key/value pairs
    if annot_keys:
        for nums, vi in sorted(leaves, key=lambda t: -t[1]):
            try:
                if int(nums[vi - 1]) in annot_keys:
                    del nums[vi]
                    del nums[vi - 1]
            except Exception:
                pass
    for a in annots:
        try:
            if "/StructParent" in a:
                del a["/StructParent"]
        except Exception:
            pass
    for p in pdf.pages:
        for a in p.get("/Annots") or []:
            try:
                sp = a.get("/StructParent")
                if sp is not None and int(sp) in annot_keys:
                    del a["/StructParent"]
            except Exception:
                pass

    # Content: unwrap the marked content of removed MCIDs
    unwrapped = 0
    for key, mcids in mcids_by_page.items():
        unwrapped += _unwrap_mcids(pdf, page_by_sp[key], mcids)

    # Note IDs 1..N / IDTree
    try:
        if renumber_notes:          # only TagPilot's own output follows the Note-ID rule
            from renderer.struct_tree_builder import finalize_element_ids
            finalize_element_ids(pdf)
    except Exception:
        pass
    return {"elements": len(removed), "marked_content": unwrapped, "annotations": len(annot_keys)}


def delete_tags_in_file(pdf_path, targets, renumber_notes=True):
    """Delete targets in the PDF file (atomic replace). Raises FileLockedError
    (a PermissionError) only when the file is really open in another program."""
    from engine.file_lock import check_writable, safe_replace
    check_writable(pdf_path)                     # real lock -> FileLockedError, nothing changed
    tmp = pdf_path + ".deltag.tmp.pdf"
    with pikepdf.Pdf.open(pdf_path) as pdf:
        stats = delete_tags_in_pdf(pdf, targets, renumber_notes=renumber_notes)
        pdf.save(tmp)
    safe_replace(tmp, pdf_path)                  # waits out short locks; true message otherwise
    return stats


def find_tagged_output(project_path):
    out_dir = os.path.join(project_path, "output")
    if not os.path.isdir(out_dir):
        return None
    name = os.path.basename(os.path.normpath(project_path))
    preferred = os.path.join(out_dir, f"{name}_tagged.pdf")
    if os.path.exists(preferred):
        return preferred
    for f in sorted(os.listdir(out_dir)):
        if f.lower().endswith("_tagged.pdf"):
            return os.path.join(out_dir, f)
    return None
