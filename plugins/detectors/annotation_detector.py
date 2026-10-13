import pikepdf
from typing import Dict, Any, List
from engine.document_context import DocumentContext
from plugins.base_plugin import BasePlugin
from graph.document_graph import Node

def compute_overlap(r1, r2):
    """Calculates intersection area / r1 area."""
    x_left = max(r1[0], r2[0])
    y_bottom = max(r1[1], r2[1])
    x_right = min(r1[2], r2[2])
    y_top = min(r1[3], r2[3])

    if x_right < x_left or y_top < y_bottom:
        return 0.0

    intersection_area = (x_right - x_left) * (y_top - y_bottom)
    r1_area = (r1[2] - r1[0]) * (r1[3] - r1[1])
    if r1_area == 0:
        return 0.0
    return intersection_area / r1_area

def rect_center_y(rect):
    return (rect[1] + rect[3]) / 2

class AnnotationDetector(BasePlugin):
    """
    Discovers native PDF annotations and introduces semantic annotation nodes into the graph.
    """
    category = "detectors"
    name = "AnnotationDetector"

    def run(self, context: DocumentContext):
        pdf_path = self.config.get("input_pdf") or context.metadata.get("pdf_path")
        if not pdf_path:
            return

        try:
            pdf = pikepdf.Pdf.open(pdf_path)
        except Exception as e:
            context.metadata.setdefault("warnings", []).append(f"AnnotationDetector could not open PDF: {e}")
            return

        for page_idx, page in enumerate(pdf.pages):
            if "/Annots" not in page:
                continue

            # Resolve references if it's an indirect array
            annots = page.Annots
            
            for annot_idx, annot in enumerate(annots):
                subtype = str(annot.get("/Subtype", ""))
                
                # We only support /Widget and /Link
                if subtype not in ("/Widget", "/Link"):
                    context.metadata.setdefault("unsupported_annotations", []).append({
                        "page": page_idx + 1,
                        "index": annot_idx,
                        "subtype": subtype
                    })
                    continue

                rect = [float(x) for x in annot.get("/Rect", [])]
                if not rect or len(rect) != 4:
                    continue
                    
                media_box = page.get("/MediaBox", [0, 0, 0, 0])
                page_h = float(media_box[3]) - float(media_box[1])
                y_down_rect = [rect[0], page_h - rect[3], rect[2], page_h - rect[1]]

                from renderer.page_geometry import PageFrame
                frame = PageFrame(page)
                crop_x0, crop_y0 = frame.crop_offset
                self._frame = frame

                runtime_id = f"page_{page_idx}_annot_{annot_idx}"

                if subtype == "/Widget":
                    self._process_widget(annot, rect, y_down_rect, runtime_id, page_idx + 1, page_h, context, crop_x0, crop_y0)
                elif subtype == "/Link":
                    self._process_link(annot, rect, y_down_rect, runtime_id, page_idx + 1, page_h, context, crop_x0, crop_y0)

        pdf.close()

    def _process_widget(self, annot, rect, y_down_rect, runtime_id, page_num, page_height, context: DocumentContext, crop_x0=0.0, crop_y0=0.0):
        t_val = str(annot.get("/T", "")) if "/T" in annot else ""
        tu_val = str(annot.get("/TU", "")) if "/TU" in annot else ""
        ft_val = str(annot.get("/FT", "")) if "/FT" in annot else ""

        node = Node({
            "type": "form_field",
            "text": "",
            "bbox": rect,
            "page_number": page_num
        })
        node.semantic["pdf_tag"] = "Form"
        node.semantic["annotation"] = {
            "subtype": "Widget",
            "runtime_annot_id": runtime_id,
            "rect": rect,
            "T": t_val,
            "TU": tu_val,
            "FT": ft_val
        }

        # Widget Association Ranking
        # 1. Table-cell ownership
        # 2. Semantic form-field label match (naive)
        # 3. Accessible-Name Match
        # 4. Row ownership
        # 5. Geometric proximity

        candidates = []
        for cand_uuid, cand_node in context.graph.node_map.items():
            if cand_node.metadata.get("page") != page_num and getattr(cand_node, "page", None) != page_num:
                continue
            
            cand_rect = cand_node.geometry.get("bbox")
            if not cand_rect:
                continue

            try:
                cand_rect_global = self._frame.json_bbox_to_pdf(cand_rect)
            except Exception:
                cand_rect_global = [cand_rect[0] + crop_x0, cand_rect[1] + crop_y0, cand_rect[2] + crop_x0, cand_rect[3] + crop_y0]
            cand_rect_top_down = [
                cand_rect_global[0],
                page_height - cand_rect_global[3] if page_height else cand_rect_global[1],
                cand_rect_global[2],
                page_height - cand_rect_global[1] if page_height else cand_rect_global[3]
            ]

            score = 0
            if cand_node.semantic.get("type") == "table_data_cell":
                if compute_overlap(y_down_rect, cand_rect_top_down) > 0.8:
                    score += 100

            if cand_node.semantic.get("type") == "form_label" and cand_node.text.strip() == t_val:
                score += 80

            if cand_node.text.strip() == tu_val and tu_val:
                score += 60

            # Horizontal alignment
            c_y = rect_center_y(y_down_rect)
            cand_c_y = rect_center_y(cand_rect_top_down)
            if abs(c_y - cand_c_y) < 10:
                score += 40
                
            if score > 0:
                print(f"[AnnotationDetector] Widget {runtime_id} vs {cand_node.uuid} score={score}")

            if score > 0:
                candidates.append((score, cand_uuid))

        candidates.sort(reverse=True, key=lambda x: x[0])
        
        status = "UNASSOCIABLE"
        target_parent = None
        
        if len(candidates) > 0:
            if len(candidates) == 1 or (candidates[0][0] - candidates[1][0] >= 20):
                target_parent = candidates[0][1]
                status = "ASSOCIATED"
            else:
                status = "AMBIGUOUS"

        node.semantic["association_status"] = status
        
        if status == "ASSOCIATED" and target_parent:
            parent_node = context.graph.get_node(target_parent)
            if parent_node:
                # Insert as child
                node.relations["parent"] = target_parent
                parent_node.relations.setdefault("children", []).append(node.uuid)

        context.graph.add_node(node)

    def _process_link(self, annot, rect, y_down_rect, runtime_id, page_num, page_height, context: DocumentContext, crop_x0=0.0, crop_y0=0.0):
        # Check if runtime_id is already in the graph (e.g. from gemini_extractor TOC matching)
        for cand_node in context.graph.node_map.values():
            cand_annot = cand_node.semantic.get("annotation")
            if cand_annot and cand_annot.get("runtime_annot_id") == runtime_id:
                # Already processed
                return
            # Also check if it's an objr node
            if cand_node.semantic.get("type") == "objr" and cand_node.semantic.get("runtime_annot_id") == runtime_id:
                return
        # Action signature
        action_type = ""
        action_sig = ""
        a_dict = annot.get("/A")
        if a_dict:
            action_type = str(a_dict.get("/S", ""))
            action_sig = str(a_dict.get("/URI", a_dict.get("/D", "")))
            
        node = Node({
            "type": "hyperlink",
            "text": "",
            "bbox": rect,
            "page_number": page_num
        })
        node.semantic["pdf_tag"] = "Link"
        node.semantic["annotation"] = {
            "subtype": "Link",
            "runtime_annot_id": runtime_id,
            "rect": rect,
            "action_type": action_type,
            "action_sig": action_sig
        }
        
        # Link Association Ranking
        # 1. Exact semantic ownership
        # 2. Deepest valid semantic leaf/container
        # 3. Strong spatial overlap
        candidates = []
        for cand_uuid, cand_node in context.graph.node_map.items():
            if cand_node.metadata.get("page") != page_num and getattr(cand_node, "page", None) != page_num:
                continue
            
            cand_rect = cand_node.geometry.get("bbox")
            if not cand_rect:
                continue
                
            try:
                cand_rect_global = self._frame.json_bbox_to_pdf(cand_rect)
            except Exception:
                cand_rect_global = [cand_rect[0] + crop_x0, cand_rect[1] + crop_y0, cand_rect[2] + crop_x0, cand_rect[3] + crop_y0]
            
            if page_height:
                cand_rect_top_down = [
                    cand_rect_global[0],
                    page_height - cand_rect_global[3],
                    cand_rect_global[2],
                    page_height - cand_rect_global[1]
                ]
            else:
                cand_rect_top_down = cand_rect

            overlap = compute_overlap(y_down_rect, cand_rect_top_down)
            
            # Horizontal alignment
            c_y = rect_center_y(y_down_rect)
            cand_c_y = rect_center_y(cand_rect_top_down)
            
            c_y_diff = abs(c_y - cand_c_y)
            if c_y_diff < 15:
                pass

            score = 0
            if overlap > 0.5:
                score += overlap * 50
                
            if abs(c_y - cand_c_y) < 10:
                score += 40
                
            if score > 0:
                # Boost leaf nodes (no children)
                if not cand_node.relations.get("children"):
                    score += 50
                # Boost specific types (Reference is best for Links)
                cand_type = cand_node.semantic.get("type")
                if cand_type in ["reference", "Reference"]:
                    score += 30
                elif cand_node.semantic.get("pdf_tag") == "Reference":
                    score += 30
                elif cand_type in ["text", "table_data_cell"]:
                    score += 20
                candidates.append((score, cand_uuid))

        candidates.sort(reverse=True, key=lambda x: x[0])
        
        status = "UNASSOCIABLE"
        target_parent = None
        
        if len(candidates) > 0:
            if len(candidates) == 1 or (candidates[0][0] - candidates[1][0] >= 10):
                target_parent = candidates[0][1]
                status = "ASSOCIATED"
            else:
                status = "AMBIGUOUS"
                
        node.semantic["association_status"] = status
        
        if status == "ASSOCIATED" and target_parent:
            parent_node = context.graph.get_node(target_parent)
            if parent_node:
                # Check if the parent already has a hyperlink child (e.g. from gemini_extractor)
                existing_hyperlink = None
                if "children" in parent_node.relations:
                    for child_uuid in parent_node.relations["children"]:
                        child_node = context.graph.get_node(child_uuid)
                        if child_node and child_node.semantic.get("type") in ("hyperlink", "link"):
                            existing_hyperlink = child_node
                            break
                            
                if existing_hyperlink:
                    # Check distance to avoid merging text links and page number links in TOC
                    eh_bbox = existing_hyperlink.semantic.get("bbox", existing_hyperlink.semantic.get("bounding_box"))
                    if eh_bbox:
                        eh_cx = (eh_bbox[0] + eh_bbox[2]) / 2.0
                        my_cx = (cand_rect_global[0] + cand_rect_global[2]) / 2.0
                        if abs(eh_cx - my_cx) > 50:
                            existing_hyperlink = None

                if existing_hyperlink:
                    # Merge this annotation into the existing hyperlink node's kids as an OBJR
                    objr_node = Node({
                        "type": "objr",
                        "page_number": page_num
                    })
                    objr_node.semantic["annotation"] = node.semantic["annotation"]
                    context.graph.add_node(objr_node)
                    
                    objr_node.relations["parent"] = existing_hyperlink.uuid
                    existing_hyperlink.relations.setdefault("children", []).append(objr_node.uuid)
                    
                    # Ensure the existing hyperlink is aware it has kids now (if it wasn't already)
                    if "kids" not in existing_hyperlink.semantic:
                        existing_hyperlink.semantic["kids"] = []
                    existing_hyperlink.semantic["kids"].append(objr_node.semantic)
                    
                    # We don't add the blank 'node' to the graph
                    return
                else:
                    if parent_node.semantic.get("type") in ("paragraph", "heading", "table_cell", "list_item", "caption", "text_block"):
                        import fitz
                        import re
                        try:
                            pdf_path = context.metadata.get("pdf_path") or context.metadata.get("workspace_path") + "/input.pdf"
                            if not __import__("os").path.exists(pdf_path) and "input_pdf" in context.metadata.get("config", {}):
                                pdf_path = context.metadata["config"]["input_pdf"]
                                
                            fitz_doc = fitz.open(pdf_path)
                            page_obj = fitz_doc[page_num - 1]
                            words = page_obj.get_text("words")
                            
                            link_words = []
                            seen_rects = []
                            for w in words:
                                w_rect = fitz.Rect(w[:4])
                                l_rect = fitz.Rect(y_down_rect)
                                if w_rect.intersects(l_rect):
                                    intersection = w_rect.intersect(l_rect)
                                    if (intersection.width * intersection.height) / (w_rect.width * w_rect.height) > 0.4:
                                        # Check if this w_rect heavily overlaps with any already seen word
                                        is_dup = False
                                        for seen in seen_rects:
                                            s_inter = w_rect.intersect(seen)
                                            if s_inter.width > 0 and s_inter.height > 0:
                                                if (s_inter.width * s_inter.height) / (w_rect.width * w_rect.height) > 0.8:
                                                    is_dup = True
                                                    break
                                        
                                        if not is_dup:
                                            seen_rects.append(w_rect)
                                            link_words.append(w[4])
                            
                            if link_words:
                                # Reconstruct the text pattern ensuring special regex chars are escaped
                                pattern_parts = []
                                for i, w in enumerate(link_words):
                                    escaped_w = re.escape(w)
                                    if i == len(link_words) - 1:
                                        # Make trailing punctuation optional in case Gemini omitted it
                                        escaped_w = re.sub(r'(\\[^\w\s])+$', lambda m: ''.join([c + '?' for c in re.findall(r'\\.', m.group(0))]), escaped_w)
                                    pattern_parts.append(escaped_w)
                                pattern = r'\s*'.join(pattern_parts)
                                match = re.search(pattern, parent_node.text)
                                if match:
                                    parent_node.analysis.setdefault("inline_links", []).append({
                                        "start_offset": match.start(),
                                        "end_offset": match.end(),
                                        "text": match.group(0),
                                        "target_url": action_sig,
                                        "annot_refs": [pdf.make_indirect(pikepdf.Dictionary(
                                            Type=pikepdf.Name("/OBJR"),
                                            Obj=pikepdf.Dictionary(StructParent=0)  # Dummy to prevent crash, handled in struct_tree_builder
                                        ))] if 'pdf' in locals() else [],
                                        "runtime_annot_id": runtime_id
                                    })
                                    return # Skip adding the node to graph because it's handled as an inline link
                        except Exception as e:
                            print(f"[AnnotationDetector] Error resolving inline link text: {e}")

                    node.relations["parent"] = target_parent
                    parent_node.relations.setdefault("children", []).append(node.uuid)

        context.graph.add_node(node)

