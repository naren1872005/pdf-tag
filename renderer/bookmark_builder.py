import pikepdf
from pikepdf import Array, Name, OutlineItem

class BookmarkBuilder:
    @staticmethod
    def build(pdf, doc_json, page_objs):
        """
        Reads the finalized semantic heading hierarchy and builds the PDF outline/bookmarks.
        Conforms strictly to PDF/UA-1 accessibility standards with hierarchical nesting
        and /XYZ coordinate targets.
        """
        headings = []
        for p in doc_json.get("pages", []):
            page_num = p.get("page_number", 1)
            # Ensure page_objs index is valid
            if page_num < 1 or page_num > len(page_objs):
                continue
            page_obj = page_objs[page_num - 1]
            
            def extract_headings(els):
                for el in els:
                    # Skip artifacts or decorative page furniture
                    t_str = str(el.get("type", "")).lower()
                    tag_str = str(el.get("tag", "")).upper()
                    if t_str == "artifact" or tag_str == "ARTIFACT":
                        continue

                    is_h = t_str == "heading" or tag_str in ("H1", "H2", "H3", "H4", "H5", "H6") or t_str in ("h1", "h2", "h3", "h4", "h5", "h6")
                    if is_h:
                        level = el.get("heading_level")
                        if level is None:
                            for candidate in (tag_str, t_str.upper()):
                                if candidate.startswith("H") and len(candidate) == 2 and candidate[1].isdigit():
                                    level = int(candidate[1])
                                    break
                        if level is None:
                            level = 1
                        try:
                            level = int(level)
                        except (ValueError, TypeError):
                            level = 1

                        content = el.get("content") or el.get("text") or f"Heading {level}"
                        content = str(content).replace('\n', ' ').strip()
                        if content:
                            bbox = el.get("_geom") or el.get("bounding_box")
                            headings.append({
                                "level": level,
                                "title": content,
                                "page_obj": page_obj,
                                "bbox": bbox
                            })
                    if el.get("kids"):
                        extract_headings(el["kids"])
                        
            extract_headings(p.get("elements", []))
            
        if not headings:
            return
            
        with pdf.open_outline() as outline:
            # Clear existing bookmarks to avoid duplication
            if hasattr(outline.root, "children"):
                outline.root.children.clear()
            else:
                outline.root.clear()
            
            # stack holds tuples of (level, item)
            stack = [(0, outline.root)]
            
            for h in headings:
                p_obj = h["page_obj"].obj if hasattr(h["page_obj"], "obj") else h["page_obj"]
                bbox = h.get("bbox")
                
                # Use PDF /XYZ destination targeting top-left of the heading
                if isinstance(bbox, dict) and "left" in bbox and "top" in bbox:
                    dest = Array([p_obj, Name("/XYZ"), int(round(bbox["left"])), int(round(bbox["top"])), None])
                elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                    dest = Array([p_obj, Name("/XYZ"), int(round(bbox[0])), int(round(bbox[3])), None])
                else:
                    dest = Array([p_obj, Name("/Fit")])

                item = OutlineItem(h["title"], destination=dest)
                
                # Pop until we find a parent with level < h["level"]
                while stack and stack[-1][0] >= h["level"]:
                    stack.pop()
                    
                if not stack:
                    stack = [(0, outline.root)]
                    
                parent_item = stack[-1][1]
                if hasattr(parent_item, "children"):
                    parent_item.children.append(item)
                else:
                    parent_item.append(item)
                
                stack.append((h["level"], item))
