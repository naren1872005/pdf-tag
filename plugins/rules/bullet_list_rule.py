from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction
from graph.node import Node
import uuid
import re

class BulletListRule(BaseSemanticRule):
    name = "BulletListRule"
    priority = 7
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        # We need nodes in reading order to find consecutive items
        nodes = sorted(context.graph.get_all_nodes(), key=lambda n: int(n.id) if n.id is not None and str(n.id).isdigit() else 999999)
        
        bullet_pattern = re.compile(r'^[\u2022\u25CF\u25CB\u25A0\u25A1\u25AA\u25AB\u25BA\u25B8\u2013\-]\s+')
        
        current_list = []
        lists_found = []
        
        for node in nodes:
            sem_type = node.semantic.get("type", "")
            
            # Handle inline lists inside table cells or other single nodes
            if sem_type in ("table", "table_row", "table_data_cell", "figure", "list"):
                # But if it's a table_data_cell and contains multiple bullets, we can split it
                if sem_type == "table_data_cell" and node.text:
                    text = node.text.strip()
                    # Count bullets
                    bullets = []
                    for b in ['■', '□', '•', '●', '○', '►', '▸']:
                        if b in text:
                            # Only bullets that START a line are list markers. A symbol in the
                            # middle of a sentence ("Browse ▶ Check / ■ Stop buttons") is not a list.
                            pos = 0
                            while pos < len(text):
                                pos = text.find(b, pos)
                                if pos == -1: break
                                if pos == 0 or text[pos - 1] in "\n\r":
                                    bullets.append((pos, b))
                                pos += 1
                                
                    if len(bullets) >= 1:
                        # We have an inline list!
                        bullets.sort()
                        # Instead of splitting it here which is complex for GraphAction,
                        # we can queue an action to split this node. 
                        # Wait, GraphAction doesn't have a split action. Let's just mutate graph safely.
                        
                        # Calculate rough bounding boxes
                        bbox = node.geometry.get("bbox", [0,0,0,0])
                        width = bbox[2] - bbox[0]
                        segment_width = width / len(bullets)
                        
                        # Create L node
                        l_id = str(uuid.uuid4())
                        l_node = Node({
                            "id": l_id,
                            "uuid": l_id,
                            "content": "",
                            "page_number": node.page
                        })
                        l_node.semantic["type"] = "list"
                        l_node.geometry["bbox"] = bbox
                        
                        # Add L node to graph and make it child of this TD
                        context.graph.add_node(l_node)
                        l_node.relations["parent"] = node.uuid
                        node.relations.setdefault("children", []).append(l_node.uuid)
                        
                        text_len = max(len(text), 1)
                        for i, (pos, b) in enumerate(bullets):
                            start = pos
                            end = bullets[i+1][0] if i+1 < len(bullets) else len(text)
                            item_text = text[start:end].strip()
                            
                            # Replace unsupported bullet for ALL PDFs to ensure WinAnsiEncoding works in the content stream.
                            # The original character is still preserved in the tag tree via /ActualText (set in ListStructureRule).
                            item_text = item_text.replace(b, '•') if b in ['■', '□', '●', '○', '►', '▸'] else item_text
                            
                            li_id = str(uuid.uuid4())
                            li_node = Node({
                                "id": li_id,
                                "uuid": li_id,
                                "content": item_text,
                                "page_number": node.page
                            })
                            li_node.semantic["type"] = "list_item"
                            
                            # Tighten bbox based on text length to avoid stretching in Hybrid mode
                            font_size = node.semantic.get("font size", 11.0) or 11.0
                            # estimate character width (roughly 0.5 * font_size)
                            estimated_width = len(item_text) * font_size * 0.5
                            # Add some padding for the bullet
                            estimated_width += 15.0 
                            
                            # Center the list items horizontally if they don't take up the full width,
                            # or just left align them within their segment.
                            # We'll left align them within their segment for simplicity.
                            x0 = bbox[0] + (width * (start / text_len))
                            # Ensure it doesn't exceed the segment or the page
                            x1 = min(x0 + estimated_width, bbox[0] + (width * (end / text_len)))
                            
                            li_node.geometry["bbox"] = [x0, bbox[1], x1, bbox[3]]
                            li_node.geometry["parent_bbox"] = bbox
                            
                            for key in ["font", "font size"]:
                                if key in node.semantic:
                                    li_node.semantic[key] = node.semantic[key]
                                    
                            if "reading_order" in node.metadata:
                                li_node.metadata["reading_order"] = node.metadata["reading_order"]
                                
                            context.graph.add_node(li_node)
                            li_node.relations["parent"] = l_node.uuid
                            l_node.relations.setdefault("children", []).append(li_node.uuid)
                            
                        # Clear the text of the original TD so it doesn't render twice
                        node.text = ""
                        if "content" in node.semantic:
                            del node.semantic["content"]
                        
                        action_queue.reports.append({
                            "rule": self.name,
                            "message": f"Split inline list in {node.uuid} into {len(bullets)} list items",
                            "modified_nodes": [node.uuid, l_node.uuid]
                        })
                        
                if current_list:
                    lists_found.append(current_list)
                    current_list = []
                continue
                
            text = (node.text or "").strip()

            # Items Gemini already placed inside an L (nested lists) keep their structure.
            parent_uuid = node.relations.get("parent")
            parent_node = context.graph.get_node(parent_uuid) if parent_uuid else None
            if parent_node is not None and parent_node.semantic.get("type") in ("list", "list_body", "list_item"):
                if current_list:
                    lists_found.append(current_list)
                    current_list = []
                continue
            
            # Check if it has a bullet or is already classified as list_item
            is_bullet = bool(bullet_pattern.search(text))
            
            # Additional fallback check for standalone squares if regex misses
            if not is_bullet and len(text) > 0 and text[0] in ['•', '●', '○', '■', '□', '▪', '▫', '►', '▸', '–', '-']:
                is_bullet = True
                
            is_list_item = (sem_type == "list_item") or is_bullet
                
            has_widget = False
            if node.visual and node.visual.get("widgets"):
                has_widget = True
                
            if is_list_item and not has_widget:
                # Potential list item
                if not current_list:
                    current_list.append(node)
                else:
                    # Check indentation and spacing
                    prev_node = current_list[-1]
                    prev_bbox = prev_node.geometry.get("bbox", [0,0,0,0])
                    curr_bbox = node.geometry.get("bbox", [0,0,0,0])
                    
                    # Same page
                    if node.page == prev_node.page:
                        # Similar indentation (within 10 points) or consecutive list items
                        if abs(curr_bbox[0] - prev_bbox[0]) < 10.0 or (sem_type == "list_item" and prev_node.semantic.get("type") == "list_item"):
                            current_list.append(node)
                        else:
                            lists_found.append(current_list)
                            current_list = [node]
                    else:
                        lists_found.append(current_list)
                        current_list = [node]
            else:
                if current_list:
                    lists_found.append(current_list)
                    current_list = []
                    
        if current_list:
            lists_found.append(current_list)
            
        for group in lists_found:
            stack = []
            created_l_nodes = []
            
            for item in group:
                x0 = item.geometry.get("bbox", [0,0,0,0])[0]
                
                if not stack:
                    first_ro = item.semantic.get("reading_order") or item.metadata.get("reading_order")
                    l_id = str(uuid.uuid4())
                    l_node = Node({
                        "id": f"{item.id}-L",
                        "uuid": l_id,
                        "content": "",
                        "page_number": item.page
                    })
                    l_node.semantic["type"] = "list"
                    if first_ro is not None:
                        l_node.semantic["reading_order"] = first_ro
                        l_node.metadata["reading_order"] = first_ro
                        
                    target_page = context.graph.pages.get(item.page)
                    if target_page and item in target_page.nodes:
                        idx = target_page.nodes.index(item)
                        target_page.nodes.insert(idx, l_node)
                        context.graph.node_map[l_node.uuid] = l_node
                    else:
                        context.graph.add_node(l_node)
                        
                    stack.append((x0, l_node))
                    created_l_nodes.append(l_node)
                else:
                    while len(stack) > 1 and x0 < stack[-1][0] - 10.0:
                        stack.pop()
                        
                    if x0 > stack[-1][0] + 10.0:
                        parent_l_node = stack[-1][1]
                        last_li_uuid = parent_l_node.relations.get("children", [])[-1] if parent_l_node.relations.get("children") else None
                        last_li_node = context.graph.get_node(last_li_uuid) if last_li_uuid else None
                        
                        l_id = str(uuid.uuid4())
                        new_l_node = Node({
                            "id": f"{item.id}-L{len(created_l_nodes)}",
                            "uuid": l_id,
                            "content": "",
                            "page_number": item.page
                        })
                        new_l_node.semantic["type"] = "list"
                        context.graph.add_node(new_l_node)
                        created_l_nodes.append(new_l_node)
                        
                        if last_li_node:
                            new_l_node.relations["parent"] = last_li_node.uuid
                            last_li_node.relations.setdefault("children", []).append(new_l_node.uuid)
                        else:
                            new_l_node.relations["parent"] = parent_l_node.uuid
                            parent_l_node.relations.setdefault("children", []).append(new_l_node.uuid)
                            
                        stack.append((x0, new_l_node))
                
                current_l_node = stack[-1][1]
                
                item.semantic["type"] = "list_item"
                action_queue.add_action(GraphAction(
                    action_type="UPDATE_SEMANTICS",
                    target_uuid=item.uuid,
                    payload={"new_type": "list_item"},
                    rule_name=self.name
                ))
                
                old_parent_uuid = item.relations.get("parent")
                if old_parent_uuid:
                    old_parent = context.graph.get_node(old_parent_uuid)
                    if old_parent and item.uuid in old_parent.relations.get("children", []):
                        old_parent.relations["children"].remove(item.uuid)
                        
                item.relations["parent"] = current_l_node.uuid
                current_l_node.relations.setdefault("children", []).append(item.uuid)
                
            for l_node in created_l_nodes:
                def get_all_item_bboxes(node_uuid):
                    bboxes = []
                    node = context.graph.get_node(node_uuid)
                    if not node: return bboxes
                    if node.semantic.get("type") == "list_item":
                        b = node.geometry.get("bbox")
                        if b and len(b) == 4: bboxes.append(b)
                    for c_uuid in node.relations.get("children", []):
                        bboxes.extend(get_all_item_bboxes(c_uuid))
                    return bboxes
                
                all_bboxes = get_all_item_bboxes(l_node.uuid)
                if all_bboxes:
                    min_x = min([b[0] for b in all_bboxes])
                    min_y = min([b[1] for b in all_bboxes])
                    max_x = max([b[2] for b in all_bboxes])
                    max_y = max([b[3] for b in all_bboxes])
                    l_node.geometry["bbox"] = [min_x, min_y, max_x, max_y]
                    
            action_queue.reports.append({
                "rule": self.name,
                "message": f"Grouped {len(group)} items into {len(created_l_nodes)} Bullet Lists (including nested)",
                "modified_nodes": [n.uuid for n in group] + [n.uuid for n in created_l_nodes]
            })
