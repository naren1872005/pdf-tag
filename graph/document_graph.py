import json
from typing import Dict, List, Any
from graph.node import Node
from graph.page import Page

class DocumentGraph:
    """
    The master Document Graph that the Rule Engine and all plugins operate on.
    """
    def __init__(self):
        self.pages: Dict[int, Page] = {}
        self.node_map: Dict[str, Node] = {} # UUID to Node mapping
        
    def add_node(self, node: Node):
        if node.page not in self.pages:
            self.pages[node.page] = Page(node.page)
        self.pages[node.page].add_node(node)
        self.node_map[node.uuid] = node
        
    def get_node(self, uuid: str) -> Node:
        return self.node_map.get(uuid)
        
    def remove_node(self, uuid: str):
        if uuid in self.node_map:
            node = self.node_map[uuid]
            if node.page in self.pages:
                self.pages[node.page].remove_node(uuid)
            del self.node_map[uuid]
        
    def get_all_nodes(self) -> List[Node]:
        return list(self.node_map.values())
        
    @classmethod
    def from_json(cls, json_data: Dict[str, Any]) -> 'DocumentGraph':
        graph = cls()
        
        # Pass 1: Create all nodes recursively
        def parse_elements(elements_list, parent_uuid=None):
            for el_data in elements_list:
                node = Node(el_data)
                # If it had a parent passed implicitly from nesting
                if parent_uuid:
                    node.relations["parent"] = parent_uuid
                graph.add_node(node)
                if el_data.get("kids"):
                    parse_elements(el_data["kids"], node.uuid)
                    
        for page_data in json_data.get('pages', []):
            elements = page_data.get('nodes') or page_data.get('elements', [])
            parse_elements(elements)
                
        # Pass 2: Reconstruct Hierarchy (bidirectional links)
        for node in graph.get_all_nodes():
            raw_parent = node.relations.get("parent")
            if raw_parent:
                parent = graph.get_node(raw_parent)
                if parent and node.uuid not in parent.relations["children"]:
                    parent.relations["children"].append(node.uuid)
                        
        return graph

    def to_json(self) -> Dict[str, Any]:
        doc_dict = {"pages": []}
        for page_num in sorted(self.pages.keys()):
            doc_dict["pages"].append(self.pages[page_num].to_dict())
        return doc_dict
