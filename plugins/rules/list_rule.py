import os
import re
import uuid
from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction
from graph.node import Node


def resolve_list_item_geometry(node, bullet_char, fitz_doc=None):
    """
    Computes precise bounding boxes for the list label (bullet) and body.
    Prefers native character extraction from PyMuPDF if available,
    falling back to line-aware geometric estimation.
    """
    bbox = node.geometry.get("bbox", [0, 0, 0, 0])
    if len(bbox) != 4:
        return [], []
        
    x0, y0, x1, y1 = bbox
    font_size = node.semantic.get("font size") or 11.0
    lines = node.semantic.get("lines")
    page_num = getattr(node, "page", None) or node.metadata.get("page", 1)
    try:
        page_num = int(page_num)
    except Exception:
        page_num = 1
        
    # 1. Native Extraction via PyMuPDF if fitz_doc is available
    if fitz_doc and 0 <= (page_num - 1) < len(fitz_doc):
        try:
            page = fitz_doc[page_num - 1]
            h = page.rect.height
            
            # Determine line 0 vertical bounds
            if lines and len(lines) > 0:
                l0_box = lines[0].get("box_2d") or lines[0].get("bbox", [x0, y1 - font_size * 1.3, x1, y1])
                line0_y0, line0_y1 = l0_box[1], l0_box[3]
            else:
                line0_y1 = y1
                line0_y0 = max(y0, y1 - font_size * 1.5)
                
            line0_yc = (line0_y0 + line0_y1) / 2.0
            
            # Query chars in the left margin area of Line 0
            # PyMuPDF page coordinates are already relative to the CropBox (page.rect),
            # exactly like the CropBox-local JSON boxes, so no offset is added here.
            # (Adding page.cropbox.x0/y0 double-shifted pages whose CropBox != MediaBox.)
            crop_x0, crop_y0 = 0.0, 0.0
            
            clip_fitz = page.rect
            clip_rect = [
                max(0, x0 - 5.0) + crop_x0,
                max(0, h - (line0_y1 + 5.0)) + crop_y0,
                min(page.rect.width, x0 + 50.0) + crop_x0,
                min(h, h - (line0_y0 - 5.0)) + crop_y0
            ]
            import fitz
            clip_fitz_rect = fitz.Rect(*clip_rect)
            # clip is in the displayed frame; get_text works in the unrotated page
            if page.rotation:
                clip_fitz_rect = clip_fitz_rect * page.derotation_matrix
            
            valid_chars = []
            for b in page.get_text("rawdict", clip=clip_fitz_rect).get("blocks", []):
                for l in b.get("lines", []):
                    # Skip non-horizontal (e.g. diagonal watermark) text
                    line_dir = l.get("dir", (1.0, 0.0))
                    if abs(line_dir[1]) > 0.3:
                        continue
                    for s in l.get("spans", []):
                        if s.get("size", 0) > font_size * 2.2:
                            continue
                        for c in s.get("chars", []):
                            if page.rotation:
                                c = dict(c, bbox=tuple(fitz.Rect(c["bbox"]) * page.rotation_matrix))
                            # c["bbox"] is CropBox-relative Top-Left. Convert to CropBox-local Bottom-Left.
                            cb = [
                                c["bbox"][0] - crop_x0, 
                                h - (c["bbox"][3] - crop_y0), 
                                c["bbox"][2] - crop_x0, 
                                h - (c["bbox"][1] - crop_y0)
                            ]
                            char_h = cb[3] - cb[1]
                            char_yc = (cb[1] + cb[3]) / 2.0
                            if char_h > font_size * 2.0:
                                continue
                            if abs(char_yc - line0_yc) > max(10.0, font_size):
                                continue
                            valid_chars.append((c["c"], cb, s["font"], s.get("size", font_size)))
                            
            if valid_chars:
                valid_chars.sort(key=lambda item: item[1][0])
                non_space = [c for c in valid_chars if c[0].strip()]
                if non_space:
                    first_char_x0 = non_space[0][1][0]
                    
                    # If first char starts noticeably further in (e.g. > x0 + 12), the bullet may be a vector graphic
                    if first_char_x0 > x0 + max(12.0, font_size * 1.2) and len(bullet_char) <= 1:
                        body_x0 = first_char_x0
                        lbl_w = min(font_size * 0.45, max(3.0, (body_x0 - x0) * 0.5))
                        lbl_x1 = min(x0 + lbl_w, body_x0 - 2.0)
                        lbl_y_min = line0_yc - font_size * 0.25
                        lbl_y_max = line0_yc + font_size * 0.25
                        return [x0, lbl_y_min, lbl_x1, lbl_y_max], [body_x0, y0, x1, y1]
                        
                    lbl_chars = []
                    body_start_char = None
                    if len(bullet_char) <= 1:
                        lbl_chars = [non_space[0]]
                        if len(non_space) > 1:
                            body_start_char = non_space[1]
                    else:
                        prefix = ""
                        for idx, c in enumerate(non_space):
                            prefix += c[0]
                            lbl_chars.append(c)
                            if len(prefix) >= len(bullet_char):
                                if idx + 1 < len(non_space):
                                    body_start_char = non_space[idx + 1]
                                break
                                
                    if lbl_chars:
                        lbl_x0 = min(c[1][0] for c in lbl_chars)
                        lbl_y0 = min(c[1][1] for c in lbl_chars)
                        lbl_x1 = max(c[1][2] for c in lbl_chars)
                        lbl_y1 = max(c[1][3] for c in lbl_chars)
                        
                        if body_start_char:
                            body_x0 = body_start_char[1][0]
                        else:
                            body_x0 = max(lbl_x1 + 3.0, x0 + font_size * 0.8)
                            
                        return [lbl_x0, lbl_y0, lbl_x1, lbl_y1], [body_x0, y0, max(x1, body_x0 + 10.0), y1]
        except Exception:
            pass

    # 2. Geometric / Line-based fallback (scanned or native extraction not matched)
    lh = min(y1 - y0, font_size * 1.3)
    if lines and len(lines) > 0:
        l0_box = lines[0].get("box_2d") or lines[0].get("bbox", [x0, y1 - lh, x1, y1])
        lbl_y0, lbl_y1 = l0_box[1], l0_box[3]
        if len(lines) > 1:
            l1_box = lines[1].get("box_2d") or lines[1].get("bbox", [x0 + 10.0, y0, x1, y0 + lh])
            body_x0 = l1_box[0]
        else:
            body_x0 = x0 + (font_size * 0.8 if len(bullet_char) <= 1 else len(bullet_char) * font_size * 0.55 + 4.0)
    else:
        lbl_y0 = y1 - lh
        lbl_y1 = y1
        body_x0 = x0 + (font_size * 0.8 if len(bullet_char) <= 1 else len(bullet_char) * font_size * 0.55 + 4.0)
        
    lbl_w = font_size * 0.45 if len(bullet_char) <= 1 else len(bullet_char) * font_size * 0.55
    lbl_x1 = min(x0 + lbl_w, body_x0 - 2.0)
    
    return [x0, lbl_y0, lbl_x1, lbl_y1], [body_x0, y0, max(x1, body_x0 + 10.0), y1]


