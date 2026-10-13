from typing import List
from plugins.visual.rule_engine import BaseRule, RuleContext, RuleResult
from models.evidence import EvidenceStatus, Evidence
import uuid

class UnderlineRule(BaseRule):
    """
    Priority 4 Rule.
    Consumes isolated horizontal lines that haven't been claimed by structures.
    Rejects decorative separators based on length.
    Produces form_line evidence for valid form fields.
    """
    name = "UnderlineRule"
    priority = 4

    def evaluate(self, context: RuleContext) -> List[RuleResult]:
        results = []
        
        config = context.config.get("rules", {}).get("underline", {})
        max_page_width_ratio = config.get("max_page_width_ratio", 0.8)
        
        # Calculate page width if possible, or use a heuristic.
        max_x = max([getattr(node, "bbox", [0, 0, 0, 0])[2] for node in getattr(context.region, "nodes", [])], default=800.0)
        page_width = max(max_x, 800.0)
        
        for ev in context.region.evidence:
            if ev.status != EvidenceStatus.ACTIVE:
                continue
            
            if ev.object_type == "horizontal_line":
                line_width = ev.bbox[2] - ev.bbox[0]
                
                # Check for decorative page separator
                if line_width > (page_width * max_page_width_ratio):
                    results.append(
                        RuleResult(
                            rule_name=self.name,
                            target_evidence_id=ev.id,
                            confidence_delta=-1.0,
                            status_update=EvidenceStatus.SUPPRESSED,
                            reason=f"Line spans > {max_page_width_ratio * 100}% of the page width, likely a decorative separator."
                        )
                    )
                    continue
                
                # Check for adjacent label
                has_label = False
                for node in getattr(context.region, "nodes", []):
                    node_bbox = getattr(node, "bbox", [0, 0, 0, 0])
                    # Node is to the left of the line (with some padding)
                    is_left = node_bbox[2] <= ev.bbox[0] + 50 and abs(node_bbox[3] - ev.bbox[3]) < 20
                    # Node is above the line
                    is_above = node_bbox[3] <= ev.bbox[1] + 10 and node_bbox[0] <= ev.bbox[2] and node_bbox[2] >= ev.bbox[0]
                    
                    if is_left or is_above:
                        has_label = True
                        break
                        
                # Confidence adjustment based on label adjacency
                new_confidence = 1.0 if has_label else 0.4
                
                # If it's a valid standalone line, promote it to form_line
                form_ev = Evidence(
                    source="underline_rule",
                    object_type="form_line",
                    bbox=ev.bbox,
                    page=ev.page,
                    confidence=new_confidence
                )
                form_ev.id = str(uuid.uuid4())
                reason = "with adjacent label" if has_label else "without adjacent label (confidence penalty)"
                form_ev.history.append(f"Promoted to form_line by UnderlineRule {reason}.")
                context.region.evidence.append(form_ev)
                
                # Suppress the original horizontal_line observation
                results.append(
                    RuleResult(
                        rule_name=self.name,
                        target_evidence_id=ev.id,
                        confidence_delta=-1.0,
                        status_update=EvidenceStatus.SUPPRESSED,
                        reason="Claimed by UnderlineRule."
                    )
                )
            
            elif ev.object_type == "vertical_line":
                results.append(
                    RuleResult(
                        rule_name=self.name,
                        target_evidence_id=ev.id,
                        confidence_delta=-1.0,
                        status_update=EvidenceStatus.SUPPRESSED,
                        reason="Unclaimed isolated vertical line suppressed."
                    )
                )

        return results
