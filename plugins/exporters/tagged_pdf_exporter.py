# GUI/plugins/exporters/tagged_pdf_exporter.py
import os
import shutil
import fitz
import pikepdf
from pikepdf import Name, Dictionary
from engine.plugin_manager import BasePlugin
from engine.document_context import DocumentContext

from export.semantic_validator import SemanticValidator
from export.semantic_normalizer import SemanticNormalizer
from export.accessibility_mapper import AccessibilityMapper

from renderer.page_renderer import PageRenderer
from renderer.struct_tree_builder import StructTreeBuilder
from renderer.parent_tree_builder import ParentTreeBuilder
from renderer.struct_parent_allocator import StructParentAllocator

from metadata.metadata_resolver import MetadataResolver
from metadata.metadata_validator import MetadataValidator
from metadata.metadata_writer import MetadataWriter

def _with_engine_defaults(config):
    """Language / PDF/UA settings from config/engine.json when the caller's config
    does not carry them (fast exports and live updates pass a reduced config;
    without these the XMP lost its PDF/UA identifier)."""
    cfg = dict(config or {})
    if cfg.get("language") and cfg.get("pdfua"):
        return cfg
    try:
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config", "engine.json")
        with open(path, "r", encoding="utf-8") as f:
            eng = json.load(f)
    except Exception:
        eng = {"language": "en-US", "pdfua": {"part": 1, "display_doc_title": True}}
    cfg.setdefault("language", eng.get("language", "en-US"))
    pdfua = dict(eng.get("pdfua") or {"part": 1, "display_doc_title": True})
    pdfua.update(cfg.get("pdfua") or {})
    cfg["pdfua"] = pdfua
    return cfg


def repair_optional_content(pdf):
    """/OCProperties without a default configuration (/D) is invalid (ISO 32000
    8.11.4.2) - MuPDF reports 'No default Layer config' - and PDF/UA requires a
    /Name in every optional-content configuration (Matterhorn 20-001/20-002) and
    no /AS entry (20-003). Visibility is unchanged: the default state is ON."""
    ocp = pdf.Root.get("/OCProperties")
    if ocp is None or not isinstance(ocp, pikepdf.Dictionary):
        return False
    changed = False
    d = ocp.get("/D")
    if not isinstance(d, pikepdf.Dictionary):
        ocp.D = pikepdf.Dictionary(Name=pikepdf.String("Default"))
        changed = True
    configs = [ocp.D] + [c for c in (ocp.get("/Configs") or []) if isinstance(c, pikepdf.Dictionary)]
    for i, cfg in enumerate(configs):
        name = cfg.get("/Name")
        if name is None or not str(name).strip():
            cfg.Name = pikepdf.String("Default" if i == 0 else f"Configuration {i}")
            changed = True
        if "/AS" in cfg:
            del cfg["/AS"]
            changed = True
    if "/OCGs" not in ocp:
        ocp.OCGs = pikepdf.Array()
        changed = True
    return changed


def ensure_font_resource(page_obj, font_indirect, font_alias):
    resources = page_obj.get("/Resources", None)
    if resources is None:
        page_obj.Resources = Dictionary()
        resources = page_obj.Resources
    if "/Font" not in resources:
        resources.Font = Dictionary()
    resources.Font[Name("/" + font_alias)] = font_indirect

