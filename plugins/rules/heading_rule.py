from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction

class HeadingHierarchyRule(BaseSemanticRule):
    name = "HeadingHierarchyRule"
    priority = 2
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        # Flatten reading order to check hierarchy
        ordered_nodes = []
        for page_num in sorted(context.graph.pages.keys()):
            page = context.graph.pages[page_num]
            ordered_nodes.extend(sorted(page.nodes, key=lambda n: n.reading_order if hasattr(n, 'reading_order') and n.reading_order is not None else 999999))
            
        current_level = 0
        h1_count = 0
        
        for node in ordered_nodes:
            sem_type = node.semantic.get("type", "")
            if sem_type == "heading":
                import re
                c_text = str(getattr(node, "text", "") or node.semantic.get("content", "")).strip()
                m_sec = re.match(r'^\s*(?:(?:chapter|section)\s+)?(\d+(?:\.\d+)+)\.?\s*(\S.*)?$', c_text, re.IGNORECASE)
                if m_sec:
                    parts = m_sec.group(1).split('.')
                    level = min(6, len(parts))
                    node.semantic["heading_level"] = level
                else:
                    level = node.semantic.get("heading_level", 1)
                
                # Check 0: The first heading in the document MUST be H1
                if current_level == 0 and level != 1:
                    level = 1
                    action_queue.add_action(
                        GraphAction(
                            action_type="UPDATE_TAG",
                            target_uuid=node.uuid,
                            payload={"new_tag": f"H{level}"},
                            rule_name=self.name
                        )
                    )
                    node.semantic["heading_level"] = level
                
                # Check 1: Only one H1 per document (or at least track it)
                if level == 1:
                    h1_count += 1
                    if h1_count > 1:
                        # Demote to H2
                        level = 2
                        action_queue.add_action(
                            GraphAction(
                                action_type="UPDATE_TAG",
                                target_uuid=node.uuid,
                                payload={"new_tag": f"H{level}"},
                                rule_name=self.name
                            )
                        )
                        # We don't change the semantic type string, just the tag, or we update the heading_level
                        node.semantic["heading_level"] = level
                        
                # Check 2: Missing heading levels (e.g. H1 followed by H3)
                if level > current_level + 1 and current_level != 0:
                    # Fix hierarchy gap by promoting it
                    level = current_level + 1
                    action_queue.add_action(
                        GraphAction(
                            action_type="UPDATE_TAG",
                            target_uuid=node.uuid,
                            payload={"new_tag": f"H{level}"},
                            rule_name=self.name
                        )
                    )
                    node.semantic["heading_level"] = level
                    
                current_level = level
                
        # Fallback: If no headings were found in the ENTIRE document,
        # we must promote the very first text element to be an H1 to satisfy PDF/UA
        if h1_count == 0 and ordered_nodes:
            for node in ordered_nodes:
                sem_type = node.semantic.get("type", "")
                if sem_type in ("text", "paragraph"):
                    node.semantic["type"] = "heading"
                    node.semantic["heading_level"] = 1
                    action_queue.add_action(
                        GraphAction(
                            action_type="UPDATE_TAG",
                            target_uuid=node.uuid,
                            payload={"new_tag": "H1"},
                            rule_name=self.name
                        )
                    )
                    break