class ListStructureRule(BaseSemanticRule):
    name = "ListStructureRule"
    priority = 8
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        # Resolve PDF document handle if available
        pdf_path = (
            context.metadata.get("pdf_path")
            or getattr(context, "config", {}).get("input_pdf")
            or getattr(self, "config", {}).get("input_pdf")
        )
        if not pdf_path:
            ws = context.metadata.get("workspace_path")
            if ws:
                cand = os.path.join(ws, "input.pdf")
                if os.path.exists(cand):
                    pdf_path = cand
                else:
                    cand2 = os.path.join(ws, "input")
                    if os.path.isdir(cand2):
                        for f in os.listdir(cand2):
                            if f.lower().endswith(".pdf"):
                                pdf_path = os.path.join(cand2, f)
                                break
        fitz_doc = None
        if pdf_path and os.path.exists(pdf_path):
            try:
                import fitz
                fitz_doc = fitz.open(pdf_path)
            except Exception:
                fitz_doc = None
                
        try:
            nodes = list(context.graph.node_map.values())
            for node in nodes:
                sem_type = node.semantic.get("type", "")
                if sem_type == "list_item":
                    if node.text and node.text.strip():
                        has_lbody = False
                        for child_uuid in node.relations.get("children", []):
                            child = context.graph.get_node(child_uuid)
                            if child and child.semantic.get("type") == "list_body":
                                has_lbody = True
                                break
                                
                        if not has_lbody:
                            text = node.text.strip()
                            bullet_char = ""
                            rest_text = text
                            
                            # Matches standard bullets OR alphanumeric labels like 1., a., (1), [a], 1.1, or bare 1, a, ii
                            list_regex = r'^([\u2022\u25CF\u25CB\u25A0\u25A1\u25AA\u25AB\u25BA\u25B8\u2013\-]|(?:(?:\d+(?:\.\d+)*|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,6})[.)]?)|(?:\([a-zA-Z0-9.]{1,6}\))|(?:\[[a-zA-Z0-9.]{1,6}\]))\s+(.*)'
                            bullet_pattern = re.compile(list_regex, re.DOTALL)
                            match = bullet_pattern.match(text)
                            
                            if match:
                                bullet_char = match.group(1)
                                rest_text = match.group(2)
                            elif text and text[0] in ['•', '●', '○', '■', '□', '▪', '▫', '►', '▸', '–', '-']:
                                bullet_char = text[0]
                                rest_text = text[1:].strip()
                                
                            # Document type check for bullet injection
                            doc_type = getattr(context, "config", {}).get("document_type", "Scanned PDF")
                            if not bullet_char and doc_type == "Scanned PDF":
                                bullet_char = "•"
                                
                            modified_nodes = [node.uuid]
                            
                            # Calculate precise geometry for Lbl and LBody
                            lbl_bbox, lbody_bbox = resolve_list_item_geometry(node, bullet_char, fitz_doc=fitz_doc)
                            
                            # 1. Create Lbl if we have a bullet
                            lbl_node = None
                            if bullet_char:
                                lbl_id = f"{node.uuid}-lbl"
                                lbl_node = Node({
                                    "id": lbl_id,
                                    "uuid": lbl_id,
                                    "content": bullet_char,
                                    "metadata": {"page": getattr(node, "page", None) or node.metadata.get("page")}
                                })
                                lbl_node.semantic["type"] = "list_label"
                                lbl_node.geometry["bbox"] = lbl_bbox
                                if len(lbl_bbox) == 4:
                                    lbl_node.geometry["width"] = round(lbl_bbox[2] - lbl_bbox[0], 3)
                                    lbl_node.geometry["height"] = round(lbl_bbox[3] - lbl_bbox[1], 3)
                                lbl_node.relations["parent"] = node.uuid
                                
                                for key in ["font", "font size"]:
                                    if key in node.semantic:
                                        lbl_node.semantic[key] = node.semantic[key]
                                        
                                if "reading_order" in node.metadata:
                                    lbl_node.metadata["reading_order"] = node.metadata["reading_order"]
                                    
                                if len(lbl_bbox) == 4:
                                    lbl_node.semantic["lines"] = [{"text": bullet_char, "box_2d": lbl_bbox}]
                                    
                                context.graph.add_node(lbl_node)
                                node.relations.setdefault("children", []).append(lbl_node.uuid)
                                modified_nodes.append(lbl_node.uuid)
                            
                            # 2. Create LBody for text
                            lbody_id = f"{node.uuid}-lbody"
                            lbody_node = Node({
                                "id": lbody_id,
                                "uuid": lbody_id,
                                "content": rest_text,
                                "metadata": {"page": getattr(node, "page", None) or node.metadata.get("page")}
                            })
                            lbody_node.semantic["type"] = "list_body"
                            lbody_node.geometry["bbox"] = lbody_bbox
                            if len(lbody_bbox) == 4:
                                lbody_node.geometry["width"] = round(lbody_bbox[2] - lbody_bbox[0], 3)
                                lbody_node.geometry["height"] = round(lbody_bbox[3] - lbody_bbox[1], 3)
                            if "parent_bbox" in node.geometry and len(node.geometry["parent_bbox"]) == 4:
                                p_bbox = node.geometry["parent_bbox"]
                                if len(lbody_bbox) == 4:
                                    lbody_node.geometry["parent_bbox"] = [lbody_bbox[0], p_bbox[1], p_bbox[2], p_bbox[3]]
                                else:
                                    lbody_node.geometry["parent_bbox"] = p_bbox
                            if "reading_order" in node.metadata:
                                lbody_node.metadata["reading_order"] = node.metadata["reading_order"]
                            lbody_node.relations["parent"] = node.uuid
                            
                            # Adjust lines for LBody if original node had lines
                            orig_lines = node.semantic.get("lines")
                            if orig_lines and isinstance(orig_lines, list) and len(orig_lines) > 0 and len(lbody_bbox) == 4:
                                new_lines = []
                                for idx, l in enumerate(orig_lines):
                                    l_dict = dict(l)
                                    if idx == 0:
                                        l_text = l_dict.get("text", "")
                                        match_l = bullet_pattern.match(l_text) if bullet_char else None
                                        if match_l:
                                            l_dict["text"] = match_l.group(2)
                                        elif bullet_char and l_text.startswith(bullet_char):
                                            l_dict["text"] = l_text[len(bullet_char):].strip()
                                        b2d = l_dict.get("box_2d") or l_dict.get("bbox")
                                        if b2d and len(b2d) == 4:
                                            l_dict["box_2d"] = [lbody_bbox[0], b2d[1], b2d[2], b2d[3]]
                                    new_lines.append(l_dict)
                                lbody_node.semantic["lines"] = new_lines
                                
                            context.graph.add_node(lbody_node)
                            node.relations.setdefault("children", []).append(lbody_node.uuid)
                            modified_nodes.append(lbody_node.uuid)
                            
                            # Move existing children (like nested lists) from LI to LBody
                            existing_children = [c for c in node.relations.get("children", []) if c not in (lbl_node.uuid, lbody_node.uuid)]
                            for c_uuid in existing_children:
                                node.relations["children"].remove(c_uuid)
                                c_node = context.graph.get_node(c_uuid)
                                if c_node:
                                    c_node.relations["parent"] = lbody_node.uuid
                                lbody_node.relations.setdefault("children", []).append(c_uuid)
                            
                            # Clear text from LI node
                            node.text = ""
                            
                            for key in ["font", "font size", "content"]:
                                if key in node.semantic:
                                    if key != "content" or not bullet_char:
                                        lbody_node.semantic[key] = node.semantic[key]
                                    del node.semantic[key]
                                    
                            lbody_node.semantic["content"] = rest_text
                            if bullet_char:
                                lbl_node.semantic["content"] = bullet_char
                                lbl_node.semantic["actual_text"] = bullet_char
                            node.add_history(f"{self.name}: Split text into Lbl and LBody")
                            
                            action_queue.reports.append({
                                "rule": self.name,
                                "message": f"Wrapped LI text into Lbl and LBody for {node.uuid}",
                                "modified_nodes": modified_nodes
                            })
        finally:
            if fitz_doc:
                try:
                    fitz_doc.close()
                except Exception:
                    pass
