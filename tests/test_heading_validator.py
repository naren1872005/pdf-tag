import unittest
import copy
from plugins.normalizers.heading_candidate_validator import HeadingCandidateValidator

class MockContext:
    pass

class TestHeadingCandidateValidator(unittest.TestCase):
    def setUp(self):
        self.validator = HeadingCandidateValidator()
        self.context = MockContext()
        
        # Base setup: a form-heavy page (Page 1) and a content-heavy page (Page 2)
        self.base_data = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {"id": 1, "type": "form_label", "content": "Date Initiated"},
                        {"id": 2, "type": "paragraph", "content": "21-Jan-00"},
                        {"id": 3, "type": "form_label", "content": "Code Assigned"},
                        {"id": 4, "type": "list_item", "content": "- Item 1"},
                        {"id": 5, "type": "artifact", "content": ""}
                    ]
                },
                {
                    "page_number": 2,
                    "elements": [
                        {"id": 10, "type": "paragraph", "content": "Some intro text"},
                        {"id": 11, "type": "paragraph", "content": "More text"}
                    ]
                }
            ]
        }
        
    def _run_validation(self, data):
        self.validator.run(self.context, data)

    def test_case_1_analytical_requirements_h3_form_page(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 100, 
            "type": "heading", 
            "heading_level": 3, 
            "content": "Analytical Requirements:",
            "bounding_box": [41, 309, 126, 319] # Area = 85 * 10 = 850
        }
        data["pages"][0]["elements"].insert(2, target) # Insert into form page
        
        # Add a large legitimate heading on page 2 to set median area
        data["pages"][1]["elements"].append({
            "id": 101, "type": "heading", "heading_level": 1, "content": "TITLE",
            "bounding_box": [50, 700, 300, 730] # Area = 250 * 30 = 7500
        })
        
        self._run_validation(data)
        
        # Should be reclassified to form_label
        self.assertEqual(target["type"], "form_label")
        self.assertNotIn("heading_level", target)

    def test_case_2_analytical_requirements_h4_form_page(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 100, 
            "type": "heading", 
            "heading_level": 4, 
            "content": "Analytical Requirements:",
            "bounding_box": [41, 309, 126, 319]
        }
        data["pages"][0]["elements"].insert(2, target)
        data["pages"][1]["elements"].append({
            "id": 101, "type": "heading", "heading_level": 1, "content": "TITLE",
            "bounding_box": [50, 700, 300, 730]
        })
        
        self._run_validation(data)
        self.assertEqual(target["type"], "form_label")

    def test_case_3_for_control_use_only_h3_form_page(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 100, 
            "type": "heading", 
            "heading_level": 3, 
            "content": "FOR CONTROL USE ONLY", # No colon!
            "bounding_box": [200, 250, 290, 260] # Area = 90 * 10 = 900
        }
        data["pages"][0]["elements"].insert(2, target)
        data["pages"][1]["elements"].append({
            "id": 101, "type": "heading", "heading_level": 1, "content": "TITLE",
            "bounding_box": [50, 700, 300, 730]
        })
        
        self._run_validation(data)
        
        # Form context (alone on page, surrounded by forms) + small bbox + H3
        # Score = 30 + 20 + 15 + 10 = 75 >= 50 -> form_label
        self.assertEqual(target["type"], "form_label")

    def test_case_4_creative_input_document_h1(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 101, 
            "type": "heading", 
            "heading_level": 1, 
            "content": "CREATIVE INPUT DOCUMENT",
            "bounding_box": [55, 711, 307, 734] # Area = 252 * 23 = ~5700
        }
        data["pages"][1]["elements"].insert(0, target) # Insert into content page
        
        self._run_validation(data)
        
        # Should remain heading H1
        self.assertEqual(target["type"], "heading")
        self.assertEqual(target["heading_level"], 1)

    def test_case_5_specifics_h2(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 102, 
            "type": "heading", 
            "heading_level": 2, 
            "content": "SPECIFICS",
            "bounding_box": [74, 682, 143, 693] # Area = 69 * 11 = ~759
        }
        data["pages"][1]["elements"].insert(1, target) # Content page
        
        # We need another heading to establish median area
        data["pages"][1]["elements"].insert(0, {
            "id": 101, "type": "heading", "heading_level": 1, "content": "TITLE",
            "bounding_box": [50, 700, 300, 730]
        })
        
        self._run_validation(data)
        
        # Should remain heading H2 (score: only small bbox=15, < 50)
        self.assertEqual(target["type"], "heading")
        self.assertEqual(target["heading_level"], 2)

    def test_case_6_legitimate_heading_with_colon(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 102, 
            "type": "heading", 
            "heading_level": 2, 
            "content": "Important Note:", # Colon
            "bounding_box": [50, 680, 200, 700] # Large area
        }
        data["pages"][1]["elements"].insert(1, target) # Content page, surrounded by P
        
        self._run_validation(data)
        
        # Has colon (+15), but no form context, no small bbox, not deep level -> Score 15 < 50
        self.assertEqual(target["type"], "heading")
        self.assertEqual(target["heading_level"], 2)

    def test_case_7_legitimate_h3_in_content(self):
        data = copy.deepcopy(self.base_data)
        target = {
            "id": 103, 
            "type": "heading", 
            "heading_level": 3, 
            "content": "Subsection 1.1.1",
            "bounding_box": [50, 600, 200, 615] 
        }
        data["pages"][1]["elements"].insert(1, target) # Content page, surrounded by P
        
        self._run_validation(data)
        
        # H3 (+10), maybe small bbox (+15) -> Score 25 < 50
        self.assertEqual(target["type"], "heading")
        self.assertEqual(target["heading_level"], 3)

    def test_case_8_legitimate_heading_on_page_1_non_form(self):
        data = {
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {"id": 1, "type": "paragraph", "content": "Intro"},
                        {
                            "id": 2, 
                            "type": "heading", 
                            "heading_level": 1, 
                            "content": "DOCUMENT TITLE",
                            "bounding_box": [50, 700, 300, 730]
                        },
                        {"id": 3, "type": "paragraph", "content": "Body text"}
                    ]
                }
            ]
        }
        target = data["pages"][0]["elements"][1]
        
        self._run_validation(data)
        
        # Page 1 but not a form page -> Score 0
        self.assertEqual(target["type"], "heading")
        self.assertEqual(target["heading_level"], 1)

if __name__ == '__main__':
    unittest.main()
