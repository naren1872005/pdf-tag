import statistics
from typing import Dict, List, Any
from plugins.base_plugin import BasePlugin

class HeadingCandidateValidator(BasePlugin):
    """
    Validates Gemini 'heading' candidates to prevent form labels and administrative text
    from being incorrectly classified as structural document headings.
    
    Uses a multi-signal evidence scoring model. No single signal can reject a heading.
    """
    
    # Heuristic Thresholds (Configuration)
    FORM_DENSITY_THRESHOLD = 0.25      # Ratio of form-related elements on a page
    BBOX_AREA_RATIO_THRESHOLD = 0.25   # Candidate area compared to median heading area
    RECLASSIFICATION_THRESHOLD = 50    # Minimum score required to reclassify
    
    # Evidence Scores
    SCORE_FORM_CONTEXT = 30
    SCORE_NEIGHBOR_CONTEXT = 20
    SCORE_LABEL_LIKE = 15
    SCORE_SMALL_BBOX = 15
    SCORE_DEEP_LEVEL = 10
    
    FORM_RELATED_TYPES = {"form_label", "list_item", "artifact", "table"}

    def run(self, context, raw_data: Dict[str, Any]):
        median_area = self._calculate_median_heading_area(raw_data)
        
        for page in raw_data.get("pages", []):
            page_elements = page.get("elements", [])
            form_density = self._calculate_page_form_density(page_elements)
            
            # Identify all headings on this page for context
            page_headings = [e for e in page_elements if e.get("type") or e.get("semantic", {}).get("type") == "heading"]
            
            for i, el in enumerate(page_elements):
                if el.get("type") or el.get("semantic", {}).get("type") == "heading":
                    self._validate_candidate(
                        el, i, page_elements, form_density, median_area, len(page_headings)
                    )

    def _calculate_median_heading_area(self, raw_data: Dict[str, Any]) -> float:
        areas = []
        for page in raw_data.get("pages", []):
            for el in page.get("elements", []):
                if el.get("type") or el.get("semantic", {}).get("type") == "heading":
                    bbox = el.get("bounding_box") or el.get("geometry", {}).get("bbox")
                    if bbox and len(bbox) == 4:
                        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                        areas.append(area)
        
        if not areas:
            return 0.0
        return statistics.median(areas)
        
    def _calculate_page_form_density(self, elements: List[Dict[str, Any]]) -> float:
        if not elements:
            return 0.0
        
        form_related_count = sum(1 for e in elements if e.get("type") or e.get("semantic", {}).get("type") in self.FORM_RELATED_TYPES)
        return form_related_count / len(elements)
        
    def _get_neighbors(self, index: int, elements: List[Dict[str, Any]], window: int = 3) -> List[Dict[str, Any]]:
        start = max(0, index - window)
        end = min(len(elements), index + window + 1)
        neighbors = []
        for i in range(start, end):
            if i != index:
                neighbors.append(elements[i])
        return neighbors

    def _validate_candidate(
        self, 
        el: Dict[str, Any], 
        index: int, 
        page_elements: List[Dict[str, Any]], 
        form_density: float, 
        median_area: float,
        page_heading_count: int
    ):
        score = 0
        signals = {}
        
        content = (el.get("content") or el.get("text") or "").strip()
        level = el.get("heading_level") or el.get("semantic", {}).get("heading_level")
        bbox = el.get("bounding_box") or el.get("geometry", {}).get("bbox")
        
        # Signal A: Form Context
        if form_density >= self.FORM_DENSITY_THRESHOLD:
            # If it's the ONLY heading on a form-heavy page, it's highly suspicious
            if page_heading_count == 1:
                score += self.SCORE_FORM_CONTEXT
                signals["FormContext"] = f"density={form_density:.2f}, sole heading"
            else:
                score += (self.SCORE_FORM_CONTEXT // 2)
                signals["FormContext"] = f"density={form_density:.2f}"
                
        # Signal B: Neighbor Context
        neighbors = self._get_neighbors(index, page_elements)
        if neighbors:
            form_neighbors = sum(1 for n in neighbors if n.get("type") or n.get("semantic", {}).get("type") in self.FORM_RELATED_TYPES)
            heading_neighbors = sum(1 for n in neighbors if n.get("type") or n.get("semantic", {}).get("type") == "heading")
            if form_neighbors >= len(neighbors) / 2 and heading_neighbors == 0:
                score += self.SCORE_NEIGHBOR_CONTEXT
                signals["NeighborContext"] = f"form_neighbors={form_neighbors}/{len(neighbors)}"
                
        # Signal C: Label-like Content
        word_count = len(content.split())
        if content.endswith(":") and word_count <= 5:
            score += self.SCORE_LABEL_LIKE
            signals["LabelLike"] = f"ends_with_colon, words={word_count}"
            
        # Signal D: Visual Prominence
        if bbox and len(bbox) == 4 and median_area > 0:
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area < median_area * self.BBOX_AREA_RATIO_THRESHOLD:
                score += self.SCORE_SMALL_BBOX
                signals["SmallBBox"] = f"area={area:.1f}, median={median_area:.1f}"
                
        # Signal E: Heading Level
        if isinstance(level, int) and level >= 3:
            score += self.SCORE_DEEP_LEVEL
            signals["DeepLevel"] = f"level={level}"
            
        # Decision
        if score >= self.RECLASSIFICATION_THRESHOLD:
            original_type = el.get("type") or el.get("semantic", {}).get("type")
            original_level = level
            
            if "semantic" in el:
                el["semantic"]["type"] = "form_label"
            else:
                el["type"] = "form_label"
            if "heading_level" in el:
                del el["heading_level"]
                
            print(f"HeadingCandidateValidator: REJECTED candidate '{content}'")
            print(f"  Original: type={original_type}, level={original_level}")
            print(f"  Score: {score} >= {self.RECLASSIFICATION_THRESHOLD}")
            for k, v in signals.items():
                print(f"  Signal {k}: {v}")
            print(f"  Result: type=form_label\n")
        else:
            if score > 0:
                print(f"HeadingCandidateValidator: PASSED candidate '{content}' (score {score})")
