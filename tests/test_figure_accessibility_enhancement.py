import os
import unittest
import fitz
import pikepdf
from pikepdf import Name
import numpy as np

from models.figure_element import FigureExtractedContent
from ocr.paddle_engine import PaddleLayoutExtractor
from core.remediator import TaggingService
from export.semantic_normalizer import SemanticNormalizer


class TestFigureAccessibilityEnhancement(unittest.TestCase):
    """
    Test suite verifying embedded figure content extraction,
    accessibility tag generation, reading order preservation,
    and PDF structure tree validity across 10 distinct scenarios.
    """

    def setUp(self):
        self.temp_dir = os.path.join(os.path.dirname(__file__), "temp_fig_test")
        os.makedirs(self.temp_dir, exist_ok=True)

    def tearDown(self):
        # Clean up temporary test files
        if os.path.exists(self.temp_dir):
            for f in os.listdir(self.temp_dir):
                try:
                    os.remove(os.path.join(self.temp_dir, f))
                except Exception:
                    pass
            try:
                os.rmdir(self.temp_dir)
            except Exception:
                pass

    def test_scenario_1_normal_text_pdf_without_figures(self):
        """Scenario 1: A normal text-based PDF without figures is preserved cleanly."""
        pdf_path = os.path.join(self.temp_dir, "scenario1_no_figures.pdf")
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((50, 80), "Document Title", fontsize=18)
        page.insert_text((50, 120), "This is the first paragraph of the test document.", fontsize=11)
        page.insert_text((50, 150), "This is the second paragraph following naturally.", fontsize=11)
        doc.save(pdf_path)
        doc.close()

        # Run extraction
        extracted = PaddleLayoutExtractor.extract_document(pdf_path)
        self.assertIn("pages", extracted)
        self.assertEqual(len(extracted["pages"]), 1)
        elements = extracted["pages"][0]["elements"]
        self.assertGreater(len(elements), 0)
        
        # Verify no figures detected, elements have sequential reading order
        figure_elements = [e for e in elements if e.get("type") == "figure"]
        self.assertEqual(len(figure_elements), 0)
        reading_orders = [e["reading_order"] for e in elements]
        self.assertEqual(reading_orders, list(range(1, len(elements) + 1)))

    def test_scenario_2_figure_with_embedded_text(self):
        """Scenario 2: PDF containing a figure with embedded text extracts content and generates alt text."""
        # Test OCR line processor directly with simulated figure OCR results
        lines_detected = [
            [[[10, 10], [100, 10], [100, 30], [10, 30]], ("Quarterly Growth", 0.98)],
            [[[10, 40], [150, 40], [150, 60], [10, 60]], ("Revenue increased by 15%", 0.95)],
        ]
        rect = fitz.Rect(50, 200, 350, 400)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 300, 200, page_num=1, page_w=612, page_h=792,
            id_counter=10, base_font_size=11.0, existing_elements=[]
        )

        self.assertGreater(len(sub_elements), 0)
        self.assertIn("Quarterly Growth", summary)
        # Verify Pydantic validation: all sub elements should have valid tags and bounding boxes
        for sub in sub_elements:
            self.assertIn("bounding_box", sub)
            self.assertIn("type", sub)
            self.assertIn("content", sub)

    def test_scenario_3_figure_with_heading_and_paragraphs(self):
        """Scenario 3: Figure containing heading and multiple paragraphs classifies them properly."""
        lines_detected = [
            # Large heading line
            [[[10, 5], [120, 5], [120, 35], [10, 35]], ("EXECUTIVE SUMMARY", 0.99)],
            # Paragraph 1 line 1
            [[[10, 50], [200, 50], [200, 68], [10, 68]], ("First line of body text.", 0.96)],
            # Paragraph 1 line 2 (close vertically, should merge or stay P)
            [[[10, 72], [200, 72], [200, 90], [10, 90]], ("Second line of body text.", 0.95)],
            # Separate paragraph after a gap
            [[[10, 130], [180, 130], [180, 148], [10, 148]], ("Follow-up notes for the team.", 0.94)],
        ]
        rect = fitz.Rect(50, 150, 400, 450)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 350, 300, page_num=1, page_w=612, page_h=792,
            id_counter=1, base_font_size=11.0, existing_elements=[]
        )

        tags = [e.get("tag") for e in sub_elements]
        types = [e.get("type") for e in sub_elements]
        
        # Verify Heading is detected
        self.assertIn("heading", types)
        self.assertTrue(any(t in ("H2", "H3") for t in tags))
        # Verify Paragraph is detected
        self.assertIn("paragraph", types)
        self.assertIn("P", tags)

    def test_scenario_4_figure_containing_table(self):
        """Scenario 4: Figure containing a grid structure is classified as Table with TR, TH, TD."""
        lines_detected = [
            # Header row (Y ~ 10)
            [[[10, 10], [80, 10], [80, 25], [10, 25]], ("Item", 0.98)],
            [[[100, 10], [180, 10], [180, 25], [100, 25]], ("Cost", 0.97)],
            # Data row 1 (Y ~ 40)
            [[[10, 40], [80, 40], [80, 55], [10, 55]], ("Widget A", 0.95)],
            [[[100, 40], [180, 40], [180, 55], [100, 55]], ("$10.00", 0.96)],
            # Data row 2 (Y ~ 70)
            [[[10, 70], [80, 70], [80, 85], [10, 85]], ("Widget B", 0.95)],
            [[[100, 70], [180, 70], [180, 85], [100, 85]], ("$20.00", 0.96)],
        ]
        rect = fitz.Rect(50, 200, 300, 400)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 250, 200, page_num=1, page_w=612, page_h=792,
            id_counter=1, base_font_size=11.0, existing_elements=[]
        )

        self.assertEqual(len(sub_elements), 1)
        table = sub_elements[0]
        self.assertEqual(table.get("type"), "table")
        self.assertEqual(table.get("tag"), "Table")
        self.assertIn("kids", table)
        rows = table["kids"]
        self.assertEqual(len(rows), 3) # 1 header row + 2 data rows
        
        # Verify TH cells in first row
        th_cells = rows[0]["kids"]
        self.assertTrue(all(c.get("tag") == "TH" for c in th_cells))
        # Verify TD cells in second row
        td_cells = rows[1]["kids"]
        self.assertTrue(all(c.get("tag") == "TD" for c in td_cells))

    def test_scenario_5_figure_between_two_paragraphs(self):
        """Scenario 5: Figure positioned between two paragraphs inserts content right between P1 and P2."""
        # Top = page_h - y1 in PDF user coordinates
        # P1 is at top (y_top = 100, bottom = 150)
        p1 = {
            "id": 1, "type": "paragraph", "tag": "P", "content": "Paragraph 1 text.",
            "bounding_box": [50.0, 642.0, 500.0, 692.0]  # top_y = 792 - 692 = 100, bot_y = 150
        }
        # P2 is at bottom (y_top = 450, bottom = 500)
        p2 = {
            "id": 2, "type": "paragraph", "tag": "P", "content": "Paragraph 2 text.",
            "bounding_box": [50.0, 292.0, 500.0, 342.0]  # top_y = 792 - 342 = 450, bot_y = 500
        }
        # Figure is in the middle (top_y = 200, bottom_y = 350)
        fig_el = {
            "id": 10, "type": "figure", "tag": "Figure", "content": "",
            "bounding_box": [50.0, 442.0, 400.0, 592.0], "alt_text": "Diagram"
        }
        fig_sub = {
            "id": 11, "type": "paragraph", "tag": "P", "content": "Figure label text.",
            "bounding_box": [60.0, 450.0, 200.0, 470.0]
        }
        fig_data = [{
            "figure_el": fig_el,
            "extracted_elements": [fig_sub],
            "bbox": fig_el["bounding_box"],
            "top_y": 200.0,
            "bottom_y": 350.0,
            "left_x": 50.0,
            "right_x": 400.0
        }]

        result = PaddleLayoutExtractor._integrate_figures_reading_order(
            [p1, p2], fig_data, page_w=612, page_h=792
        )

        self.assertEqual(len(result), 4)
        # Expected order: P1 -> Figure -> Figure Content -> P2
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[1]["id"], 10)
        self.assertEqual(result[2]["id"], 11)
        self.assertEqual(result[3]["id"], 2)
        # Verify sequential reading_order
        for idx, el in enumerate(result):
            self.assertEqual(el["reading_order"], idx + 1)

    def test_scenario_6_figure_in_multi_column_page(self):
        """Scenario 6: Figure inside a two-column page stays in its proper column sequence."""
        # Left column elements (left_x = 50)
        col0_p1 = {"id": 1, "type": "paragraph", "bounding_box": [50.0, 650.0, 250.0, 700.0]} # top=92, bot=142
        col0_p2 = {"id": 2, "type": "paragraph", "bounding_box": [50.0, 400.0, 250.0, 450.0]} # top=342, bot=392
        
        # Right column elements (left_x = 350, col_split = 612 * 0.48 = 293.76)
        col1_p1 = {"id": 3, "type": "paragraph", "bounding_box": [350.0, 650.0, 550.0, 700.0]} # top=92, bot=142
        col1_p2 = {"id": 4, "type": "paragraph", "bounding_box": [350.0, 400.0, 550.0, 450.0]} # top=342, bot=392

        # Figure placed in Left column between col0_p1 and col0_p2 (top_y = 200, bottom_y = 300)
        fig_el = {"id": 5, "type": "figure", "bounding_box": [50.0, 492.0, 250.0, 592.0]}
        fig_data = [{
            "figure_el": fig_el,
            "extracted_elements": [],
            "bbox": fig_el["bounding_box"],
            "top_y": 200.0,
            "bottom_y": 300.0,
            "left_x": 50.0,
            "right_x": 250.0
        }]

        result = PaddleLayoutExtractor._integrate_figures_reading_order(
            [col0_p1, col0_p2, col1_p1, col1_p2], fig_data, page_w=612, page_h=792
        )

        ids = [el["id"] for el in result]
        # Left column must complete (p1 -> fig -> p2) before Right column (p3 -> p4) starts!
        self.assertEqual(ids, [1, 5, 2, 3, 4])

    def test_scenario_7_figure_content_continues_section(self):
        """Scenario 7: Figure continuing a numbered section is ordered directly after the section heading."""
        h2 = {
            "id": 1, "type": "heading", "tag": "H2", "heading_level": 2,
            "content": "Section 4: Technical Diagram",
            "bounding_box": [50.0, 700.0, 400.0, 730.0]  # top=62, bot=92
        }
        fig_el = {
            "id": 2, "type": "figure", "tag": "Figure", "content": "",
            "bounding_box": [50.0, 450.0, 500.0, 680.0]
        }
        fig_sub = {
            "id": 3, "type": "paragraph", "tag": "P", "content": "Figure 4.1: Architecture Flow",
            "bounding_box": [50.0, 420.0, 300.0, 440.0]
        }
        following_p = {
            "id": 4, "type": "paragraph", "tag": "P", "content": "As shown in the architecture above...",
            "bounding_box": [50.0, 350.0, 500.0, 400.0]
        }

        fig_data = [{
            "figure_el": fig_el,
            "extracted_elements": [fig_sub],
            "bbox": fig_el["bounding_box"],
            "top_y": 112.0,
            "bottom_y": 342.0,
            "left_x": 50.0,
            "right_x": 500.0
        }]

        result = PaddleLayoutExtractor._integrate_figures_reading_order(
            [h2, following_p], fig_data, page_w=612, page_h=792
        )

        ids = [el["id"] for el in result]
        self.assertEqual(ids, [1, 2, 3, 4])

    def test_scenario_8_duplicate_text_prevention(self):
        """Scenario 8: Deduplication prevents re-extracting text already present in PDF text layer."""
        existing_elements = [
            {"type": "paragraph", "content": "Existing document text already in PDF layer.", "bounding_box": [50, 500, 400, 520]}
        ]
        # Lines detected include the same text
        lines_detected = [
            [[[10, 10], [100, 10], [100, 25], [10, 25]], ("Existing document text already in PDF layer.", 0.99)],
            [[[10, 40], [100, 40], [100, 55], [10, 55]], ("New Unique Figure Caption", 0.95)]
        ]
        rect = fitz.Rect(50, 200, 300, 400)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 250, 200, page_num=1, page_w=612, page_h=792,
            id_counter=1, base_font_size=11.0, existing_elements=existing_elements
        )

        # Only the unique text should be extracted
        self.assertEqual(len(sub_elements), 1)
        self.assertIn("New Unique Figure Caption", sub_elements[0]["content"])

    def test_scenario_9_pdf_with_existing_accessibility_tags(self):
        """Scenario 9: PDF with existing StructTree tags extracts trapped figure content and remediates alt text."""
        pdf_path = os.path.join(self.temp_dir, "scenario9_tagged.pdf")
        
        # Create a simple PDF with an existing StructTree containing a Figure tag
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((50, 100), "Hello World tagged page", fontsize=12)
        doc.save(pdf_path)
        doc.close()

        # Add minimal StructTreeRoot via pikepdf
        with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdoc:
            struct_root = pdoc.make_indirect(pikepdf.Dictionary(
                Type=Name("/StructTreeRoot"),
                RoleMap=pikepdf.Dictionary(),
                K=pikepdf.Array()
            ))
            fig_elem = pdoc.make_indirect(pikepdf.Dictionary(
                Type=Name("/StructElem"),
                S=Name("/Figure"),
                P=struct_root,
                Pg=pdoc.pages[0].obj,
                K=pikepdf.Array([pikepdf.Integer(0)])
            ))
            struct_root.K.append(fig_elem)
            pdoc.Root.StructTreeRoot = struct_root
            pdoc.save(pdf_path)

        # Verify that check_pdf_has_struct_tree detects it
        fitz_doc = fitz.open(pdf_path)
        has_tree = PaddleLayoutExtractor.check_pdf_has_struct_tree(fitz_doc)
        fitz_doc.close()
        self.assertTrue(has_tree)

        # Remediate via TaggingService and check that Figure tag retains or gains valid /Alt
        output_pdf = os.path.join(self.temp_dir, "scenario9_remediated.pdf")
        TaggingService.remediate_pdf(pdf_path, output_pdf)
        
        self.assertTrue(os.path.exists(output_pdf))
        with pikepdf.open(output_pdf) as out_doc:
            self.assertIn("/StructTreeRoot", out_doc.Root)
            root_k = out_doc.Root.StructTreeRoot.K
            # Ensure Figure exists and has /Alt attribute
            def find_figures(elem):
                found = []
                if isinstance(elem, pikepdf.Dictionary):
                    if elem.get("/S") == Name("/Figure"):
                        found.append(elem)
                    kids = elem.get("/K")
                    if isinstance(kids, pikepdf.Array):
                        for k in kids:
                            found.extend(find_figures(k))
                    elif isinstance(kids, pikepdf.Dictionary):
                        found.extend(find_figures(kids))
                return found

            figures = []
            for k in root_k:
                figures.extend(find_figures(k))

            self.assertGreater(len(figures), 0)
            for fig in figures:
                self.assertIn("/Alt", fig)
                self.assertGreater(len(str(fig["/Alt"])), 0)

    def test_scenario_10_multiple_figures_on_same_page(self):
        """Scenario 10: Multiple figures on the same page are all positioned in logical reading order."""
        p_top = {"id": 1, "type": "paragraph", "bounding_box": [50.0, 700.0, 500.0, 730.0]}  # top=62, bot=92
        fig1_el = {"id": 2, "type": "figure", "bounding_box": [50.0, 500.0, 500.0, 680.0]}   # top=112, bot=292
        p_mid = {"id": 3, "type": "paragraph", "bounding_box": [50.0, 420.0, 500.0, 450.0]}  # top=342, bot=372
        fig2_el = {"id": 4, "type": "figure", "bounding_box": [50.0, 200.0, 500.0, 380.0]}   # top=412, bot=592
        p_bot = {"id": 5, "type": "paragraph", "bounding_box": [50.0, 100.0, 500.0, 130.0]}  # top=662, bot=692

        fig1_data = {
            "figure_el": fig1_el, "extracted_elements": [], "bbox": fig1_el["bounding_box"],
            "top_y": 112.0, "bottom_y": 292.0, "left_x": 50.0, "right_x": 500.0
        }
        fig2_data = {
            "figure_el": fig2_el, "extracted_elements": [], "bbox": fig2_el["bounding_box"],
            "top_y": 412.0, "bottom_y": 592.0, "left_x": 50.0, "right_x": 500.0
        }

        result = PaddleLayoutExtractor._integrate_figures_reading_order(
            [p_top, p_mid, p_bot], [fig1_data, fig2_data], page_w=612, page_h=792
        )

        ids = [el["id"] for el in result]
        self.assertEqual(ids, [1, 2, 3, 4, 5])
        for idx, el in enumerate(result):
            self.assertEqual(el["reading_order"], idx + 1)

    def test_pydantic_figure_model_validation(self):
        """Verify Pydantic FigureExtractedContent model validation with aliases and constraints."""
        data = {
            "page_number": 2,
            "bounding_box": [10.0, 20.0, 150.0, 200.0],
            "content": "Sample figure label",
            "type": "heading",
            "tag": "H2",
            "heading_level": 2,
            "confidence": 0.98,
            "alt_text": "Chart showing revenue breakdown"
        }
        model = FigureExtractedContent(**data)
        self.assertEqual(model.source_page_number, 2)
        self.assertEqual(model.semantic_element_type, "heading")
        self.assertEqual(model.proposed_accessibility_tag, "H2")
        self.assertEqual(model.heading_level, 2)
        self.assertEqual(model.alt_text, "Chart showing revenue breakdown")

        # Serialized with alias matches project schema
        dumped = model.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(dumped["type"], "heading")
        self.assertEqual(dumped["tag"], "H2")
        self.assertEqual(dumped["content"], "Sample figure label")

    def test_figure_non_table_content_not_tagged_as_table(self):
        """Verify that book paragraphs/headings inside a figure are NEVER grouped as a Table."""
        lines_detected = [
            [[[10, 10], [250, 10], [250, 30], [10, 30]], ("Chapter 1: Introduction to Biology", 0.99)],
            [[[10, 45], [280, 45], [280, 60], [10, 60]], ("Biology is the scientific study of life and organisms.", 0.96)],
            [[[10, 65], [280, 65], [280, 80], [10, 80]], ("It examines the structure, function, growth, and evolution.", 0.95)],
            [[[10, 100], [200, 100], [200, 115], [10, 115]], ("Key Biological Concepts:", 0.97)],
            [[[10, 125], [250, 125], [250, 140], [10, 140]], ("• Cellular structure and metabolism", 0.96)],
            [[[10, 145], [250, 145], [250, 160], [10, 160]], ("• Genetics and DNA inheritance", 0.96)],
        ]
        rect = fitz.Rect(50, 100, 350, 300)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 300, 200, page_num=1, page_w=612, page_h=792,
            id_counter=1, base_font_size=11.0, existing_elements=[]
        )

        # Content must NOT be classified as a Table!
        types = [e.get("type") for e in sub_elements]
        tags = [e.get("tag") for e in sub_elements]
        self.assertNotIn("table", types)
        self.assertNotIn("Table", tags)
        self.assertIn("heading", types)
        self.assertIn("paragraph", types)
        self.assertIn("list_item", types)

    def test_figure_mixed_content_individual_tags(self):
        """Verify that figure containing headings, paragraphs, lists, and genuine tables preserves each element tag."""
        # 1. Heading + Paragraph
        # 2. Genuine small table
        # 3. List item
        lines_detected = [
            [[[10, 5], [180, 5], [180, 25], [10, 25]], ("Solar System Planets", 0.99)],
            [[[10, 35], [260, 35], [260, 50], [10, 50]], ("The solar system consists of the Sun and planetary objects.", 0.95)],
            [[[10, 70], [200, 70], [200, 85], [10, 85]], ("• Terrestrial planets (Mercury, Venus, Earth, Mars)", 0.97)],
            [[[10, 90], [200, 90], [200, 105], [10, 105]], ("• Gas giants (Jupiter, Saturn, Uranus, Neptune)", 0.97)],
        ]
        rect = fitz.Rect(50, 100, 350, 400)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 300, 300, page_num=1, page_w=612, page_h=792,
            id_counter=1, base_font_size=11.0, existing_elements=[]
        )

        tags = [e.get("tag") for e in sub_elements]
        # Should contain H2/H3, P, LI, but NOT Table
        self.assertTrue(any(t in ("H2", "H3") for t in tags))
        self.assertIn("P", tags)
        self.assertIn("LI", tags)
        self.assertNotIn("Table", tags)

    def test_raster_figure_table_alongside_prose(self):
        """Verify that a raster figure containing a genuine table alongside prose classifies both correctly."""
        lines_detected = [
            [[[10, 5], [200, 5], [200, 20], [10, 20]], ("Summary of Results", 0.99)],
            # Genuine table lines: 2 columns, 3 rows
            [[[10, 30], [90, 30], [90, 45], [10, 45]], ("Header A", 0.98)],
            [[[110, 30], [190, 30], [190, 45], [110, 45]], ("Header B", 0.98)],
            [[[10, 50], [90, 50], [90, 65], [10, 65]], ("Val 1", 0.95)],
            [[[110, 50], [190, 50], [190, 65], [110, 65]], ("Val 2", 0.95)],
            [[[10, 70], [90, 70], [90, 85], [10, 85]], ("Val 3", 0.95)],
            [[[110, 70], [190, 70], [190, 85], [110, 85]], ("Val 4", 0.95)],
        ]
        rect = fitz.Rect(50, 100, 350, 300)
        sub_elements, id_counter, summary = PaddleLayoutExtractor._process_figure_ocr_lines(
            lines_detected, rect, 300, 200, page_num=1, page_w=612, page_h=792,
            id_counter=1, base_font_size=11.0, existing_elements=[]
        )
        types = [e.get("type") for e in sub_elements]
        self.assertIn("table", types)
        # Verify table structure has kids (rows and cells)
        table_el = next(e for e in sub_elements if e.get("type") == "table")
        self.assertIn("kids", table_el)
        self.assertGreaterEqual(len(table_el["kids"]), 2)

    def test_digital_figure_with_adjacent_column_text_reading_order(self):
        """Verify that digital composite figures (like Page 2 sample) keep distinct paragraphs, lists, and reading order."""
        pdf_path = os.path.join(self.temp_dir, "composite_page2.pdf")
        doc = fitz.open()
        page = doc.new_page(width=648, height=855)

        # Draw a photo on left
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 150), 1)
        samples = bytearray(pix.samples)
        for i in range(len(samples)):
            samples[i] = (i * 37) % 256
        pix_mod = fitz.Pixmap(fitz.csRGB, pix.width, pix.height, bytes(samples), 1)
        page.insert_image(fitz.Rect(40, 10, 275, 415), pixmap=pix_mod)

        # Draw quote text column next to photo (x in 280..340, y in 40..380)
        page.insert_text((280, 50), "The past resembles the future more than one drop of water resembles another.", fontsize=9)
        page.insert_text((280, 115), "IBN KHALDUN, ARAB HISTORIAN", fontsize=5)
        page.insert_text((280, 270), "1. Who do you think the last person to see this sphinx was?", fontsize=6)
        page.insert_text((280, 310), "2. Why do you think someone made the sphinx?", fontsize=6)

        # Draw section 1 below photo
        page.insert_text((60, 430), "Talk About the Quote", fontsize=14)
        page.insert_text((60, 460), "1. Read aloud the quote at the top of the page.", fontsize=11)

        # Draw section 2 on right
        page.insert_text((400, 80), "ABOUT IBN KHALDUN", fontsize=12)
        page.insert_text((400, 105), "Ibn Khaldun was a scholar from Tunisia.", fontsize=10)
        page.insert_text((350, 430), "Share Your Ideas", fontsize=14)
        page.insert_text((350, 460), "1. Guide discussion of the questions.", fontsize=11)

        doc.save(pdf_path)
        doc.close()

        extracted = PaddleLayoutExtractor.extract_document(pdf_path)
        elements = extracted["pages"][0]["elements"]

        # Confirm Figure exists
        fig_els = [e for e in elements if e.get("type") == "figure"]
        self.assertEqual(len(fig_els), 1)

        # Confirm quote text exists as distinct semantic elements (not swallowed into alt-text only)
        all_content = " ".join(e.get("content", "") for e in elements)
        self.assertIn("The past resembles the future", all_content)
        self.assertIn("IBN KHALDUN", all_content)
        self.assertIn("Talk About the Quote", all_content)
        self.assertIn("ABOUT IBN KHALDUN", all_content)

        # Confirm list items
        list_items = [e for e in elements if e.get("tag") == "LI"]
        self.assertGreaterEqual(len(list_items), 2)

        # Verify reading order is strictly ascending
        reading_orders = [e["reading_order"] for e in elements]
        self.assertEqual(reading_orders, sorted(reading_orders))

        # Test end-to-end PDF remediation
        out_pdf = os.path.join(self.temp_dir, "composite_page2_tagged.pdf")
        res = TaggingService.remediate_pdf(pdf_path, out_pdf)
        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(out_pdf))

        # Verify structure tree in output PDF
        with pikepdf.Pdf.open(out_pdf) as tagged_doc:
            self.assertIn("/StructTreeRoot", tagged_doc.Root)
            struct_root = tagged_doc.Root.StructTreeRoot
            self.assertIn("/K", struct_root)


if __name__ == "__main__":
    unittest.main()

