import uuid
from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue
from graph.node import Node

class FormObjectReferenceRule(BaseSemanticRule):
    """
    Priority 90 Rule.
    Associates every interactive AcroForm widget semantic node with a <Form> element.
    Groups multiple controls under the same parent into a single <Form> node.
    """
    name = "FormObjectReferenceRule"
    priority = 90
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        interactive_types = {
            "text_field", "checkbox", "radio_button", 
            "combo_box", "list_box", "push_button", "signature"
        }
        
        # Group interactive controls by their parent UUID
        controls_by_parent = {}
        nodes = list(context.graph.node_map.values())
        
        for node in nodes:
            sem_type = node.semantic.get("type", "")
            if sem_type in interactive_types or getattr(node, "annotation_id", None) or node.metadata.get("is_interactive"):
                # Determine the correct parent container for the Form tag
                if sem_type in ["table_data_cell", "paragraph", "list_item", "heading"]:
                    # The node itself is the container
                    target_parent_uuid = node.uuid
                else:
                    # The node is a control inside a container
                    target_parent_uuid = node.relations.get("parent")
                    
                if target_parent_uuid:
                    controls_by_parent.setdefault(target_parent_uuid, []).append(node)
                    
        for parent_uuid, controls in controls_by_parent.items():
            parent_node = context.graph.get_node(parent_uuid)
            if not parent_node:
                continue
                
            # Create a single Form node for this parent
            form_id = str(uuid.uuid4())
            form_node = Node({
                "id": form_id,
                "uuid": form_id,
                "content": "",
                "page_number": getattr(parent_node, "page", None) or parent_node.metadata.get("page")
            })
            form_node.semantic["type"] = "form"
            
            # Store the semantic IDs of the controls so the exporter can resolve them to widgets
            form_node.semantic["control_node_ids"] = [c.uuid for c in controls]
            
            # Use parent's geometry roughly
            form_node.geometry["bbox"] = parent_node.geometry.get("bbox", [0,0,0,0])
            
            # Attach to graph and set relationships
            context.graph.add_node(form_node)
            form_node.relations["parent"] = parent_uuid
            
            # Append Form node at the END of parent's children to preserve reading order (after any text)
            parent_node.relations.setdefault("children", []).append(form_id)
            
            parent_node.add_history(f"{self.name}: Added Form wrapper for {len(controls)} controls")
            
            action_queue.reports.append({
                "rule": self.name,
                "message": f"Added Form wrapper for {len(controls)} interactive controls under {parent_uuid}",
                "modified_nodes": [parent_uuid, form_id] + [c.uuid for c in controls]
            })