class TaggedPdfExporter(BasePlugin):
    category = "exporters"
    name = "TaggedPdfExporter"
    
    def _graph_to_legacy_json(self, graph):
        """Converts internal DocumentGraph namespaces into the nested legacy schema."""
        legacy_doc = {"pages": []}
        for page_num in sorted(graph.pages.keys()):
            page = graph.pages[page_num]
            legacy_page = {"page_number": page_num, "elements": []}
            
            top_level = [n for n in page.nodes if not n.relations.get("parent")]
            
            def map_node(n):
                children = n.relations.get("children", [])
                
                # Derive reading order: from node, or if container, from first child
                n_meta = getattr(n, "metadata", {}) or {}
                ro = n.semantic.get("reading_order") or n_meta.get("reading_order")
                if ro is None and children:
                    first_child = graph.get_node(children[0])
                    if first_child:
                        fc_meta = getattr(first_child, "metadata", {}) or {}
                        ro = first_child.semantic.get("reading_order") or fc_meta.get("reading_order") or first_child.id
                if ro is None:
                    ro = n.id if n.id is not None else 999999

                legacy_el = {
                    "id": n.id,
                    "uuid": n.uuid,
                    "type": n.semantic.get("type"),
                    "content": n.text,
                    "bounding_box": n.geometry.get("bbox", []),
                    "reading_order": ro,
                    "analysis": getattr(n, "analysis", None)
                }
                for k, v in n.semantic.items():
                    if k not in ("type",):
                        legacy_el[k] = v
                if ro is not None:
                    legacy_el["reading_order"] = ro
                    
                if n.semantic.get("annotation"):
                    legacy_el["annotation"] = n.semantic["annotation"]
                    
                if children:
                    kids = []
                    for child_uuid in children:
                        child_node = graph.get_node(child_uuid)
                        if child_node:
                            kids.append(map_node(child_node))
                    legacy_el["kids"] = kids
                return legacy_el
                
            for n in top_level:
                legacy_page["elements"].append(map_node(n))
            legacy_doc["pages"].append(legacy_page)
        return legacy_doc

    def run(self, context: DocumentContext, data=None):
        doc_json = self._graph_to_legacy_json(context.graph)
        
        validator = SemanticValidator()
        if not validator.validate(doc_json):
            context.metadata.setdefault("warnings", []).extend(validator.errors)
        context.metadata.setdefault("warnings", []).extend(validator.warnings)
            
        normalizer = SemanticNormalizer()
        doc_json = normalizer.normalize(doc_json)

        # Enforce PDF/UA nesting: L > LI > (Lbl, LBody > [.., L]) and
        # TOC > TOCI > (Reference > Link, TOC). Fixes LI-in-LI / TOCI-in-TOCI.
        from export.structure_enforcer import StructureEnforcer, attach_toc_targets
        enforcer = StructureEnforcer()
        doc_json = enforcer.enforce(doc_json)
        # Every document with text has an H1 (before TOC targets are matched)
        from export.structure_enforcer import ensure_h1
        _h1 = ensure_h1(doc_json)
        if _h1:
            print(f"[StructureEnforcer] {_h1}")
        # TOC entries: no Lbl inside TOC/TOCI; the entry number goes into the Link
        # (when the link covers it) or before it as Reference text. Scanned PDFs get
        # their entry links re-matched to the PDF's own annotations by position.
        _dt0 = (self.config.get("document_type") or (context.profile.get("document_type", "") if context.profile else ""))
        try:
            from export.toc_entries import finalize_toc_entries
            _src_pdf = context.metadata.get("pdf_path")
            if not _src_pdf:
                _wp = context.metadata.get("workspace_path", "")
                for _d in [os.path.join(_wp, "input"), _wp]:
                    if _wp and os.path.isdir(_d):
                        _cand = [f for f in os.listdir(_d) if f.lower().endswith(".pdf")]
                        if _cand:
                            _src_pdf = os.path.join(_d, _cand[0])
                            break
            _toc_stats = finalize_toc_entries(doc_json, _src_pdf, _dt0)
            if any(_toc_stats.values()):
                print(f"[StructureEnforcer] TOC entries: {_toc_stats}")
        except Exception as _e:
            print(f"[StructureEnforcer] TOC entry pass failed: {_e}")
        n_targets = attach_toc_targets(doc_json)

        # Digital/Hybrid: links come from the PDF's own /Link annotations and are
        # tagged geometrically by DigitalStreamTagger (Link > OBJR + text). Drop the
        # graph's annotation carriers so no annotation gets a second OBJR.
        _dt = (self.config.get("document_type") or (context.profile.get("document_type", "") if context.profile else "")).upper()
        if "DIGITAL" in _dt or "HYBRID" in _dt:
            from export.structure_enforcer import strip_annotation_carriers
            n_stripped = strip_annotation_carriers(doc_json)
            print(f"[StructureEnforcer] annotation carrier nodes handed to the tagger: {n_stripped}")
        if enforcer.changes:
            from collections import Counter as _C
            for msg, cnt in _C(enforcer.changes).items():
                print(f"[StructureEnforcer] {msg} (x{cnt})")
        print(f"[StructureEnforcer] TOC entries linked to headings: {n_targets}")
        
        workspace_path = context.metadata.get("workspace_path", "")
        input_pdf = context.metadata.get("pdf_path")
        
        if not input_pdf and workspace_path:
            for search_dir in [os.path.join(workspace_path, "input"), workspace_path]:
                if os.path.exists(search_dir):
                    for f in os.listdir(search_dir):
                        if f.lower().endswith(".pdf"):
                            input_pdf = os.path.join(search_dir, f)
                            break
                    if input_pdf:
                        break
                        
        if not input_pdf:
            print("[TaggedPdfExporter] No input PDF found to tag.")
            return
            
        # 1. Resolve and Validate Metadata
        # print("[TaggedPdfExporter] Resolving document metadata...")
        metadata = MetadataResolver.resolve(
            document_data=doc_json,
            config=_with_engine_defaults(self.config),
            project_metadata=self.config.get("project_metadata")
        )
        
        is_valid, metadata_errors = MetadataValidator.validate(metadata)
        if not is_valid:
            print("[TaggedPdfExporter] Metadata Validation Failed. Cannot export PDF/UA:")
            for err in metadata_errors:
                print(f"  - {err}")
            context.metadata.setdefault("errors", []).extend(metadata_errors)
            # return
            
        # print("[TaggedPdfExporter] Metadata validation passed.")
            
        output_pdf = context.metadata.get("output_pdf")
        if not output_pdf:
            workspace_name = os.path.basename(workspace_path)
            output_pdf = os.path.join(workspace_path, "output", f"{workspace_name}_tagged.pdf")
        os.makedirs(os.path.dirname(output_pdf), exist_ok=True)
        context.metadata["output_pdf"] = output_pdf
        print(f"[TaggedPdfExporter] Exporting Tagged PDF to {output_pdf}")
        # print("[TaggedPdfExporter] Geometry setup...")
        fitz_doc = fitz.open(input_pdf)
        
        # Load native font map if DIGITAL
        doc_type = self.config.get("document_type") or (context.profile.get("document_type", "SCANNED") if context.profile else "Scanned PDF")
        is_digital = "DIGITAL" in doc_type.upper() or "HYBRID" in doc_type.upper()
        
        page_dims = {}
        # True CropBox lower-left corner in PDF user space (bottom-up). PyMuPDF's
        # page.cropbox is expressed top-down, so its y0 is NOT the PDF CropBox y0;
        # using it shifted every box on pages whose CropBox != MediaBox.
        # One JSON -> PDF conversion per page (CropBox anywhere, MediaBox origin,
        # inherited boxes, /Rotate). See renderer/page_geometry.py.
        from renderer.page_geometry import PageFrame
        _frames = {}
        try:
            with pikepdf.Pdf.open(input_pdf) as _src:
                for _i, _pg in enumerate(_src.pages):
                    _frames[_i + 1] = PageFrame(_pg)
        except Exception as _e:
            print(f"[TaggedPdfExporter] could not read page boxes: {_e}")
        for page_data in doc_json.get("pages", []):
            page_num = page_data.get("page_number")
            _frame = _frames.get(page_num)
            if page_num and page_num <= len(fitz_doc):
                fitz_page = fitz_doc[page_num - 1]
                page_rect = fitz_page.rect
                page_dims[page_num] = (page_rect.width, page_rect.height)
                
            def prep_geoms(els):
                for el in els:
                    bbox = el.get("bounding_box")
                    if bbox and len(bbox) == 4:
                        x0, y0, x1, y1 = bbox
                        
                        # JSON (displayed page, CropBox-local) -> PDF user space
                        if _frame is not None:
                            gx0, gy0, gx1, gy1 = _frame.json_bbox_to_pdf(bbox)
                        else:
                            gx0, gy0, gx1, gy1 = x0, y0, x1, y1
                        
                        el["_geom"] = {
                            "left": gx0, "bottom": gy0, "right": gx1, "top": gy1,
                            "width": gx1 - gx0, "height": gy1 - gy0,
                        }
                    if el.get("kids"):
                        prep_geoms(el["kids"])
                        
            prep_geoms(page_data.get("elements", []))
            
        # print("[TaggedPdfExporter] Extracting Font Metrics...")
        font = fitz.Font("helv")
        
        native_font_map = []
        if is_digital:
            map_path = os.path.join(str(workspace_path), "native_font_map.json")
            if os.path.exists(map_path):
                import json
                try:
                    with open(map_path, "r", encoding="utf-8") as f:
                        native_font_map = json.load(f)
                except:
                    pass
                    
        # print("[TaggedPdfExporter] Extracting initial stream counts...")
        page_initial_streams = {}
        for p in doc_json.get("pages", []):
            p_num = p.get("page_number")
            if p_num and p_num <= len(fitz_doc):
                page_initial_streams[p_num] = len(fitz_doc[p_num - 1].get_contents())
                
        # (Bookmark generation is now handled entirely by BookmarkBuilder later in the pipeline)
        
        pdf_bytes = fitz_doc.write()
        
        # print("[TaggedPdfExporter] Rendering with Pikepdf...")
        import io
        pdf = pikepdf.Pdf.open(io.BytesIO(pdf_bytes))
        

        
        # Apply the CIDToGIDMap patch
        for k, v in pdf.pages[0].Resources.get("/Font", {}).items():
            if v.get("/Subtype") == "/Type0":
                descendants = v.get("/DescendantFonts", [])
                if descendants:
                    df = descendants[0]
                    if df.get("/Subtype") == "/CIDFontType2":
                        if "/CIDToGIDMap" not in df:
                            df["/CIDToGIDMap"] = pikepdf.Name("/Identity")
        
        # Apply the CIDToGIDMap patch
        for k, v in pdf.pages[0].Resources.get("/Font", {}).items():
            if v.get("/Subtype") == "/Type0":
                descendants = v.get("/DescendantFonts", [])
                if descendants:
                    df = descendants[0]
                    if df.get("/Subtype") == "/CIDFontType2":
                        if "/CIDToGIDMap" not in df:
                            df["/CIDToGIDMap"] = pikepdf.Name("/Identity")

        # We still create the Helvetica fallback font for safety/scanned/hybrid mode
        pdf_font = pdf.make_indirect(Dictionary(
            Type=Name("/Font"),
            Subtype=Name("/Type1"),
            BaseFont=Name("/Helvetica"),
            Encoding=Name("/WinAnsiEncoding")
        ))
        
        # print("[TaggedPdfExporter] Initializing Tag Tree...")
        struct_tree_root, doc_elem = StructTreeBuilder.create_root(pdf)
        
        # Context-aware footnote classification
        has_any_reference = False
        referenced_notes = set()
        
        def find_references(els):
            nonlocal has_any_reference
            for el in els:
                if el.get("type") in ["footnote_reference", "hyperlink"]:
                    has_any_reference = True
                    target = el.get("target_id") or el.get("relations", {}).get("target_id")
                    if target is not None:
                        referenced_notes.add(target)
                if el.get("kids"):
                    find_references(el["kids"])
                    
        for p in doc_json.get("pages", []):
            find_references(p.get("elements", []))
            
        def custom_get_tag_fn(el_type, el):
            if el_type in ("Form", "Link", "form_field", "hyperlink", "link"):
                return "Form" if el_type in ("Form", "form_field") else "Link"
            if el_type == "footnote_note":
                return "Note"
            return AccessibilityMapper.get_tag(el_type, el)
        
        all_page_struct_elems = []
        sp_allocator = StructParentAllocator()
        
        for page_idx, page_obj in enumerate(pdf.pages):
            # print(f"[TaggedPdfExporter] Rendering page {page_idx + 1}...")
            page_num = page_idx + 1
            ensure_font_resource(page_obj, pdf_font, "helv")
            
            # PDF/UA requires Tabs to be set to /S (Structure) for pages with annotations
            page_obj.Tabs = pikepdf.Name("/S")
            
            # Allocate page StructParent
            page_sp_id = sp_allocator.allocate_page(page_idx)
            page_obj.StructParents = page_sp_id
            
            page_elements = []
            for p in doc_json.get("pages", []):
                if p.get("page_number") == page_num:
                    page_elements = p.get("elements", [])
                    break
                    
            import re
            name_to_id = {}
            images_info = fitz_doc[page_idx].get_image_info(xrefs=True)
            for el in page_elements:
                if el.get("type") == "figure" and el.get("image_path"):
                    img_path = el["image_path"]
                    match = re.search(r"img_(\d+)\.png", img_path)
                    if match:
                        xref = int(match.group(1))
                        for img in images_info:
                            if img.get("xref") == xref:
                                name_to_id[img.get("name")] = str(el.get("id"))
                                break
                                
            mcid_counter, render_results, annotation_map = PageRenderer.render_page(
                pdf=pdf, 
                page_idx=page_idx, 
                elements=page_elements, 
                font=font, 
                name_to_id=name_to_id,
                get_tag_fn=custom_get_tag_fn,
                document_type=doc_type,
                native_font_map=native_font_map,
                initial_streams=page_initial_streams.get(page_num, 1)
            )
            
            id_to_mcid = {rr.object_id: rr.mcid for rr in render_results}
            
            page_mcid_map = {}
            
            # print(f"[TaggedPdfExporter] Building Structural Tree for page {page_idx + 1}...")
            StructTreeBuilder.build_tree(
                pdf,
                page_elements, 
                doc_elem, 
                struct_tree_root, 
                page_mcid_map, 
                page_obj, 
                custom_get_tag_fn,
                id_to_mcid,
                sp_allocator=sp_allocator,
                annotation_map=annotation_map
            )
            
            # Pre-export MCID Registration & Consistency Check
            for rr in render_results:
                mcid = rr.mcid
                if isinstance(mcid, list):
                    for item in mcid:
                        if isinstance(item, dict) and item.get("type") in ("LinkGroup", "LinkObjr"):
                            ms = item.get("mcids") or []
                        else:
                            ms = [item if isinstance(item, int) else item["mcid"]]
                        for m in ms:
                          if m not in page_mcid_map:
                            raise ValueError(f"Consistency Error: MCID {m} was emitted on page {page_idx+1} for object {rr.object_id} but is missing from ParentTree page_mcid_map!")
                else:
                    if mcid not in page_mcid_map:
                        raise ValueError(f"Consistency Error: MCID {mcid} was emitted on page {page_idx+1} for object {rr.object_id} but is missing from ParentTree page_mcid_map!")
                        
            # print(f"[DEBUG] Page {page_idx+1} page_mcid_map keys: {sorted(page_mcid_map.keys())}")
            # if 0 in page_mcid_map:
            #     print(f"[DEBUG] Page {page_idx+1} MCID 0 mapped to: {page_mcid_map[0].get('/S')}")
            # else:
            #     print(f"[DEBUG] Page {page_idx+1} MCID 0 is missing!")
            all_page_struct_elems.append(page_mcid_map)
            
        # print("[TaggedPdfExporter] Building ParentTree...")
        ParentTreeBuilder.build(pdf, struct_tree_root, all_page_struct_elems, sp_allocator)
        
        # print("[TaggedPdfExporter] Building Document Outline/Bookmarks...")
        try:
            from renderer.bookmark_builder import BookmarkBuilder
            BookmarkBuilder.build(pdf, doc_json, list(pdf.pages))
        except Exception as e:
            print(f"[TaggedPdfExporter] Failed to build bookmarks: {e}")
        
        # Same final rules for Digital, Hybrid and Scanned: one annotation = one
        # OBJR, link Alt text from the visible text, scanned link annotations joined
        # to their text, unreadable list labels -> Lbl > Span with ActualText.
        try:
            from export.struct_postpass import fix_links, wrap_unreadable_labels
            _art_boxes = {}

            def _collect_art(els, pi):
                for _el in els or []:
                    if (_el.get("type") == "artifact" or custom_get_tag_fn(_el.get("type"), _el) == "Artifact"):
                        _g = _el.get("_geom")
                        if isinstance(_g, dict) and "left" in _g:
                            _art_boxes.setdefault(pi, []).append(
                                [float(_g["left"]), float(_g["bottom"]), float(_g["right"]), float(_g["top"])])
                    _collect_art(_el.get("kids"), pi)
            for _p in doc_json.get("pages", []):
                if _p.get("page_number"):
                    _collect_art(_p.get("elements"), int(_p["page_number"]) - 1)
            _ls = fix_links(pdf, scanned=not is_digital, artifact_boxes=_art_boxes)
            print(f"[StructPostPass] links: {_ls}")
            _nl = wrap_unreadable_labels(pdf)
            print(f"[StructPostPass] unreadable list labels wrapped in Span: {_nl}")
            from export.struct_postpass import fix_inadmissible_content
            _ia = fix_inadmissible_content(pdf)
            if _ia:
                print(f"[StructPostPass] content moved out of Table/TR/L/LI/TOC/TOCI into its cell/LBody/Reference: {_ia}")
            from export.struct_postpass import drop_empty_text_tags
            _de = drop_empty_text_tags(pdf)
            if _de.get("removed") or _de.get("h1"):
                print(f"[StructPostPass] empty heading/paragraph tags removed: {_de}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[TaggedPdfExporter] Structure post-pass failed: {e}")

        # /ID only on Note tags (1..N); everything else keeps just the private node link
        try:
            from renderer.struct_tree_builder import finalize_element_ids
            finalize_element_ids(pdf)
        except Exception as e:
            print(f"[TaggedPdfExporter] Element ID pass failed: {e}")

        # Optional content (layers): PDF/UA needs a named default configuration
        try:
            repair_optional_content(pdf)
        except Exception as e:
            print(f"[TaggedPdfExporter] Optional content repair failed: {e}")

        mark_info = Dictionary(Marked=pikepdf.Boolean(True))
        pdf.Root.MarkInfo = mark_info
        
        # print("[TaggedPdfExporter] Writing PDF Metadata...")
        MetadataWriter.write(pdf, metadata)
        
        # Repair un-embedded Digital Fonts (Base-14) AFTER all rendering is done
        try:
            from renderer.digital_font_repair import DigitalFontRepair
            # print("[TaggedPdfExporter] Running DigitalFontRepair...")
            repairer = DigitalFontRepair(pdf)
            repairer.repair_fonts()
            context.metadata["font_diagnostics"] = repairer.diagnostics
        except Exception as e:
            print(f"[TaggedPdfExporter] DigitalFontRepair failed: {e}")
            
        # print("[TaggedPdfExporter] Saving Output PDF...")
        try:
            from engine.file_lock import check_writable, safe_replace
            # A really locked output (open in Acrobat...) is reported before writing;
            # the PDF is written to a temp file and swapped in, so a failure never
            # leaves a half-written output behind.
            check_writable(output_pdf, action="run again")
            tmp_out = output_pdf + ".export.tmp.pdf"
            pdf.save(tmp_out)
            safe_replace(tmp_out, output_pdf, action="run again")
            context.metadata["output_pdf"] = output_pdf
            print(f"[TaggedPdfExporter] Finished exporting {output_pdf}")
        finally:
            if 'fitz_doc' in locals():
                fitz_doc.close()
            if 'pdf' in locals():
                pdf.close()
