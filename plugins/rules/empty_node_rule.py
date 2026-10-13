from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction

class EmptyNodeRule(BaseSemanticRule):
    name = "EmptyNodeRule"
    priority = 9
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        nodes = list(context.graph.node_map.values())
        
        for node in nodes:
            sem_type = node.semantic.get("type", "")
            
            # Inspect only specific container types
            # Note: Do not inspect figure_caption or table_caption per requirements
            if sem_type in ("heading", "paragraph"):
                has_text = bool(node.text and node.text.strip())
                children = node.relations.get("children", [])
                
                # Check for widgets/visuals (if applicable)
                has_widget = False
                if node.visual and node.visual.get("widgets"):
                    has_widget = True
                    
                # If truly empty
                if not has_text and not children and not has_widget:
                    action_queue.add_action(
                        GraphAction(
                            action_type="UPDATE_SEMANTICS",
                            target_uuid=node.uuid,
                            payload={"new_type": "artifact"},
                            rule_name=self.name
                        )
                    )
                    action_queue.add_action(
                        GraphAction(
                            action_type="UPDATE_TAG",
                            target_uuid=node.uuid,
                            payload={"new_tag": "Artifact"},
                            rule_name=self.name
                        )
                    )
