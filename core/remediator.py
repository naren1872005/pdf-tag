import os
import shutil
import fitz
import pikepdf
from pikepdf import Name, Dictionary

from ocr.paddle_engine import PaddleLayoutExtractor
from export.semantic_validator import SemanticValidator
from export.semantic_normalizer import SemanticNormalizer
from export.accessibility_mapper import AccessibilityMapper
from export.structure_enforcer import StructureEnforcer, attach_toc_targets, ensure_h1, enforce_heading_hierarchy
from renderer.page_renderer import PageRenderer
from renderer.struct_tree_builder import StructTreeBuilder, finalize_element_ids
from renderer.parent_tree_builder import ParentTreeBuilder
from renderer.struct_parent_allocator import StructParentAllocator
from metadata.metadata_resolver import MetadataResolver
from metadata.metadata_validator import MetadataValidator
from metadata.metadata_writer import MetadataWriter
from renderer.page_geometry import PageFrame


def _ensure_font_resource(page_obj, font_indirect, font_alias):
    resources = page_obj.get("/Resources", None)
    if resources is None:
        page_obj.Resources = Dictionary()
        resources = page_obj.Resources
    if "/Font" not in resources:
        resources.Font = Dictionary()
    resources.Font[Name("/" + font_alias)] = font_indirect


