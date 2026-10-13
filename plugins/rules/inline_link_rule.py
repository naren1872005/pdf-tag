import re
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue, GraphAction
from plugins.rules.base_rule import BaseSemanticRule

class InlineLinkRule(BaseSemanticRule):
    name = "InlineLinkRule"
    priority = 85 # Run after basic semantics, before export
    
    URL_REGEX = re.compile(
        r'(?P<url>https?://[^\s/$.?#].[^\s]*|www\.[^\s/$.?#].[^\s]*)|(?P<email>[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        re.IGNORECASE
    )

    def run(self, context: DocumentContext, action_queue: ActionQueue):
        for node in context.graph.get_all_nodes():
            if node.semantic.get("type") == "hyperlink":
                continue # Already a hyperlink, skip
            if node.relations.get("children"):
                continue # Has children, not a simple leaf text block
                
            text = node.text
            if not text:
                continue
                
            existing_links = node.analysis.get("inline_links", [])
                
            links = []
            for match in self.URL_REGEX.finditer(text):
                url = match.group("url")
                email = match.group("email")
                
                target_url = None
                matched_text = match.group(0)
                
                if url:
                    if url.lower().startswith("www."):
                        target_url = "https://" + url
                    else:
                        target_url = url
                elif email:
                    target_url = "mailto:" + email
                    
                if target_url:
                    # Check if already processed by AnnotationDetector
                    is_duplicate = False
                    for elink in existing_links:
                        if elink.get("target_url") == target_url or (elink.get("start_offset") <= match.start() and elink.get("end_offset") >= match.end()):
                            is_duplicate = True
                            break
                            
                    if not is_duplicate:
                        links.append({
                        "start_offset": match.start(),
                        "end_offset": match.end(),
                        "text": matched_text,
                        "target_url": target_url
                    })
                    
            if links:
                action_queue.add_action(
                    GraphAction(
                        action_type="ADD_INLINE_LINKS",
                        target_uuid=node.uuid,
                        payload={"links": links},
                        rule_name=self.name
                    )
                )
