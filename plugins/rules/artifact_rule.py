from plugins.rules.base_rule import BaseSemanticRule
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction

class ArtifactRule(BaseSemanticRule):
    name = "ArtifactRule"
    priority = 1
    
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        for node_id, node in context.graph.node_map.items():
            sem_type = node.semantic.get("type", "")
            
            # Identify elements that should be artifacts
            artifact_types = {"artifact", "header", "footer", "page_number", "decorative", "running_header", "running_footer", "watermark"}
            
            if sem_type.lower() in artifact_types:
                # If it's already an artifact, we still enforce it just to be safe
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
