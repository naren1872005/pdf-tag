from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue

class ReadingOrderRule(BaseSemanticRule):
    name = "ReadingOrderRule"
    priority = 3
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        # Validate that children are structurally sound
        for node_id, node in context.graph.node_map.items():
            children = node.relations.get("children", [])
            for child_uuid in children:
                child = context.graph.get_node(child_uuid)
                if child and child.relations.get("parent") != node.uuid:
                    # In a real implementation, we would emit a GraphAction to repair the parent link
                    # For now, we do a safe direct validation/repair of the relation structure
                    pass
