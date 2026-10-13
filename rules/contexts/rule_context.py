from typing import Dict, Any
from graph.node import Node
from engine.document_context import DocumentContext

class RuleContext:
    """
    Context passed to every rule plugin.
    Provides targeted access to the current node, its neighbors, and document-wide stats.
    """
    def __init__(self, doc_context: DocumentContext, current_node: Node, config: Dict[str, Any]):
        self.doc_context = doc_context
        self.node = current_node
        self.config = config
        
    @property
    def neighbors(self) -> Dict[str, Node]:
        return self.node.neighbors
