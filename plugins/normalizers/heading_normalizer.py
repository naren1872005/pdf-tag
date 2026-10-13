from plugins.base_plugin import BasePlugin

class HeadingNormalizer(BasePlugin):
    """
    Normalizes heading levels.
    Ensures that only one H1 exists per document.
    Any subsequent H1s are converted to H2.
    """
    def __init__(self, config=None):
        super().__init__(config)
    
    def run(self, context, raw_data):
        h1_found = False
        
        for page in raw_data.get("pages", []):
            for el in page.get("elements", []):
                if el.get("type") or el.get("semantic", {}).get("type") == "heading":
                    level = el.get("heading_level") or el.get("semantic", {}).get("heading_level")
                    if level == 1:
                        if not h1_found:
                            h1_found = True
                        else:
                            # Downgrade subsequent H1s to H2
                            if "semantic" in el:
                                el["semantic"]["heading_level"] = 2
                            else:
                                el["heading_level"] = 2
                            # Log the change
                            if "warnings" not in context.metadata:
                                context.metadata["warnings"] = []
                            context.metadata["warnings"].append(
                                f"HeadingNormalizer: Downgraded additional H1 '{el.get('content')}' on page {page.get('page_number')} to H2."
                            )
