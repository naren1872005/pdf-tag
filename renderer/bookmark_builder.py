import pikepdf
from pikepdf import Array, Name, OutlineItem

class BookmarkBuilder:
    @staticmethod
    def build(pdf, doc_json, page_objs):
        """
        Reads the finalized semantic heading hierarchy and builds the PDF outline.
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
                    if el.get("type") == "heading":
                        level = int(el.get("heading_level", 1))
                        content = el.get("content", f"Heading {level}")
                        # Remove newlines for bookmark title
                        content = str(content).replace('\n', ' ').strip()
                        if content:
                            headings.append({
                                "level": level,
                                "title": content,
                                "page_obj": page_obj
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
                dest = Array([h["page_obj"].obj if hasattr(h["page_obj"], "obj") else h["page_obj"], Name("/Fit")])
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
