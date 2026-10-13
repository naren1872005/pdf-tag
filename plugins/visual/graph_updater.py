from typing import List
from graph.document_graph import DocumentGraph
from models.visual_object import VisualObject
from models.visual_region import VisualRegion

class GraphUpdater:
    """
    The ONLY component allowed to mutate the DocumentGraph during Visual Analysis.
    Constructs deterministic graph relationships (controls, fills, belongs_to) 
    between OCR nodes and finalized VisualObjects.
    """
    def __init__(self, config=None):
        self.config = config or {}

    def apply(self, graph: DocumentGraph, regions: List[VisualRegion]):
        """
        Builds relationships between nodes and visual objects within each region.
        """
        for region in regions:
            for v_obj in region.visual_objects:
                target_node_uuid = v_obj.owner_node_uuid
                
                # Deterministic logic to find the owning node if none exists
                if not target_node_uuid:
                    closest_node = None
                    min_dist = float('inf')
                    
                    v_cx = (v_obj.bbox[0] + v_obj.bbox[2]) / 2
                    v_cy = (v_obj.bbox[1] + v_obj.bbox[3]) / 2
                    
                    for node_data in region.nodes:
                        n_bbox = node_data.geometry.get("bbox")
                        if not n_bbox: continue
                        
                        n_cx = (n_bbox[0] + n_bbox[2]) / 2
                        n_cy = (n_bbox[1] + n_bbox[3]) / 2
                        
                        # Simple euclidean distance inside the localized region
                        dist = ((v_cx - n_cx)**2 + (v_cy - n_cy)**2)**0.5
                        
                        # Heuristics for relationships
                        if v_obj.type in ["underline", "underline_field"]:
                            if abs(v_cy - n_cy) > 20: 
                                dist += 1000 # Penalize if not on same horizontal line
                            if v_cx < n_cx:
                                dist += 500 # Penalize if underline is to the left of text
                                
                        if dist < min_dist:
                            min_dist = dist
                            closest_node = node_data
                            
                    # Connect to closest text node if within reasonable distance
                    if closest_node and min_dist < 200:
                        target_node_uuid = closest_node.uuid
                        
                # Update the graph
                if target_node_uuid:
                    target_node = graph.get_node(target_node_uuid)
                    if target_node:
                        # Append the visual widget
                        target_node.visual.setdefault("widgets", []).append(v_obj.to_dict())
                        
                        # Add deterministic relationship edge
                        rel_type = "controls" if v_obj.type in ["checkbox", "radio_button"] else "fills"
                        # Assumes graph node supports add_relationship (this may need adaptation based on actual DocumentGraph)
                        if hasattr(target_node, 'add_relationship'):
                            target_node.add_relationship(rel_type, v_obj.id)
                        
                        target_node.add_history(f"Visual Analysis: Attached {v_obj.type} via {rel_type} relationship.")
                else:
                    # If it has no text node (e.g., empty form field with no label)
                    # We add the visual object itself as a standalone node in the graph
                    # Note: Assumes graph.add_node exists and handles these args. 
                    # If not, this is the architectural intent for Phase 1.
                    if hasattr(graph, 'add_node'):
                        from graph.node import Node
                        import uuid
                        raw_data = {
                            "id": f"visual_{uuid.uuid4().hex[:8]}",
                            "uuid": str(uuid.uuid4()),
                            "type": "visual_widget",
                            "semantic_type": "visual_widget",
                            "content": "",
                            "page_number": v_obj.page,
                            "bounding_box": v_obj.bbox,
                            "metadata": v_obj.to_dict()
                        }
                        new_node = Node(raw_data)
                        graph.add_node(new_node)
                        new_node.add_history(f"Visual Analysis: Created standalone {v_obj.type} node.")
