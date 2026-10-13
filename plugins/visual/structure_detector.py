from typing import List, Dict, Set
from models.observation import Observation
from models.structure import Structure
import uuid

class StructureDetector:
    """
    Internal service for the RuleEngine.
    Groups intersecting horizontal and vertical observations into Structures.
    Extracts geometric features without semantic interpretation.
    """
    def __init__(self, config=None):
        self.config = config or {}
        
    def _do_intersect(self, bbox1: List[float], bbox2: List[float], tol: float = 2.0) -> bool:
        """Checks if two bounding boxes intersect (with a small tolerance)."""
        x_left = max(bbox1[0] - tol, bbox2[0] - tol)
        y_top = max(bbox1[1] - tol, bbox2[1] - tol)
        x_right = min(bbox1[2] + tol, bbox2[2] + tol)
        y_bottom = min(bbox1[3] + tol, bbox2[3] + tol)

        return x_right >= x_left and y_bottom >= y_top

    def detect(self, observations: List[Observation], page: int) -> List[Structure]:
        """
        Takes raw observations for a page and groups intersecting ones into Structures.
        """
        # Treat legacy types as well
        lines = [obs for obs in observations if getattr(obs, "type", getattr(obs, "object_type", "")) in ("horizontal_line", "vertical_line")]
        
        # Build adjacency list for intersections
        adj: Dict[str, List[str]] = {obs.id: [] for obs in lines}
        obs_map: Dict[str, Observation] = {obs.id: obs for obs in lines}
        
        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                if self._do_intersect(lines[i].bbox, lines[j].bbox, tol=5.0):
                    adj[lines[i].id].append(lines[j].id)
                    adj[lines[j].id].append(lines[i].id)
                    
        # Find connected components
        visited: Set[str] = set()
        structures: List[Structure] = []
        
        for obs_id in adj:
            if obs_id not in visited:
                comp = []
                stack = [obs_id]
                while stack:
                    curr = stack.pop()
                    if curr not in visited:
                        visited.add(curr)
                        comp.append(curr)
                        stack.extend(adj[curr])
                        
                # Only create a structure if there's more than one line (an intersection)
                if len(comp) > 1:
                    struct_obs = [obs_map[c] for c in comp]
                    
                    # Calculate bounding box of the structure
                    min_x = min(o.bbox[0] for o in struct_obs)
                    min_y = min(o.bbox[1] for o in struct_obs)
                    max_x = max(o.bbox[2] for o in struct_obs)
                    max_y = max(o.bbox[3] for o in struct_obs)
                    
                    # Extract geometric features
                    h_lines = sum(1 for o in struct_obs if getattr(o, "type", getattr(o, "object_type", "")) == "horizontal_line")
                    v_lines = sum(1 for o in struct_obs if getattr(o, "type", getattr(o, "object_type", "")) == "vertical_line")
                    
                    # Count actual intersection points
                    intersections = 0
                    for i in range(len(struct_obs)):
                        for j in range(i + 1, len(struct_obs)):
                            type_i = getattr(struct_obs[i], "type", getattr(struct_obs[i], "object_type", ""))
                            type_j = getattr(struct_obs[j], "type", getattr(struct_obs[j], "object_type", ""))
                            if type_i != type_j:
                                if self._do_intersect(struct_obs[i].bbox, struct_obs[j].bbox, tol=5.0):
                                    intersections += 1
                                    
                    features = {
                        "horizontal_lines": h_lines,
                        "vertical_lines": v_lines,
                        "intersections": intersections,
                        "connected_components": 1,
                        "closed": False # Could implement a proper cycle check here if needed
                    }
                    
                    structure = Structure(
                        id=str(uuid.uuid4()),
                        page=page,
                        bbox=[min_x, min_y, max_x, max_y],
                        member_observation_ids=comp,
                        features=features
                    )
                    structures.append(structure)
                    
        return structures