class TaggingService:
    """
    Core accessibility tagging service that transforms an input PDF
    into a tagged PDF/UA-1 compliant document using PaddleOCR and
    the deterministic structure engine.
    """

    @classmethod
    def remediate_pdf(cls, input_pdf: str, output_pdf: str, progress_callback=None) -> dict:
        if progress_callback:
            progress_callback(1, 4, "Extracting layout & text with PaddleOCR...")

        # 1. Extraction
        doc_json = PaddleLayoutExtractor.extract_document(input_pdf, progress_callback=progress_callback)

        # Check if the PDF already contains authentic StructTree accessibility tags
        with fitz.open(input_pdf) as test_doc:
            has_existing_struct = PaddleLayoutExtractor.check_pdf_has_struct_tree(test_doc)

        if has_existing_struct:
            if progress_callback:
                progress_callback(3, 4, "Preserving authentic PDF structure & updating PDF/UA compliance...")

            # Ensure PDF/UA compliance flags & XMP on the existing tagged PDF
            with pikepdf.open(input_pdf) as pdf:
                if "/MarkInfo" not in pdf.Root:
                    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
                else:
                    pdf.Root.MarkInfo.Marked = True

                if "/ViewerPreferences" not in pdf.Root:
                    pdf.Root.ViewerPreferences = pikepdf.Dictionary(DisplayDocTitle=True)
                else:
                    pdf.Root.ViewerPreferences.DisplayDocTitle = True

                for p in pdf.pages:
                    p.Tabs = pikepdf.Name("/S")

                # Ensure all Figure elements in the existing structure tree have valid, meaningful /Alt descriptions
                root = pdf.Root.get("/StructTreeRoot")
                if root:
                    doc_fig_alts = {}
                    for p_data in doc_json.get("pages", []):
                        p_num = p_data.get("page_number")
                        for el in p_data.get("elements", []):
                            if el.get("type") == "figure" and el.get("alt_text"):
                                doc_fig_alts.setdefault(p_num, []).append(el.get("alt_text"))

                    def walk_fix_figures(node, p_idx=1):
                        k = node.get("/K")
                        kids = list(k) if isinstance(k, (pikepdf.Array, list)) else ([k] if isinstance(k, pikepdf.Dictionary) else [])
                        for child in kids:
                            if isinstance(child, pikepdf.Dictionary):
                                s_val = str(child.get("/S", ""))
                                if s_val == "/Figure" or child.get("/S") == pikepdf.Name("/Figure"):
                                    curr_alt = str(child.get("/Alt", "")).strip()
                                    if not curr_alt or curr_alt.lower() in ("none", "image", "figure", "embedded image", "picture"):
                                        alts = doc_fig_alts.get(p_idx, [])
                                        new_alt = alts.pop(0) if alts else f"Figure on Page {p_idx}"
                                        child.Alt = pikepdf.String(new_alt)
                                pg_obj = child.get("/Pg")
                                child_p_idx = p_idx
                                if pg_obj:
                                    for idx_p, p_p in enumerate(pdf.pages):
                                        if p_p.obj == pg_obj:
                                            child_p_idx = idx_p + 1
                                            break
                                walk_fix_figures(child, child_p_idx)

                    walk_fix_figures(root)

                try:
                    config = {"language": "en-US", "pdfua": {"part": 1, "display_doc_title": True}}
                    metadata = MetadataResolver.resolve(document_data=doc_json, config=config)
                    MetadataWriter.write(pdf, metadata)
                except Exception as meta_err:
                    print(f"[WARN] Metadata update notice: {meta_err}")

                pdf.save(output_pdf)

            # Generate stats from doc_json
            stats = {
                "page_count": len(doc_json.get("pages", [])),
                "elements_count": 0,
                "headings_count": 0,
                "paragraphs_count": 0,
                "figures_count": 0,
                "lists_count": 0,
                "tables_count": 0
            }

            def count_stat_elements(els):
                for el in els:
                    stats["elements_count"] += 1
                    t = str(el.get("type", "")).lower()
                    tg = str(el.get("tag", "")).upper()
                    if "heading" in t or tg.startswith("H"):
                        stats["headings_count"] += 1
                    elif "paragraph" in t or tg == "P":
                        stats["paragraphs_count"] += 1
                    elif "figure" in t or tg == "FIGURE":
                        stats["figures_count"] += 1
                    elif "list" in t or tg in ("L", "LI", "LBL", "LBODY"):
                        stats["lists_count"] += 1
                    elif "table" in t or tg in ("TABLE", "TR", "TH", "TD"):
                        stats["tables_count"] += 1
                    if el.get("kids"):
                        count_stat_elements(el["kids"])

            for p in doc_json.get("pages", []):
                count_stat_elements(p.get("elements", []))

            if progress_callback:
                progress_callback(4, 4, "PDF structure successfully preserved!")

            return {
                "success": True,
                "output_pdf": output_pdf,
                "doc_json": doc_json,
                "stats": stats
            }

        if progress_callback:
            progress_callback(2, 4, "Enforcing PDF/UA structure & heading hierarchy...")

        # 2. Validation & Normalization
        validator = SemanticValidator()
        validator.validate(doc_json)

        normalizer = SemanticNormalizer()
        doc_json = normalizer.normalize(doc_json)

        # 3. Structure Enforcement (H1 check, List nesting, Table integrity, Heading hierarchy)
        enforcer = StructureEnforcer()
        doc_json = enforcer.enforce(doc_json)
        enforce_heading_hierarchy(doc_json)
        ensure_h1(doc_json)
        attach_toc_targets(doc_json)

        if progress_callback:
            progress_callback(3, 4, "Building StructTreeRoot & marking content streams...")

        # 4. Geometry Preparation
        fitz_doc = fitz.open(input_pdf)
        page_frames = {}
        try:
            with pikepdf.Pdf.open(input_pdf) as src_pdf:
                for i, pg in enumerate(src_pdf.pages):
                    page_frames[i + 1] = PageFrame(pg)
        except Exception:
            pass

        for page_data in doc_json.get("pages", []):
            page_num = page_data.get("page_number")
            frame = page_frames.get(page_num)

            def prep_geoms(els):
                for el in els:
                    bbox = el.get("bounding_box")
                    if bbox and len(bbox) == 4:
                        if frame is not None:
                            gx0, gy0, gx1, gy1 = frame.json_bbox_to_pdf(bbox)
                        else:
                            gx0, gy0, gx1, gy1 = bbox
                        el["_geom"] = {
                            "left": gx0, "bottom": gy0, "right": gx1, "top": gy1,
                            "width": gx1 - gx0, "height": gy1 - gy0,
                        }
                    if el.get("kids"):
                        prep_geoms(el["kids"])

            prep_geoms(page_data.get("elements", []))

        # 5. Metadata Resolution
        config = {
            "language": "en-US",
            "pdfua": {"part": 1, "display_doc_title": True}
        }
        metadata = MetadataResolver.resolve(document_data=doc_json, config=config)

        # 6. Render with pikepdf
        pdf_bytes = fitz_doc.write()
        import io
        pdf = pikepdf.Pdf.open(io.BytesIO(pdf_bytes))

        # Base-14 fallback font
        pdf_font = pdf.make_indirect(Dictionary(
            Type=Name("/Font"),
            Subtype=Name("/Type1"),
            BaseFont=Name("/Helvetica"),
            Encoding=Name("/WinAnsiEncoding")
        ))

        struct_tree_root, doc_elem = StructTreeBuilder.create_root(pdf)

        def custom_get_tag_fn(el_type, el):
            if el_type in ("Form", "Link", "form_field", "hyperlink", "link"):
                return "Form" if el_type in ("Form", "form_field") else "Link"
            if el_type == "footnote_note":
                return "Note"
            return AccessibilityMapper.get_tag(el_type, el)

        # Determine whether PDF is born-digital or scanned
        total_text = sum(len(p.get_text()) for p in fitz_doc)
        is_digital = total_text > 100
        doc_type = "DIGITAL" if is_digital else "SCANNED"

        native_font_map = []
        if is_digital:
            try:
                from plugins.digital.native_text_extractor import NativeTextExtractor
                from plugins.digital.font_resource_resolver import FontResourceResolver
                from plugins.digital.font_mapper import FontMapper
                resolver = FontResourceResolver()
                for p_idx in range(len(fitz_doc)):
                    p_num = p_idx + 1
                    p_els = []
                    for p in doc_json.get("pages", []):
                        if p.get("page_number") == p_num:
                            p_els = p.get("elements", [])
                            break
                    extracted = NativeTextExtractor.extract_page(input_pdf, p_num)
                    flat_nodes = []
                    def collect_flat(els):
                        for el in els:
                            flat_nodes.append(el)
                            if el.get("kids"):
                                collect_flat(el["kids"])
                    collect_flat(p_els)
                    f_map, _ = FontMapper.map_nodes(flat_nodes, extracted, resolver)
                    native_font_map.extend(f_map)
            except Exception as e:
                print(f"[TaggingService] Font mapping notice: {e}")

        font = fitz.Font("helv")
        all_page_struct_elems = []
        sp_allocator = StructParentAllocator()

        for page_idx, page_obj in enumerate(pdf.pages):
            page_num = page_idx + 1
            _ensure_font_resource(page_obj, pdf_font, "helv")
            page_obj.Tabs = pikepdf.Name("/S")
            page_obj.StructParents = sp_allocator.allocate_page(page_idx)

            page_elements = []
            for p in doc_json.get("pages", []):
                if p.get("page_number") == page_num:
                    page_elements = p.get("elements", [])
                    break

            name_to_id = {}
            try:
                images_info = fitz_doc[page_idx].get_image_info(xrefs=True)
                for el in page_elements:
                    if el.get("type") == "figure":
                        el_bbox = el.get("bounding_box")
                        el_xref = el.get("xref")
                        for img in images_info:
                            img_name = img.get("name")
                            if not img_name:
                                continue
                            if el_xref and img.get("xref") == el_xref:
                                name_to_id[img_name] = str(el.get("id"))
                                break
                            elif el_bbox:
                                ib = img.get("bbox")
                                if ib:
                                    pdf_ib = [ib[0], fitz_doc[page_idx].rect.height - ib[3], ib[2], fitz_doc[page_idx].rect.height - ib[1]]
                                    ix = max(0, min(el_bbox[2], pdf_ib[2]) - max(el_bbox[0], pdf_ib[0]))
                                    iy = max(0, min(el_bbox[3], pdf_ib[3]) - max(el_bbox[1], pdf_ib[1]))
                                    if ix > 0 and iy > 0:
                                        name_to_id[img_name] = str(el.get("id"))
                                        break
            except Exception:
                pass

            init_streams = len(fitz_doc[page_idx].get_contents()) if page_idx < len(fitz_doc) else 1
            mcid_counter, render_results, annotation_map = PageRenderer.render_page(
                pdf=pdf,
                page_idx=page_idx,
                elements=page_elements,
                font=font,
                name_to_id=name_to_id,
                get_tag_fn=custom_get_tag_fn,
                document_type=doc_type,
                native_font_map=native_font_map,
                initial_streams=init_streams
            )

            id_to_mcid = {rr.object_id: rr.mcid for rr in render_results}
            page_mcid_map = {}

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
            all_page_struct_elems.append(page_mcid_map)

        if progress_callback:
            progress_callback(4, 4, "Finalizing ParentTree, XMP Metadata & Font embedding...")

        # 7. ParentTree & PostPass
        ParentTreeBuilder.build(pdf, struct_tree_root, all_page_struct_elems, sp_allocator)

        try:
            from export.struct_postpass import fix_links, wrap_unreadable_labels, fix_inadmissible_content, drop_empty_text_tags
            fix_links(pdf, scanned=False)
            wrap_unreadable_labels(pdf)
            fix_inadmissible_content(pdf)
            drop_empty_text_tags(pdf)
        except Exception:
            pass

        try:
            finalize_element_ids(pdf)
        except Exception:
            pass

        # Marked info
        pdf.Root.MarkInfo = Dictionary(Marked=pikepdf.Boolean(True))

        # Metadata & XMP
        MetadataWriter.write(pdf, metadata)

        # Digital font repair
        try:
            from renderer.digital_font_repair import DigitalFontRepair
            repairer = DigitalFontRepair(pdf)
            repairer.repair_fonts()
        except Exception:
            pass

        os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
        pdf.save(output_pdf)

        fitz_doc.close()
        pdf.close()

        # Generate summary stats recursively
        stats = {
            "page_count": len(doc_json.get("pages", [])),
            "elements_count": 0,
            "headings_count": 0,
            "paragraphs_count": 0,
            "figures_count": 0,
            "lists_count": 0,
            "tables_count": 0
        }

        def count_stat_elements(els):
            for el in els:
                stats["elements_count"] += 1
                t = str(el.get("type", "")).lower()
                if "heading" in t:
                    stats["headings_count"] += 1
                elif "paragraph" in t:
                    stats["paragraphs_count"] += 1
                elif "figure" in t:
                    stats["figures_count"] += 1
                elif "list" in t:
                    stats["lists_count"] += 1
                elif "table" in t:
                    stats["tables_count"] += 1
                if el.get("kids"):
                    count_stat_elements(el["kids"])

        for p in doc_json.get("pages", []):
            count_stat_elements(p.get("elements", []))

        return {
            "success": True,
            "output_pdf": output_pdf,
            "doc_json": doc_json,
            "stats": stats
        }
