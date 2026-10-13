from typing import List, Dict, Any
from graph.node import Node

class Page:
    def __init__(self, page_number: int):
        self.page_number = page_number
        self.nodes: List[Node] = []
        
    def add_node(self, node: Node):
        self.nodes.append(node)
        
    def remove_node(self, uuid: str):
        self.nodes = [n for n in self.nodes if n.uuid != uuid]
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "nodes": [node.to_dict() for node in self.nodes]
        }
