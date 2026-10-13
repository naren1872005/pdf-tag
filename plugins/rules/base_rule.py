from typing import Any
from engine.document_context import DocumentContext
from plugins.rules.action_queue import ActionQueue

class BaseSemanticRule:
    """
    Base class for semantic validation and refinement rules.
    Rules must evaluate the DocumentGraph and emit GraphActions to the ActionQueue.
    They must never modify the graph directly.
    """
    name = "BaseSemanticRule"
    priority = 999
    
    def __init__(self, config=None):
        self.config = config or {}
        
    def run(self, context: DocumentContext, action_queue: ActionQueue):
        """
        Evaluate the graph and emit actions to the action_queue.
        """
        raise NotImplementedError("Rules must implement run()")
