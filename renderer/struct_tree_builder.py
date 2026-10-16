# GUI/renderer/struct_tree_builder.py
import pikepdf
from pikepdf import Name, Array, Dictionary


# Private key holding the JSON node id of a structure element ("node_<id>").
NODE_KEY = "/TPNodeID"
# Placeholder alternate texts from older extractor versions: never written as /Alt
GENERIC_ALT = {"embedded image", "image", "figure", "picture"}


def node_ref(elem):
    """JSON node id string ("node_<id>") of a structure element, or ''."""
    try:
        v = elem.get(NODE_KEY)
        if v is None:
            v = elem.get("/ID")
        return str(v) if v is not None else ""
    except Exception:
        return ""


def finalize_element_ids(pdf):
    """/ID only on Note elements: "1".."N" in document order, unique, plus the
    /IDTree that ISO 32000 requires when element identifiers exist. Every other
    element gets no /ID."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    notes, seen = [], set()

    def walk(node):
        stack = [node]
        while stack:
            e = stack.pop()
            if not isinstance(e, pikepdf.Dictionary):
                continue
            try:
                key = e.objgen if e.is_indirect else id(e)
            except Exception:
                key = id(e)
            if key in seen:
                continue
            seen.add(key)
            if "/S" in e and str(e.get("/Type", "/StructElem")) == "/StructElem":
                if "/ID" in e:
                    v = e.get("/ID")
                    if "/TPNodeID" not in e and str(v).startswith("node_"):
                        e[NODE_KEY] = v
                    del e["/ID"]
                if str(e.get("/S")) == "/Note":
                    notes.append(e)
            k = e.get("/K")
            kids = list(k) if isinstance(k, pikepdf.Array) else ([k] if k is not None else [])
            for x in reversed(kids):
                if isinstance(x, pikepdf.Dictionary) and str(x.get("/Type", "")) not in ("/OBJR", "/MCR"):
                    stack.append(x)

    walk(root)
    names = []
    for n, e in enumerate(notes, 1):
        e.ID = pikepdf.String(str(n))
        names.append((str(n), e))
    if names:
        names.sort(key=lambda t: t[0].encode())       # name-tree keys in byte order
        arr = pikepdf.Array()
        for k, e in names:
            arr.append(pikepdf.String(k))
            arr.append(e)
        root.IDTree = pdf.make_indirect(pikepdf.Dictionary(Names=arr))
    elif "/IDTree" in root:
        del root["/IDTree"]
    return len(notes)

class StructTreeBuilder:
    
    @staticmethod
    def create_root(pdf):
        struct_tree_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
        pdf.Root.StructTreeRoot = struct_tree_root
        
        doc_elem = pdf.make_indirect(Dictionary(
            Type=Name("/StructElem"),
            S=Name("/Document"),
            P=struct_tree_root,
            K=Array()
        ))
        struct_tree_root.K = Array([doc_elem])
        
        sect_elem = pdf.make_indirect(Dictionary(
            Type=Name("/StructElem"),
            S=Name("/Art"),
            P=doc_elem,
            K=Array()
        ))
        doc_elem.K.append(sect_elem)
        return struct_tree_root, sect_elem


    @staticmethod
    def _get_pdf_bbox(current_page, semantic_bbox, is_absolute=False):
        from pikepdf import Array
        if not semantic_bbox or not isinstance(semantic_bbox, list) or len(semantic_bbox) != 4:
            return None
            
        if is_absolute:
            try:
                return Array([float(semantic_bbox[0]), float(semantic_bbox[1]), float(semantic_bbox[2]), float(semantic_bbox[3])])
            except (ValueError, TypeError):
                return None
        
        # JSON box (displayed page, CropBox-local) -> PDF user space
        try:
            from renderer.page_geometry import PageFrame
            fr = PageFrame(current_page)
            llx, lly, urx, ury = fr.json_bbox_to_pdf(semantic_bbox)
            return Array([llx, lly, urx, ury])
        except (ValueError, TypeError, Exception):
            return None

    @staticmethod
    def _apply_bbox(pdf, elem_dict, current_page, semantic_bbox, tag_name="", is_absolute=False):
        pdf_bbox = StructTreeBuilder._get_pdf_bbox(current_page, semantic_bbox, is_absolute=is_absolute)
        if not pdf_bbox:
            if tag_name in ("TR", "TD"):
                print(f"[DEBUG] {tag_name}_BBOX_SOURCE_MISSING")
            return
            
        from pikepdf import Dictionary, Name, Array
        layout_dict = Dictionary(O=Name("/Layout"), BBox=pdf_bbox)
        
        if "/A" not in elem_dict:
            elem_dict.A = pdf.make_indirect(layout_dict)
        else:
            existing_a = elem_dict.A
            if isinstance(existing_a, Dictionary):
                if str(existing_a.get("/O", "")) == "/Layout":
                    existing_a.BBox = pdf_bbox
                else:
                    elem_dict.A = pdf.make_indirect(Array([existing_a, layout_dict]))
            elif isinstance(existing_a, Array):
                found_layout = False
                for a_dict in existing_a:
                    if isinstance(a_dict, Dictionary) and str(a_dict.get("/O", "")) == "/Layout":
                        a_dict.BBox = pdf_bbox
                        found_layout = True
                        break
                if not found_layout:
                    existing_a.append(layout_dict)

    @staticmethod
    def _apply_table_cell_attrs(pdf, elem_dict, el, tag):
        """
        /A << /O /Table /Scope /Column|/Row|/Both /ColSpan n /RowSpan n >>
        (what Acrobat's Table Cell Properties dialog edits). Scope only on TH;
        spans only when > 1 (1 is the default).
        """
        attrs = Dictionary(O=Name("/Table"))
        if tag == "TH":
            scope = str(el.get("scope") or "Column")
            if scope not in ("Column", "Row", "Both"):
                scope = "Column"
            attrs.Scope = Name("/" + scope)
        try:
            rs = int(el.get("row_span") or 1)
            cs = int(el.get("col_span") or 1)
        except (TypeError, ValueError):
            rs = cs = 1
        if rs > 1:
            attrs.RowSpan = rs
        if cs > 1:
            attrs.ColSpan = cs
        if len(attrs.keys()) == 1:
            return  # plain 1x1 TD: nothing to say
        existing = elem_dict.get("/A")
        if existing is None:
            elem_dict.A = pdf.make_indirect(attrs)
        elif isinstance(existing, Dictionary):
            if str(existing.get("/O", "")) == "/Table":
                for k, v in attrs.items():
                    existing[k] = v
            else:
                elem_dict.A = pdf.make_indirect(Array([existing, attrs]))
        elif isinstance(existing, Array):
            for a in existing:
                if isinstance(a, Dictionary) and str(a.get("/O", "")) == "/Table":
                    for k, v in attrs.items():
                        a[k] = v
                    return
            existing.append(attrs)

    @staticmethod
    def _min_mcid(item, page_obj=None):
        """Smallest MCID inside an int / StructElem subtree (None if none)."""
        if isinstance(item, int):
            return item
        try:
            if isinstance(item, pikepdf.Object) and not isinstance(item, (Dictionary, Array)):
                return int(item)
        except Exception:
            return None
        if isinstance(item, Dictionary):
            if item.get("/Type") == Name("/OBJR"):
                return None
            k = item.get("/K")
            kids = list(k) if isinstance(k, Array) else ([k] if k is not None else [])
            vals = [StructTreeBuilder._min_mcid(x) for x in kids]
            vals = [v for v in vals if v is not None]
            return min(vals) if vals else None
        return None

    @staticmethod
    def _order_kids(elem_dict):
        """Stable-sorts /K by content-stream order when it mixes MCIDs and child elements."""
        k = elem_dict.get("/K")
        if not isinstance(k, Array) or len(k) < 2:
            return
        items = list(k)
        has_int = any(not isinstance(x, Dictionary) for x in items)
        has_dict = any(isinstance(x, Dictionary) and x.get("/Type") != Name("/OBJR") for x in items)
        if not (has_int and has_dict):
            return
        keys = []
        last = -1
        for x in items:
            v = StructTreeBuilder._min_mcid(x)
            if v is None:
                v = last  # OBJR / empty elements stay next to their predecessor
            keys.append(v)
            last = v
        order = sorted(range(len(items)), key=lambda i: (keys[i], i))
        if order != list(range(len(items))):
            elem_dict.K = Array([items[i] for i in order])

    @staticmethod
    def _register_annot(pdf, annot, link_dict, alt, current_page, sp_allocator):
        annot.Contents = pikepdf.String(alt)
        if "/StructParent" in annot:
            del annot["/StructParent"]
        sp_id = sp_allocator.allocate_annotation(f"link_{annot.objgen}")
        annot.StructParent = sp_id
        if not hasattr(pdf, "_annot_struct_map"):
            pdf._annot_struct_map = {}
        pdf._annot_struct_map[sp_id] = link_dict
        return pdf.make_indirect(Dictionary(Type=Name("/OBJR"), Obj=annot, Pg=current_page.obj))

    @staticmethod
    def _add_link_group(pdf, item, parent_dict, current_page, page_mcid_map, sp_allocator):
        """
        [Reference >] Link > [OBJR..., MCIDs...]; the annotation /Contents carries the link text.
        Internal footnote markers (16, *, †) are wrapped in Reference.
        """
        alt = item.get("alt") or "Link"
        holder = parent_dict
        if not item.get("annots"):
            # a group that lost its annotation to another group: never an empty or
            # annotation-less Link; its text (if any) stays with the parent
            for m in item.get("mcids") or []:
                parent_dict.K.append(m)
                page_mcid_map[m] = parent_dict
            return
        if item.get("reference"):
            ref = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/Reference"),
                                               P=parent_dict, Pg=current_page.obj))
            ref.K = Array()
            parent_dict.K.append(ref)
            holder = ref
        link = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/Link"),
                                            P=holder, Pg=current_page.obj))
        k = Array()
        for annot in item.get("annots") or []:
            k.append(StructTreeBuilder._register_annot(pdf, annot, link, alt, current_page, sp_allocator))
        for m in item.get("mcids") or []:
            k.append(m)
            page_mcid_map[m] = link
        link.K = k
        holder.K.append(link)

    @staticmethod
    def _attach_objrs(pdf, link_dict, item, current_page, sp_allocator):
        alt = item.get("alt") or "Link"
        if "/Alt" in link_dict:
            del link_dict["/Alt"]
        if "/K" not in link_dict:
            link_dict.K = Array()
        objrs = [StructTreeBuilder._register_annot(pdf, a, link_dict, alt, current_page, sp_allocator)
                 for a in item.get("annots") or []]
        existing = list(link_dict.K)
        link_dict.K = Array(objrs + existing)

    @staticmethod
    def _add_toc_link(pdf, el, ref_dict, current_page, page_mcid_map, sp_allocator):
        """Creates a GoTo Link annotation over the TOC entry and a Link StructElem
        (Reference > Link > [MCIDs..., OBJR]). Visual appearance is unchanged
        (no border)."""
        try:
            target = el["_toc_target"]
            t_idx = int(target["page_number"]) - 1
            if t_idx < 0 or t_idx >= len(pdf.pages):
                return
            t_page = pdf.pages[t_idx]
            from renderer.page_geometry import PageFrame
            t_frame = PageFrame(t_page)
            tb = target.get("bounding_box") or []
            if len(tb) == 4:
                # top-left of the heading as displayed, in PDF user space
                px, py = t_frame.view_point_to_pdf(float(tb[0]), float(tb[3]) + 6)
                dest = Array([t_page.obj, Name("/XYZ"), px, py, 0])
            else:
                dest = Array([t_page.obj, Name("/Fit")])

            rect = StructTreeBuilder._get_pdf_bbox(current_page, el.get("bounding_box"))
            if rect is None:
                return
            from plugins.digital.digital_stream_tagger import DigitalStreamTagger
            text = DigitalStreamTagger.clean_alt(el.get("content") or "") or "Go to section"
            annot = pdf.make_indirect(Dictionary(
                Type=Name("/Annot"),
                Subtype=Name("/Link"),
                Rect=rect,
                Border=Array([0, 0, 0]),
                F=4,
                Dest=dest,
                Contents=pikepdf.String(text),
                P=current_page.obj,
            ))
            if "/Annots" not in current_page.obj:
                current_page.obj.Annots = pdf.make_indirect(Array())
            current_page.obj.Annots.append(annot)

            link_dict = pdf.make_indirect(Dictionary(
                Type=Name("/StructElem"),
                S=Name("/Link"),
                P=ref_dict,
                Pg=current_page.obj,
            ))
            link_k = Array()
            rest = Array()
            for item in list(ref_dict.get("/K", Array())):
                if isinstance(item, Dictionary):
                    rest.append(item)
                else:
                    link_k.append(item)
                    page_mcid_map[int(item)] = link_dict
            objr = pdf.make_indirect(Dictionary(Type=Name("/OBJR"), Obj=annot, Pg=current_page.obj))
            link_k.insert(0, objr)
            link_dict.K = link_k
            rest.insert(0, link_dict)
            ref_dict.K = rest

            sp_id = sp_allocator.allocate_annotation(f"toc_link_{el.get('id')}")
            annot.StructParent = sp_id
            if not hasattr(pdf, "_annot_struct_map"):
                pdf._annot_struct_map = {}
            pdf._annot_struct_map[sp_id] = link_dict
        except Exception as e:
            print(f"[StructTreeBuilder] TOC link for node {el.get('id')} failed: {e}")

    @staticmethod
    def build_tree(pdf, elements, parent_elem, struct_tree_root, page_mcid_map, current_page, get_tag_fn, id_to_mcid, sp_allocator=None, annotation_map=None):
        """Recursively builds the structure tree."""
        from pikepdf import Dictionary, Name, Array
        if sp_allocator is None:
            from .struct_parent_allocator import StructParentAllocator
            sp_allocator = StructParentAllocator()
        
        # 1. Pre-process elements to group contiguous list_items into a 'list' wrapper
        grouped_elements = []
        i = 0
        
        # Check if we are already inside a list to prevent infinite recursion
        in_list = False
        if parent_elem is not None and hasattr(parent_elem, 'keys') and "/S" in parent_elem:
            if str(parent_elem.get("/S")) in ("/L", "/LBody"):
                in_list = True
                
        while i < len(elements):
            el = elements[i]
            el_type = el.get("type")
            if el_type == "list_item" and not in_list:
                list_items = [el]
                j = i + 1
                while j < len(elements) and elements[j].get("type") == "list_item":
                    list_items.append(elements[j])
                    j += 1
                grouped_elements.append({"type": "list", "kids": list_items, "bounding_box": list_items[0].get("bounding_box")})
                i = j
                continue
            
            if el_type == "table":
                kids = el.get("kids", [])
                if kids and kids[0].get("type") in ("table_header_cell", "table_data_cell"):
                    kids = sorted(kids, key=lambda x: x.get("bounding_box", [0,0,0,0])[1])
                    rows = []
                    current_row = []
                    current_y = None
                    for kid in kids:
                        y = kid.get("bounding_box", [0,0,0,0])[1]
                        if current_y is None:
                            current_y = y
                            current_row.append(kid)
                        elif abs(y - current_y) < 5:
                            current_row.append(kid)
                        else:
                            rows.append({"type": "table_row", "kids": sorted(current_row, key=lambda x: x.get("bounding_box", [0,0,0,0])[0])})
                            current_row = [kid]
                            current_y = y
                    if current_row:
                        rows.append({"type": "table_row", "kids": sorted(current_row, key=lambda x: x.get("bounding_box", [0,0,0,0])[0])})
                    el["kids"] = rows
            
            grouped_elements.append(el)
            i += 1
            
        for el in grouped_elements:
            el_type = el.get("type")
            tag = get_tag_fn(el_type, el)
            
            if tag == "Artifact":
                # Do not emit StructElem for artifacts
                continue

            if el_type in ("objr", "OBJR") or tag == "OBJR":
                # PDF Object Reference dictionary inside Link's /K.
                # NEVER emit a /StructElem /S /OBJR!
                annot_info = el.get("annotation", {})
                runtime_id = annot_info.get("runtime_annot_id") or el.get("runtime_annot_id")
                if runtime_id and parent_elem is not None:
                    try:
                        parts = runtime_id.split("_")
                        p_idx = int(parts[1])
                        a_idx = int(parts[3])
                        annot_ref = pdf.pages[p_idx].Annots[a_idx]
                        
                        already_present = False
                        if "/K" in parent_elem:
                            for k_item in parent_elem.K:
                                if isinstance(k_item, Dictionary) and k_item.get("/Type") == Name("/OBJR") and k_item.get("/Obj") == annot_ref:
                                    already_present = True
                                    break
                                    
                        if not already_present:
                            objr_dict = pdf.make_indirect(Dictionary(
                                Type=Name("/OBJR"),
                                Obj=annot_ref,
                                Pg=pdf.pages[p_idx].obj
                            ))
                            if "/K" not in parent_elem:
                                parent_elem.K = Array()
                            parent_elem.K.append(objr_dict)
                            
                            if not hasattr(pdf, "_annot_struct_map"):
                                pdf._annot_struct_map = {}
                            
                            sp_id = sp_allocator.allocate_annotation(runtime_id)
                            annot_ref.StructParent = sp_id
                            pdf._annot_struct_map[sp_id] = parent_elem
                    except Exception as e:
                        print(f"[DEBUG] Error resolving OBJR annot {runtime_id}: {e}")
                continue
                
            el_id = el.get("id")
            mcid = id_to_mcid.get(str(el_id))
            if mcid is None and str(el_id).endswith("-P"):
                mcid = id_to_mcid.get(str(el_id)[:-2])
            if mcid is None and el_id is not None:
                mcid = id_to_mcid.get(f"{el_id}-P")
            
            elem_dict = pdf.make_indirect(Dictionary(
                Type=Name("/StructElem"),
                S=Name(f"/{tag}"),
                P=parent_elem
            ))
            
            # Phase 19E: element -> JSON node link for the GUI (live PDF editing,
            # tag tree selection). Kept in a private key that viewers do not show;
            # the public /ID is written only for Note (see finalize_element_ids).
            if el_id is not None:
                elem_dict[NODE_KEY] = pikepdf.String(f"node_{el_id}")
            
            # Alt when alternate text was supplied (Figures and Formulas ONLY)
            _alt = (el.get("alt_text") or el.get("Alt") or "").strip()
            if _alt and _alt.lower() not in GENERIC_ALT and tag in ("Figure", "Formula"):
                elem_dict.Alt = pikepdf.String(_alt)

            if el.get("actual_text"):
                elem_dict.ActualText = pikepdf.String(el["actual_text"])
            elif el.get("content") and tag in ("P", "LBody", "Lbl", "H1", "H2", "H3", "H4", "H5", "H6", "TH", "TD", "Span"):
                kids = el.get("kids") or []
                has_child_struct = any(isinstance(k, dict) and (k.get("type") in ("span", "link", "hyperlink", "reference") or str(k.get("tag", "")).upper() in ("SPAN", "LINK", "REFERENCE")) for k in kids)
                if not has_child_struct:
                    from export.structure_enforcer import normalize_word_spacing
                    clean_txt = normalize_word_spacing(str(el["content"]).strip())
                    if clean_txt:
                        elem_dict.ActualText = pikepdf.String(clean_txt)
                
            # Determine block-level context based on parent tag
            parent_s = parent_elem.get("/S")
            parent_tag = str(parent_s) if parent_s else ""
            is_block_context = parent_tag in ["/Document", "/Part", "/Art", "/Sect", "/Div", "/TD", "/TH"]
                
            if tag == "Note":
                if is_block_context:
                    if "/A" not in elem_dict:
                        elem_dict.A = pdf.make_indirect(Dictionary(O=Name("/Layout")))
                    elem_dict.A.Placement = Name("/Block")
                    
            if tag == "Figure":
                has_layout = False
                layout_dict = Dictionary(O=Name("/Layout"))
                
                pdf_bbox = StructTreeBuilder._get_pdf_bbox(current_page, el.get("bounding_box"))
                if pdf_bbox:
                    layout_dict.BBox = pdf_bbox
                    has_layout = True
                        
                if is_block_context:
                    layout_dict.Placement = Name("/Block")
                    has_layout = True
                    
                if has_layout:
                    if "/A" not in elem_dict:
                        elem_dict.A = pdf.make_indirect(layout_dict)
                    else:
                        # Merge into existing Layout dict if present
                        for k, v in layout_dict.items():
                            elem_dict.A[k] = v
                    
            if tag == "Table":
                elem_dict.A = pdf.make_indirect(Dictionary(O=Name("/Table")))
                StructTreeBuilder._apply_bbox(pdf, elem_dict, current_page, el.get("bounding_box"), "Table")
                
            if tag == "Formula":
                StructTreeBuilder._apply_bbox(pdf, elem_dict, current_page, el.get("bounding_box"), "Formula")
                if is_block_context:
                    if "/A" not in elem_dict:
                        elem_dict.A = pdf.make_indirect(Dictionary(O=Name("/Layout")))
                    elem_dict.A.Placement = Name("/Block")

            if tag in ("TR", "TD", "TH", "L", "LI", "LBody", "Lbl", "TOC", "TOCI", "Reference"):
                StructTreeBuilder._apply_bbox(pdf, elem_dict, current_page, el.get("bounding_box"), tag)

            if tag in ("TH", "TD"):
                StructTreeBuilder._apply_table_cell_attrs(pdf, elem_dict, el, tag)
                
            if tag == "Span":
                if el.get("underline") or (el.get("attributes") and el["attributes"].get("Underline") in (True, "true", "True")):
                    layout_dict = Dictionary(O=Name("/Layout"), TextDecorationType=Name("/Underline"))
                    if "/A" not in elem_dict:
                        elem_dict.A = pdf.make_indirect(layout_dict)
                    else:
                        existing_a = elem_dict.A
                        if isinstance(existing_a, Dictionary):
                            existing_a.TextDecorationType = Name("/Underline")
                        elif isinstance(existing_a, Array):
                            existing_a.append(layout_dict)

            if tag == "Form":
                annot_info = el.get("annotation", {})
                if annot_info.get("rect"):
                    StructTreeBuilder._apply_bbox(pdf, elem_dict, current_page, annot_info["rect"], "Form", is_absolute=True)
                    
                # Process AcroForm OBJR tags using runtime_annot_id
                runtime_id = annot_info.get("runtime_annot_id")
                
                objr_dicts = []
                if runtime_id:
                    try:
                        parts = runtime_id.split("_")
                        p_idx = int(parts[1])
                        a_idx = int(parts[3])
                        widget_ref = pdf.pages[p_idx].Annots[a_idx]
                        
                        objr_dict = pdf.make_indirect(Dictionary(
                            Type=Name("/OBJR"),
                            Obj=widget_ref,
                            Pg=pdf.pages[p_idx].obj
                        ))
                        objr_dicts.append(objr_dict)
                        
                        # Allocate StructParent
                        sp_id = sp_allocator.allocate_annotation(runtime_id)
                        widget_ref.StructParent = sp_id
                        if not hasattr(pdf, "_annot_struct_map"):
                            pdf._annot_struct_map = {}
                        pdf._annot_struct_map[sp_id] = elem_dict
                        
                    except Exception as e:
                        print(f"[DEBUG] Error resolving Form widget {runtime_id}: {e}")
                        
                if objr_dicts:
                    if "/K" not in elem_dict:
                        elem_dict.K = Array()
                    for objr in objr_dicts:
                        elem_dict.K.append(objr)
                        
            if el.get("kids"):
                if "/K" not in elem_dict:
                    elem_dict.K = Array()
                StructTreeBuilder.build_tree(
                    pdf, el["kids"], elem_dict, struct_tree_root, 
                    page_mcid_map, current_page, get_tag_fn, id_to_mcid, sp_allocator, annotation_map
                )
                if len(elem_dict.K) == 0:
                    # if kids were all artifacts and no OBJRs, remove K array
                    del elem_dict["K"]
                    
            has_child_struct_elems = False
            if "/K" in elem_dict and isinstance(elem_dict.K, Array):
                has_child_struct_elems = any(
                    isinstance(x, Dictionary) and str(x.get("/Type", "")) == "/StructElem"
                    for x in elem_dict.K
                )

            if mcid is not None and not has_child_struct_elems:
                # Node with content
                if "/K" not in elem_dict:
                    elem_dict.K = Array()
                    
                target_dict = elem_dict
                if tag == "LI":
                    # LI content that was never split into Lbl/LBody goes into an LBody.
                    # (No empty Lbl is emitted: an empty Lbl is worse than none.)
                    lbody_dict = pdf.make_indirect(Dictionary(
                        Type=Name("/StructElem"),
                        S=Name("/LBody"),
                        P=elem_dict
                    ))
                    lbody_dict.K = Array()
                    StructTreeBuilder._apply_bbox(pdf, lbody_dict, current_page, el.get("bounding_box"), "LBody")
                    
                    lbody_dict.Pg = current_page.obj
                    elem_dict.K.append(lbody_dict)
                    target_dict = lbody_dict
                    
                if isinstance(mcid, list):
                    if len(mcid) > 1:
                        print(f"[DEBUG] StructTreeBuilder processing list of mcids: {mcid}")
                    for item in mcid:
                        if isinstance(item, int):
                            target_dict.K.append(item)
                            page_mcid_map[item] = target_dict
                        elif isinstance(item, dict) and item.get("type") == "Text":
                            target_dict.K.append(item["mcid"])
                            page_mcid_map[item["mcid"]] = target_dict
                        elif isinstance(item, dict) and item.get("type") == "LinkGroup":
                            StructTreeBuilder._add_link_group(pdf, item, target_dict, current_page, page_mcid_map, sp_allocator)
                        elif isinstance(item, dict) and item.get("type") == "LinkObjr":
                            # this element IS the Link: attach its annotation(s) + Alt
                            StructTreeBuilder._attach_objrs(pdf, elem_dict, item, current_page, sp_allocator)
                        elif isinstance(item, dict) and item.get("type") == "Link":
                            # Create a nested Link structure element
                            link_dict = pdf.make_indirect(Dictionary(
                                Type=Name("/StructElem"),
                                S=Name("/Link"),
                                P=elem_dict
                            ))
                            link_dict.K = Array()

                            annot_ref = item.get("annot_ref")
                            if annot_ref is not None:
                                objr_dict = pdf.make_indirect(Dictionary(Type=Name("/OBJR"), Obj=annot_ref, Pg=current_page.obj))
                                link_dict.K.append(objr_dict)
                                
                                if not hasattr(pdf, "_annot_struct_map"):
                                    pdf._annot_struct_map = {}
                                if "/StructParent" not in annot_ref:
                                    unique_id = f"inline_link_{id(annot_ref)}"
                                    sp_id = sp_allocator.allocate_annotation(unique_id)
                                    annot_ref.StructParent = sp_id
                                else:
                                    sp_id = int(annot_ref.StructParent)
                                pdf._annot_struct_map[sp_id] = link_dict
                                
                            link_mcid = item.get("mcid")
                            if link_mcid is not None:
                                link_dict.K.append(link_mcid)
                                page_mcid_map[link_mcid] = link_dict
                            
                            link_dict.Pg = current_page.obj
                            elem_dict.K.append(link_dict)
                else:
                    target_dict.K.append(mcid)
                    page_mcid_map[mcid] = target_dict
                
                elem_dict.Pg = current_page.obj

                # TOC entry: Reference > Link > [content, OBJR] pointing at its heading
                # (only when the PDF has no real link annotation over the entry)
                has_real_link = isinstance(mcid, list) and any(
                    isinstance(it, dict) and it.get("type") in ("LinkGroup", "LinkObjr") for it in mcid)
                if tag == "Reference" and el.get("_toc_target") and not has_real_link:
                    StructTreeBuilder._add_toc_link(pdf, el, elem_dict, current_page, page_mcid_map, sp_allocator)

                # Keep children in content order (e.g. LBody text before its nested L)
                StructTreeBuilder._order_kids(elem_dict)
            elif not el.get("kids") and tag not in ("Form", "Link"):
                # Empty leaf node
                elem_dict.Pg = current_page.obj
                
            if tag == "Link":
                annot_info = el.get("annotation", {})
                if annot_info.get("rect"):
                    StructTreeBuilder._apply_bbox(pdf, elem_dict, current_page, annot_info["rect"], "Link", is_absolute=True)
                elif el.get("bounding_box"):
                    StructTreeBuilder._apply_bbox(pdf, elem_dict, current_page, el.get("bounding_box"), "Link")
                runtime_id = annot_info.get("runtime_annot_id")

                if tag in ("Form", "Link") and runtime_id:
                    try:
                        parts = runtime_id.split("_")
                        p_idx = int(parts[1])
                        a_idx = int(parts[3])
                        annot_ref = pdf.pages[p_idx].Annots[a_idx]
                        
                        import re
                        raw_content = el.get("content", "")
                        if raw_content:
                            clean_content = re.sub(r'\.{2,}', ' ', raw_content).strip()
                            clean_content = re.sub(r'\s+', ' ', clean_content)
                            annot_ref.Contents = pikepdf.String(clean_content)
                        
                        already_present = False
                        if "/K" in elem_dict:
                            for k_item in elem_dict.K:
                                if isinstance(k_item, Dictionary) and k_item.get("/Type") == Name("/OBJR") and k_item.get("/Obj") == annot_ref:
                                    already_present = True
                                    break
                                    
                        if not already_present:
                            objr_dict = pdf.make_indirect(Dictionary(
                                Type=Name("/OBJR"),
                                Obj=annot_ref,
                                Pg=pdf.pages[p_idx].obj
                            ))
                            if "/K" not in elem_dict:
                                elem_dict.K = Array()
                            elem_dict.K.append(objr_dict)
                            
                            print(f"[DEBUG] Link tag built! ID={el_id}, K array: {elem_dict.K}")
                            
                            if not hasattr(pdf, "_annot_struct_map"):
                                pdf._annot_struct_map = {}
                            
                            sp_id = sp_allocator.allocate_annotation(runtime_id)
                            annot_ref.StructParent = sp_id
                            pdf._annot_struct_map[sp_id] = elem_dict
                    except Exception as e:
                        print(f"[DEBUG] Error resolving Link annot {runtime_id}: {e}")
                
            if tag == "Link" and str(parent_elem.get("/S", "")) in ("/Document", "/Part", "/Art", "/Sect"):
                # PDF/UA requires Link (inline) to be inside a block-level element like P
                p_dict = pdf.make_indirect(Dictionary(Type=Name("/StructElem"), S=Name("/P")))
                p_dict.K = Array([elem_dict])
                p_dict.Pg = elem_dict.get("/Pg", current_page.obj)
                elem_dict.P = p_dict
                if "/K" not in parent_elem:
                    parent_elem.K = Array()
                parent_elem.K.append(p_dict)
                p_dict.P = parent_elem
            elif tag == "TOC" and str(parent_elem.get("/S", "")) in ("/Document", "/Part", "/Art", "/Sect"):
                # Multi-Page TOC Continuation:
                # Do not generate a disconnected new <TOC> root; link continuing <TOCI> entries
                # directly under the existing primary <TOC> tree structure.
                existing_root_toc = None
                if "/K" in parent_elem:
                    for k_child in parent_elem.K:
                        if isinstance(k_child, Dictionary) and str(k_child.get("/S", "")) == "/TOC":
                            existing_root_toc = k_child
                            break
                if existing_root_toc is not None:
                    # Move all children of the newly built TOC element into existing_root_toc
                    if "/K" in elem_dict:
                        if "/K" not in existing_root_toc:
                            existing_root_toc.K = Array()
                        for toci_child in elem_dict.K:
                            if isinstance(toci_child, Dictionary):
                                toci_child.P = existing_root_toc
                            existing_root_toc.K.append(toci_child)
                else:
                    if "/K" not in parent_elem:
                        parent_elem.K = Array()
                    parent_elem.K.append(elem_dict)
                    elem_dict.P = parent_elem
            elif tag == "L" and str(parent_elem.get("/S", "")) in ("/Document", "/Part", "/Art", "/Sect"):
                # Multi-Page List Continuation:
                # Do not generate a disconnected new <L> root; link continuing <LI> items
                # directly under the existing primary <L> tree structure.
                target_l_id = str(el.get("continues_list_id") or "")
                existing_root_l = None
                if target_l_id and hasattr(pdf, "_list_struct_map"):
                    existing_root_l = pdf._list_struct_map.get(target_l_id)
                if existing_root_l is None and el.get("is_continuation") and "/K" in parent_elem:
                    for k_child in parent_elem.K:
                        if isinstance(k_child, Dictionary) and str(k_child.get("/S", "")) == "/L":
                            existing_root_l = k_child
                            break

                if existing_root_l is not None:
                    # Move all children of the newly built continuing L element into existing_root_l
                    if "/K" in elem_dict:
                        if "/K" not in existing_root_l:
                            existing_root_l.K = Array()
                        for li_child in elem_dict.K:
                            if isinstance(li_child, Dictionary):
                                li_child.P = existing_root_l
                            existing_root_l.K.append(li_child)
                    if el_id is not None:
                        if not hasattr(pdf, "_list_struct_map"):
                            pdf._list_struct_map = {}
                        pdf._list_struct_map[str(el_id)] = existing_root_l
                else:
                    if "/K" not in parent_elem:
                        parent_elem.K = Array()
                    parent_elem.K.append(elem_dict)
                    elem_dict.P = parent_elem
                    if el_id is not None:
                        if not hasattr(pdf, "_list_struct_map"):
                            pdf._list_struct_map = {}
                        pdf._list_struct_map[str(el_id)] = elem_dict
            else:
                if "/K" not in parent_elem:
                    parent_elem.K = Array()
                parent_elem.K.append(elem_dict)
                elem_dict.P = parent_elem
                if tag == "L" and el_id is not None:
                    if not hasattr(pdf, "_list_struct_map"):
                        pdf._list_struct_map = {}
                    pdf._list_struct_map[str(el_id)] = elem_dict
