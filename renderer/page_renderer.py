# GUI/renderer/page_renderer.py
import pikepdf
import re
from renderer.text_fitter import TextFitter
from renderer.mcid_writer import MCIDWriter
from renderer.image_renderer import ImageRenderer

class PageRenderer:
    

    @staticmethod
    def pre_render_fitz(fitz_doc, doc_json, doc_type, native_font_map):
        import fitz
        from renderer.font_resolver import FontResolver
        from renderer.text_fitter import TextFitter
        from renderer.fallback_font_data import fallback_font_b64
        import base64

        is_digital = "DIGITAL" in doc_type.upper() or "HYBRID" in doc_type.upper()
        font_bytes = base64.b64decode(fallback_font_b64)
        font_metric = fitz.Font("helv")
        
        # mcid_mappings: page_num -> {el_id: [stream_indices]}
        mcid_mappings = {}
        page_initial_streams = {}

        for page_data in doc_json.get("pages", []):
            page_num = page_data.get("page_number")
            if not page_num or page_num > len(fitz_doc):
                continue
                
            fitz_page = fitz_doc[page_num - 1]
            
            
            page_font_runs = {}
            for fm in native_font_map:
                if fm.get("page") == page_num:
                    page_font_runs[str(fm.get("node_id"))] = fm.get("font_runs", [])
                    
            page_mappings = {}
            
            # --- 1. REDACTION PASS ---
            if is_digital:
                redacted = False
                
                # Build a quick lookup for elements on this page
                elements_by_id = {}
                def index_elements(els):
                    for e in els:
                        elements_by_id[str(e.get("id"))] = e
                        if e.get("kids"):
                            index_elements(e["kids"])
                index_elements(page_data.get("elements", []))
                    
                for el_id, runs in page_font_runs.items():
                    el = elements_by_id.get(el_id, {})
                    el_type = el.get("type") or el.get("semantic", {}).get("type") or el.get("semantic", {}).get("type")
                    is_list_label = el_type == "list_label"
                    
                    if "Zapf" in str(runs) or "Helvetica" in str(runs):
                        if is_list_label:
                            print(f"[DEBUG GLYPH] {el_id}: HAS LIST LABEL")
    @staticmethod
    def _unicode_fallback_font(pdf):
        """Type0 DejaVu Sans for text outside WinAnsi. Its /ToUnicode maps EVERY
        glyph of the font back to its character (a partial map left accented
        letters and symbols as 'characters that cannot be mapped to Unicode').
        Built once per PDF and shared by all pages."""
        cached = getattr(pdf, "_tp_funicode", None)
        if cached is not None:
            return cached
        import io, base64
        from fontTools.ttLib import TTFont
        from renderer.fallback_font_data import fallback_font_b64

        font_bytes = base64.b64decode(fallback_font_b64)
        ttf = TTFont(io.BytesIO(font_bytes))
        cmap = ttf['cmap'].getBestCmap()
        gid_to_uni = {}
        for uni, glyph_name in sorted(cmap.items()):
            if uni < 0x20 or 0xD800 <= uni <= 0xDFFF:
                continue
            try:
                gid = ttf.getGlyphID(glyph_name)
            except Exception:
                continue
            if gid and gid not in gid_to_uni:
                gid_to_uni[gid] = uni

        def utf16(u):
            return u.to_bytes(2, "big").hex().upper() if u <= 0xFFFF else chr(u).encode("utf-16-be").hex().upper()

        lines = [f"<{gid:04X}> <{utf16(u)}>" for gid, u in sorted(gid_to_uni.items())]
        blocks = []
        for i in range(0, len(lines), 100):
            chunk = lines[i:i + 100]
            blocks.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar")
        tounicode_cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                          "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                          "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
                          "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
                          + "\n".join(blocks) +
                          "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend")

        tounicode_stream = pdf.make_stream(tounicode_cmap.encode('ascii'))
        font_stream = pdf.make_stream(font_bytes)
        font_stream.Length1 = len(font_bytes)
        font_descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"),
            FontName=pikepdf.Name("/DejaVuSans"),
            Flags=32,
            FontBBox=pikepdf.Array([-1000, -1000, 3000, 3000]),
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
            FontFile2=font_stream
        )
        cid_font = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/CIDFontType2"),
            BaseFont=pikepdf.Name("/DejaVuSans"),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            FontDescriptor=pdf.make_indirect(font_descriptor),
            CIDToGIDMap=pikepdf.Name("/Identity")
        )
        type0_font = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type0"),
            BaseFont=pikepdf.Name("/DejaVuSans"),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([pdf.make_indirect(cid_font)]),
            ToUnicode=pdf.make_indirect(tounicode_stream)
        ))
        try:
            pdf._tp_funicode = type0_font
        except Exception:
            pass
        return type0_font

    @staticmethod
    def render_page(pdf, page_idx, elements, font, name_to_id=None, get_tag_fn=None, document_type="Scanned PDF", native_font_map=None, initial_streams=1):
        import re

        """
        Renders a single page, replacing the original content stream with our tagged one.
        Returns (mcid_counter, render_results)
        """
        page = pdf.pages[page_idx]
        is_digital = "DIGITAL" in document_type.upper() or "HYBRID" in document_type.upper()
        
        from renderer.font_resolver import FontResolver
        if name_to_id is None:
            name_to_id = {}
        if native_font_map is None:
            native_font_map = []
            
        from renderer.render_result import RenderResult
            
        page_obj = pdf.pages[page_idx]
        page_num = page_idx + 1
        
        # CropBox lower-left corner (PDF user space). TextFitter / MCIDWriter take
        # CropBox-LOCAL geometry (the JSON bounding_box / lines / font runs) and add
        # this offset themselves, for every document type.
        media_box = page_obj.get("/MediaBox")
        from renderer.page_geometry import PageFrame
        page_frame = PageFrame(page_obj)
        crop_offset = page_frame.crop_offset
        if page_frame.rotation and not is_digital:
            print(f"[PageRenderer] page {page_idx + 1} has /Rotate {page_frame.rotation}: "
                  f"invisible OCR text is placed by position; its reading direction follows the unrotated page")

        def local_geom(el):
            """CropBox-local geometry from the JSON bounding_box (el['_geom'] holds
            PDF user-space coordinates for the tagger and must not be offset again)."""
            bbox = el.get("bounding_box")
            if bbox and len(bbox) == 4:
                x0, y0, x1, y1 = [float(v) for v in bbox]
                return {"left": x0, "bottom": y0, "right": x1, "top": y1,
                        "width": x1 - x0, "height": y1 - y0}
            return None
            
        # Build node_id to font runs mapping for this page
        page_font_runs = {}
        for fm in native_font_map:
            if fm.get("page") == page_num:
                page_font_runs[str(fm.get("node_id"))] = fm.get("font_runs", [])
                
        # 1. Image names
        image_names = ImageRenderer.get_image_xobject_names(page_obj)
        
        # 2. Existing instructions
        try:
            original_instructions = list(pikepdf.parse_content_stream(page_obj))
        except Exception:
            original_instructions = []
            
        mcid_counter = 0
        render_results = []
        mcid_mappings_to_wrap = []
        annotation_map = {}
        
        # Only parse the background instructions (the initial streams)
        background_instructions = []
        contents = page_obj.Contents
        if isinstance(contents, pikepdf.Array):
            num_to_parse = min(initial_streams, len(contents))
            streams_to_parse = [contents[i] for i in range(num_to_parse)]
            if streams_to_parse:
                # Concatenate stream bytes so tokens/operators split across chunk boundaries remain intact
                combined_bytes = b"".join(s.read_bytes() for s in streams_to_parse)
                temp_stream = pdf.make_stream(combined_bytes)
                background_instructions = list(pikepdf.parse_content_stream(temp_stream))
        elif isinstance(contents, pikepdf.Stream) and initial_streams > 0:
            background_instructions = list(pikepdf.parse_content_stream(contents))
            
        # Pre-existing marked content (old MCIDs from the input PDF) is stripped inside
        # DigitalStreamTagger.tag_stream, which keeps /OC (optional content) sequences
        # intact so hidden layers stay hidden.
        raster_fallback_nodes = set()

        unsupported_nodes = set()
        digital_tagged_mcids = {}
        if is_digital:
            def collect_unsupported(el):
                # We now natively tag hyperlinks and inline links to prevent duplicated synthetic text
                el_type = el.get("type") or el.get("semantic", {}).get("type")
                if el_type == "unsupported_legacy_type": 
                    unsupported_nodes.add(str(el.get("id")))
                for kid in el.get("kids", []):
                    collect_unsupported(kid)
            for el in elements:
                collect_unsupported(el)
                
            artifact_nodes = set()
            def pre_scan(els):
                for el in els:
                    tag = get_tag_fn(el.get("type") or el.get("semantic", {}).get("type"), el) if get_tag_fn else "P"
                    if tag == "Artifact":
                        artifact_nodes.add(str(el.get("id")))
                    if el.get("kids"):
                        pre_scan(el["kids"])
            pre_scan(elements)
                

            node_id_to_tag = {}
            def build_node_id_to_tag(els):
                for el in els:
                    el_type = el.get("type") or el.get("semantic", {}).get("type")
                    t = get_tag_fn(el_type, el) if get_tag_fn else "P"
                    node_id_to_tag[str(el.get("id"))] = t
                    if el.get("kids"):
                        build_node_id_to_tag(el["kids"])
            build_node_id_to_tag(elements)

            # Real CropBox offset: JSON bounding_box values are CropBox-local.
            json_crop_offset = crop_offset

            # Form XObjects (logo + wordmark + copyright drawn as ONE object) are
            # inlined so each image / path / text run inside gets its own tag.
            try:
                from plugins.digital.form_inliner import inline_forms
                background_instructions, _n_inl = inline_forms(pdf, page_obj, background_instructions)
                if _n_inl:
                    print(f"[DigitalStreamTagger] page {page_num}: {_n_inl} form XObject(s) inlined for tagging")
            except Exception as e:
                print(f"[WARN] Form inlining skipped on page {page_num}: {e}")

            try:
                from plugins.digital.digital_stream_tagger import DigitalStreamTagger
                background_instructions, digital_tagged_mcids, mcid_counter = DigitalStreamTagger.tag_stream(
                    pdf, page_obj, background_instructions, page_font_runs, mcid_counter, unsupported_nodes,
                    artifact_nodes, node_id_to_tag,
                    elements=elements, crop_offset=json_crop_offset
                )
                # Digital & Hybrid: nodes with no native glyphs that sit on a raster image
                # (e.g. OCR text detected inside an embedded image) get the invisible OCR text layer,
                # ensuring full accessibility without duplicating native text.
                raster_fallback_nodes = set(getattr(DigitalStreamTagger, "raster_fallback_nodes", set()))
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[ERROR] DigitalStreamTagger failed on page {page_num}: {e}")
        
        bad_font_aliases_helv = set()
        bad_font_aliases_zapf = set()
        
        if "/Resources" in page_obj and "/Font" in page_obj.Resources:
            for alias, font_dict in page_obj.Resources.Font.items():
                base_font = str(font_dict.get("/BaseFont", ""))
                if "Helvetica" in base_font:
                    bad_font_aliases_helv.add(str(alias))
                if "ZapfDingbats" in base_font:
                    bad_font_aliases_zapf.add(str(alias))

        # 3. Wrap original content
        wrapped_instructions, mcid_counter, bg_results = ImageRenderer.wrap_original_content(
            background_instructions, image_names, page_num, name_to_id, mcid_counter,
            bad_font_aliases_helv=bad_font_aliases_helv,
            bad_font_aliases_zapf=bad_font_aliases_zapf,
            is_digital=is_digital
        )
        render_results.extend(bg_results)
        bg_processed_ids = {str(rr.object_id) for rr in bg_results}
        
        # 4. Generate tagged text instructions
        text_instructions = []
        
        def process_element(el):
            nonlocal mcid_counter
            
            el_type = el.get("type") or el.get("semantic", {}).get("type")
            tag_name = get_tag_fn(el_type, el) if get_tag_fn else "P"
            
            if el_type in ("objr", "OBJR", "object_reference") or tag_name in ("OBJR", "object_reference"):
                # PDF Object References (like Link / Form annotations) do not render text
                return

            if el_type not in ("table", "table_row", "table_header_cell", "table_data_cell") and el.get("kids"):
                has_span_or_text_kids = any(
                    k.get("type") in ("span", "Span", "paragraph", "reference", "link", "hyperlink") or
                    (get_tag_fn and get_tag_fn(k.get("type"), k) in ("Span", "P", "Link", "Reference"))
                    for k in el["kids"]
                )
                if has_span_or_text_kids:
                    for kid in el["kids"]:
                        process_element(kid)
                    return

            if el_type in ("table", "table_row", "table_header_cell", "table_data_cell", "list", "list_item", "document", "toc", "TOC", "toci", "TOCI", "reference", "Reference") or tag_name in ("TOC", "TOCI", "Reference"):
                # These are structural containers
                kids = el.get("kids")
                if kids:
                    # If it has structural kids (like P, L, Figure, Link, Reference), process them and skip rendering our own text
                    has_renderable_kids = any(k.get("type") not in ("form", "object_reference", "objr", "OBJR") for k in kids)
                    # TOC Reference text around its Link (entry number before it,
                    # leader dots / page number after it): own marked content of the
                    # Reference (scanned flow; digital tags the real glyphs).
                    lead = el.get("lead_text") if not is_digital else None
                    tail = el.get("tail_text") if not is_digital else None
                    if lead and el.get("lead_bbox"):
                        process_element({"id": el.get("id"), "type": "reference", "content": lead,
                                         "bounding_box": el.get("lead_bbox"), "page_number": el.get("page_number")})
                    for kid in kids:
                        process_element(kid)
                    if tail and el.get("tail_bbox"):
                        process_element({"id": el.get("id"), "type": "reference", "content": tail,
                                         "bounding_box": el.get("tail_bbox"), "page_number": el.get("page_number")})
                    if has_renderable_kids:
                        return
                # If it doesn't have renderable kids, it might be a leaf container (like an unlinked Reference or TD with text). 
                # Let it fall through to be rendered!
                
            if el_type == "figure":
                is_tagged_natively = (str(el.get("id")) in bg_processed_ids or 
                                      (is_digital and str(el.get("id")) in digital_tagged_mcids))
                if not is_tagged_natively:
                    geom = local_geom(el)
                    if geom:
                        raw_str = MCIDWriter.build_invisible_rect_stream(geom, mcid_counter, tag_name, crop_offset)
                        parsed = pikepdf.parse_content_stream(
                            pikepdf.Stream(pdf, raw_str.encode("cp1252", errors="replace"))
                        )
                        text_instructions.extend(parsed)
                        
                        if tag_name != "Artifact":
                            render_results.append(RenderResult(
                                object_id=str(el.get("id")),
                                page=page_num,
                                mcid=mcid_counter
                            ))
                            mcid_counter += 1
                
                if el.get("kids"):
                    for kid in el["kids"]:
                        process_element(kid)
                return
                
            content = el.get("content") or el.get("text") or ""
            if not content:
                if el.get("kids"):
                    for kid in el["kids"]:
                        process_element(kid)
                return
                
            target_url = None
            if el_type == "hyperlink":
                target_url = el.get("target_url") or el.get("url") or el.get("attributes", {}).get("target_url")
                if not target_url:
                    c = content.strip()
                    if re.match(r'^(https?://|www\.)[^\s/$.?#].[^\s]*$', c, re.IGNORECASE):
                        if c.lower().startswith('www.'):
                            target_url = "https://" + c
                        else:
                            target_url = c
                if not target_url:
                    print(f"[WARNING] Unresolved hyperlink target for content: '{content}'")
                
            el_id = str(el.get("id", mcid_counter))
            
            if is_digital and el_id in digital_tagged_mcids:
                mcids_list = digital_tagged_mcids[el_id]
                render_results.append(RenderResult(
                    object_id=el_id,
                    page=page_num,
                    mcid=mcids_list
                ))
                
                # --- NATIVE ANNOTATION GENERATION ---
                # Digital/Hybrid: links are tagged by DigitalStreamTagger from the PDF's
                # own /Link annotations (one Link + OBJR per link). Creating annotations
                # here as well produced duplicate objects, so it is disabled.
                # 1. Full Hyperlinks
                if el_type == "hyperlink" and target_url and not is_digital:
                    geom = el.get("bounding_box")
                    if geom:
                        if isinstance(geom, list) and len(geom) == 4:
                            x0, y0, x1, y1 = geom
                        else:
                            x0, y0, x1, y1 = geom["left"], geom["bottom"], geom["left"] + geom["width"], geom["bottom"] + geom["height"]
                            
                        actual_rect = [x0 + crop_offset[0], y0 + crop_offset[1], 
                                       x1 + crop_offset[0], y1 + crop_offset[1]]
                        
                        clean_content = re.sub(r'\.{2,}', ' ', content).strip() if content else ""
                        clean_content = re.sub(r'\s+', ' ', clean_content)
                        annot_dict = pdf.make_indirect(pikepdf.Dictionary(
                            Type=pikepdf.Name("/Annot"),
                            Subtype=pikepdf.Name("/Link"),
                            Rect=pikepdf.Array(actual_rect),
                            Border=pikepdf.Array([0, 0, 0]),
                            P=page_obj.obj,
                            Contents=pikepdf.String(clean_content if clean_content else target_url),
                            A=pikepdf.Dictionary(
                                S=pikepdf.Name("/URI"),
                                URI=pikepdf.String(target_url)
                            )
                        ))
                        if "/Annots" not in page_obj:
                            page_obj.Annots = pdf.make_indirect(pikepdf.Array())
                        page_obj.Annots.append(annot_dict)
                        annotation_map[el_id] = annot_dict
                        
                # 2. Inline Links
                inline_links = (el.get("analysis") or {}).get("inline_links", [])
                font_runs = page_font_runs.get(el_id, [])
                if inline_links and font_runs and not is_digital:
                    annot_rects = {}
                    cx, cy = crop_offset
                    for run in font_runs:
                        text = run.get("text", "")
                        clean_text = text.replace(" ", "").replace("\n", "")
                        matched_link = None
                        for link in inline_links:
                            clean_link_text = link.get("text", "").replace(" ", "").replace("\n", "")
                            if clean_link_text and clean_link_text in clean_text:
                                matched_link = link
                                break
                        if matched_link:
                            if matched_link["text"] not in annot_rects:
                                annot_rects[matched_link["text"]] = []
                            bbox = run.get("bbox", [0, 0, 0, 0])
                            actual_rect = [bbox[0] + cx, bbox[1] + cy, bbox[2] + cx, bbox[3] + cy]
                            annot_rects[matched_link["text"]].append(actual_rect)
                            
                    if "/Annots" not in page_obj:
                        page_obj.Annots = pdf.make_indirect(pikepdf.Array())
                        
                    for link_text, rects in annot_rects.items():
                        link_url = next((l["target_url"] for l in inline_links if l["text"] == link_text), link_text)
                        for rect in rects:
                            annot_dict = pdf.make_indirect(pikepdf.Dictionary(
                                Type=pikepdf.Name("/Annot"),
                                Subtype=pikepdf.Name("/Link"),
                                Rect=pikepdf.Array(rect),
                                Border=pikepdf.Array([0, 0, 0]),
                                P=page_obj.obj,
                                Contents=pikepdf.String(link_url),
                                A=pikepdf.Dictionary(
                                    S=pikepdf.Name("/URI"),
                                    URI=pikepdf.String(link_url)
                                )
                            ))
                            page_obj.Annots.append(annot_dict)
                # ------------------------------------
                
                if el.get("kids"):
                    for kid in el["kids"]:
                        process_element(kid)
                return
                
            font_runs = page_font_runs.get(el_id, [])
            
            if not font_runs or not is_digital:
                content = FontResolver.process_text_for_rendering(content, document_type)
                
            geom = local_geom(el)
            if not geom:
                return
                
            is_vertical, direction = TextFitter.detect_orientation(el, geom)
            
            lines_out, font_size, actual_rect, warnings = TextFitter.fit_text_block(
                font=font,
                geom=geom,
                content=content,
                vertical=is_vertical,
                direction=direction,
                el_id=el_id,
                document_type=document_type,
                font_runs=font_runs,
                source_lines=el.get("lines"),
                text_generation_mode="synthetic" if (is_digital and el_id not in raster_fallback_nodes) else "auto",
                crop_offset=crop_offset
            )
            
            if lines_out or font_runs:
                inline_links = (el.get("analysis") or {}).get("inline_links", [])
                for link in inline_links:
                    if "start_offset" in link:
                        link["start_offset"] = int(link["start_offset"])
                    if "end_offset" in link:
                        link["end_offset"] = int(link["end_offset"])

                # if el_id in ("9", 9, "30", 30):
                #     # print(f"[DEBUG] Node {el_id} lines_out len: {len(lines_out) if lines_out else 0}, inline_links: {inline_links}")
                #     # for line in lines_out:
                #     #     print(f"[DEBUG] Node {el_id} Line: '{line['text']}' offsets: {line.get('source_start')}..{line.get('source_end')}")
                
                # Check if we have inline links
                if lines_out and inline_links:
                    print(f"[DEBUG] PageRenderer processing inline_links for el_id={el_id}: {inline_links}")
                    def get_next_mcid():
                        nonlocal mcid_counter
                        m = mcid_counter
                        mcid_counter += 1
                        return m
                    
                    # Prepare annotations
                    for link in inline_links:
                        link["annot_refs"] = []
                        # Calculate exact rects for this link across lines
                        for line in lines_out:
                            src_s = line.get("source_start", 0)
                            src_e = line.get("source_end", len(line["text"]))
                            
                            if link["start_offset"] < src_e and link["end_offset"] > src_s:
                                local_start = max(0, link["start_offset"] - src_s)
                                local_end = min(len(line["text"]), link["end_offset"] - src_s)
                                
                                tz = line["tz"]
                                tc = line.get("tc", 0.0)
                                a, b, c, d, e, f = line["tm"]
                                
                                prefix_len = font.text_length(line["text"][:local_start], fontsize=font_size)
                                link_len = font.text_length(line["text"][local_start:local_end], fontsize=font_size)
                                
                                x_offset = prefix_len * (tz / 100.0) + local_start * tc
                                link_width = link_len * (tz / 100.0) + (local_end - local_start) * tc
                                
                                if is_vertical:
                                    if direction == "bottom_to_top":
                                        rect = [e, f + x_offset, e + font_size, f + x_offset + link_width]
                                    else:
                                        rect = [e - font_size, f - x_offset - link_width, e, f - x_offset]
                                else:
                                    rect = [e + x_offset, f, e + x_offset + link_width, f + font_size]
                                
                                target_url = link["target_url"]
                                runtime_id = link.get("runtime_annot_id")
                                existing_annot = None
                                
                                if runtime_id and "/Annots" in page_obj:
                                    try:
                                        # runtime_id is format 'page_0_annot_3'
                                        annot_idx = int(runtime_id.split("_")[-1])
                                        if 0 <= annot_idx < len(page_obj.Annots):
                                            existing_annot = page_obj.Annots[annot_idx]
                                    except Exception:
                                        pass
                                
                                if existing_annot is not None:
                                    annot_dict = existing_annot
                                else:
                                    # In the interest of ensuring the MCID has an OBJR that perfectly matches the text box,
                                    # we generate a new annotation.
                                    annot_dict = pdf.make_indirect(pikepdf.Dictionary(
                                        Type=pikepdf.Name("/Annot"),
                                        Subtype=pikepdf.Name("/Link"),
                                        Rect=pikepdf.Array(rect),
                                        Border=pikepdf.Array([0, 0, 0]),
                                        P=page_obj.obj,
                                        Contents=pikepdf.String(target_url),
                                        A=pikepdf.Dictionary(
                                            S=pikepdf.Name("/URI"),
                                            URI=pikepdf.String(target_url)
                                        )
                                    ))
                                    if "/Annots" not in page_obj:
                                        page_obj.Annots = pdf.make_indirect(pikepdf.Array())
                                    page_obj.Annots.append(annot_dict)
                                    
                                link["annot_refs"].append(annot_dict)
                                
                    raw_str, used_mcids = MCIDWriter.build_inline_link_stream(
                        lines_out, font, font_size, tag_name, inline_links, get_next_mcid
                    )
                    parsed = pikepdf.parse_content_stream(
                        pikepdf.Stream(pdf, raw_str.encode("cp1252", errors="replace"))
                    )
                    text_instructions.extend(parsed)
                    
                    if tag_name != "Artifact":
                        rr = RenderResult(object_id=el_id, page=page_num, mcid=used_mcids)
                        render_results.append(rr)
                        
                else:
                    # (digital: never re-draw link text as a synthetic layer -> duplicate text)
                    if font_runs and is_digital and inline_links and False:
                        def get_next_mcid():
                            nonlocal mcid_counter
                            m = mcid_counter
                            mcid_counter += 1
                            return m
                            
                        raw_str, used_mcids, annot_rects = MCIDWriter.build_native_inline_link_stream(
                            font_runs, tag_name, inline_links, get_next_mcid, crop_offset
                        )
                        parsed = pikepdf.parse_content_stream(
                            pikepdf.Stream(pdf, raw_str.encode("cp1252", errors="replace"))
                        )
                        text_instructions.extend(parsed)
                        
                        if "/Annots" not in page_obj:
                            page_obj.Annots = pdf.make_indirect(pikepdf.Array())
                            
                        for link_text, rects in annot_rects.items():
                            link_url = next((l["target_url"] for l in inline_links if l["text"] == link_text), link_text)
                            for rect in rects:
                                annot_dict = pdf.make_indirect(pikepdf.Dictionary(
                                    Type=pikepdf.Name("/Annot"),
                                    Subtype=pikepdf.Name("/Link"),
                                    Rect=pikepdf.Array(rect),
                                    Border=pikepdf.Array([0, 0, 0]),
                                    P=page_obj.obj,
                                    Contents=pikepdf.String(link_url),
                                    A=pikepdf.Dictionary(
                                        S=pikepdf.Name("/URI"),
                                        URI=pikepdf.String(link_url)
                                    )
                                ))
                                page_obj.Annots.append(annot_dict)
                                
                                for link in inline_links:
                                    if link["text"] == link_text:
                                        if "annot_refs" not in link:
                                            link["annot_refs"] = []
                                        link["annot_refs"].append(annot_dict)
                                        
                        for link in inline_links:
                            if "annot_refs" in link:
                                for umcid in used_mcids:
                                    if isinstance(umcid, dict) and umcid.get("type") == "Link" and umcid.get("target_url") == link["target_url"]:
                                        if "annot_ref" not in umcid:
                                            umcid["annot_ref"] = link["annot_refs"][0]
                                        
                        if tag_name != "Artifact":
                            rr = RenderResult(object_id=el_id, page=page_num, mcid=used_mcids)
                            render_results.append(rr)
                            
                        if el.get("kids"):
                            for kid in el["kids"]:
                                process_element(kid)
                        return

                    # Digital/Hybrid: the original bullet glyph is kept and tagged as Lbl by
                    # DigitalStreamTagger; it is never hidden and re-drawn (that altered the
                    # original content and could duplicate the bullet).
                    has_invalid_bullet = False and is_digital and tag_name == "Lbl" and any(
                        (r.get("font") == "ZapfDingbats" and r.get("text") == "I") or
                        ("Helvetica" in r.get("font", "") and r.get("text") == "\u2022")
                        for r in font_runs
                    )
                    if has_invalid_bullet:
                        raw_str = MCIDWriter.build_native_font_stream(font_runs, mcid_counter, tag_name, crop_offset)
                        parsed = pikepdf.parse_content_stream(
                            pikepdf.Stream(pdf, raw_str.encode("cp1252", errors="replace"))
                        )
                        text_instructions.extend(parsed)
                    elif lines_out and (not is_digital or el_id in raster_fallback_nodes):
                        raw_str = MCIDWriter.build_element_content_stream(lines_out, font_size, mcid_counter, tag_name)
                        parsed = pikepdf.parse_content_stream(
                            pikepdf.Stream(pdf, raw_str.encode("cp1252", errors="replace"))
                        )
                        text_instructions.extend(parsed)
                    elif font_runs and not is_digital:
                        raw_str = MCIDWriter.build_native_font_stream(font_runs, mcid_counter, tag_name, crop_offset)
                        parsed = pikepdf.parse_content_stream(
                            pikepdf.Stream(pdf, raw_str.encode("cp1252", errors="replace"))
                        )
                        text_instructions.extend(parsed)
                    
                    # We only append a RenderResult (and assign an MCID) if we actually generated tags for it!
                    if not is_digital or has_invalid_bullet or (el_id in raster_fallback_nodes and lines_out):
                        if tag_name != "Artifact":
                            render_results.append(RenderResult(
                                object_id=el_id,
                                page=page_num,
                                mcid=mcid_counter
                            ))
                            mcid_counter += 1
                    
                    if el_type == "hyperlink" and target_url and actual_rect and not is_digital:
                        clean_content = re.sub(r'\.{2,}', ' ', content).strip() if content else ""
                        clean_content = re.sub(r'\s+', ' ', clean_content)
                        annot_dict = pdf.make_indirect(pikepdf.Dictionary(
                            Type=pikepdf.Name("/Annot"),
                            Subtype=pikepdf.Name("/Link"),
                            Rect=pikepdf.Array(actual_rect),
                            Border=pikepdf.Array([0, 0, 0]),
                            P=page_obj.obj,
                            Contents=pikepdf.String(clean_content if clean_content else target_url),
                            A=pikepdf.Dictionary(
                                S=pikepdf.Name("/URI"),
                                URI=pikepdf.String(target_url)
                            )
                        ))
                        if "/Annots" not in page_obj:
                            page_obj.Annots = pdf.make_indirect(pikepdf.Array())
                        page_obj.Annots.append(annot_dict)
                        annotation_map[el_id] = annot_dict
                        
                    mcid_counter += 1
                    
                if el.get("kids"):
                    for kid in el["kids"]:
                        process_element(kid)
                
        for el in elements:
            process_element(el)

        # One element rendered in several pieces (TOC Reference: number before its
        # Link and page number after it) -> one result with all its MCIDs.
        if not is_digital:
            merged, order = {}, []
            for rr in render_results:
                key = str(rr.object_id)
                if key in merged and merged[key] is not None and isinstance(rr.mcid, int):
                    merged[key].append(rr.mcid)
                    continue
                if key not in merged:
                    merged[key] = [rr.mcid] if isinstance(rr.mcid, int) else None
                    order.append(rr)
                else:
                    order.append(rr)
            for rr in order:
                m = merged.get(str(rr.object_id))
                if m and len(m) > 1 and isinstance(rr.mcid, int):
                    rr.mcid = list(m)
            render_results = order

        # Digital/Hybrid: every node that received native MCIDs (figures, containers,
        # list labels, ...) must be registered so the structure tree links them.
        if is_digital and digital_tagged_mcids:
            reported = {str(rr.object_id) for rr in render_results}
            for node_id, mcids in digital_tagged_mcids.items():
                if str(node_id) not in reported:
                    render_results.append(RenderResult(object_id=str(node_id), page=page_num, mcid=list(mcids)))
            
        # Combine instructions and filter out any malformed operators with missing operands
        final_instructions = [
            inst for inst in (wrapped_instructions + text_instructions)
            if not (str(inst.operator) == "gs" and len(inst.operands) == 0)
        ]
        new_stream = pikepdf.unparse_content_stream(final_instructions)
        
        # Reconstruct page contents
        if isinstance(page_obj.Contents, pikepdf.Array):
            streams = list(page_obj.Contents)
        else:
            streams = [page_obj.Contents] if page_obj.Contents is not None else []
            
        # The background stream gets the wrapped instructions + new text instructions
        new_contents = [pdf.make_stream(new_stream)]
                
        page_obj.Contents = pikepdf.Array(new_contents)
        
        # Ensure /helv and /fallback are in Resources.Font so DigitalFontRepair can find them
        if "/Resources" not in page_obj:
            page_obj.Resources = pdf.make_indirect(pikepdf.Dictionary())
        if "/Font" not in page_obj.Resources:
            page_obj.Resources.Font = pdf.make_indirect(pikepdf.Dictionary())
            
        for font_resource in [f"/{MCIDWriter.FONT_RESOURCE}", "/fallback"]:
            if font_resource not in page_obj.Resources.Font:
                helv_dict = pikepdf.Dictionary(
                    Type=pikepdf.Name("/Font"),
                    Subtype=pikepdf.Name("/Type1"),
                    BaseFont=pikepdf.Name("/Helvetica"),
                    Encoding=pikepdf.Name("/WinAnsiEncoding")
                )
                page_obj.Resources.Font[font_resource] = pdf.make_indirect(helv_dict)
                
        # Create /FUnicode for Unicode fallback (one shared font object per PDF)
        if "/FUnicode" not in page_obj.Resources.Font:
            page_obj.Resources.Font["/FUnicode"] = PageRenderer._unicode_fallback_font(pdf)
        
        return mcid_counter, render_results, annotation_map
