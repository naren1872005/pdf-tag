from typing import List
from plugins.visual.rule_engine import BaseRule, RuleContext, RuleResult
from models.evidence import EvidenceStatus, Evidence
import uuid

class BoxRule(BaseRule):
    """
    Priority 2 Rule.
    Consumes Structures and evaluates their geometric features.
    If a structure is a simple box, evaluates nearby text to determine if it's an input_box or signature_box.
    """
    name = "BoxRule"
    priority = 2

    def evaluate(self, context: RuleContext) -> List[RuleResult]:
        results = []
        evidence_map = {ev.id: ev for ev in context.region.evidence}
        for struct in context.structures:
            active_members = [m for m in struct.member_observation_ids if evidence_map.get(m) and evidence_map[m].status == EvidenceStatus.ACTIVE]
            if not active_members:
                continue # Already claimed by a higher-priority rule like TableRule
                
            features = struct.features
            h_lines = features.get("horizontal_lines", 0)
            v_lines = features.get("vertical_lines", 0)
            intersections = features.get("intersections", 0)
            
            # A box is typically 2 horizontal lines, 2 vertical lines, and 4 intersections
            # We allow up to 3 to account for slightly misaligned detections or a split line
            if 0 < h_lines <= 3 and 0 < v_lines <= 3 and 0 < intersections <= 6:
                obj_type = "input_box"
                
                struct_bbox = struct.bbox
                for node in getattr(context.region, "nodes", []):
                    text = getattr(node, "text", "")
                    if not text:
                        continue
                    text_lower = text.lower()
                    if "sign" in text_lower or "signature" in text_lower:
                        node_bbox = getattr(node, "bbox", [0,0,0,0])
                        # Simple proximity check
                        is_near = False
                        # Above or slightly inside
                        if (node_bbox[3] <= struct_bbox[1] + 30 and node_bbox[2] >= struct_bbox[0] - 20 and node_bbox[0] <= struct_bbox[2] + 20):
                            is_near = True
                        # Inside
                        elif (node_bbox[0] >= struct_bbox[0] and node_bbox[1] >= struct_bbox[1] and node_bbox[3] <= struct_bbox[3]):
                            is_near = True
                        # Left
                        elif (node_bbox[2] <= struct_bbox[0] + 10 and node_bbox[3] >= struct_bbox[1] and node_bbox[1] <= struct_bbox[3]):
                            is_near = True
                            
                        if is_near:
                            obj_type = "signature_box"
                            break
                            
                box_ev = Evidence(
                    source="box_rule",
                    object_type=obj_type,
                    bbox=struct.bbox,
                    page=struct.page,
                    confidence=1.0
                )
                box_ev.id = str(uuid.uuid4())
                box_ev.history.append(f"Classified by BoxRule. h={h_lines}, v={v_lines}, x={intersections}")
                context.region.evidence.append(box_ev)
                
                for obs_id in active_members:
                    results.append(
                        RuleResult(
                            rule_name=self.name,
                            target_evidence_id=obs_id,
                            confidence_delta=-1.0,
                            status_update=EvidenceStatus.SUPPRESSED,
                            reason=f"Member of {obj_type}."
                        )
                    )
                    
        return results
