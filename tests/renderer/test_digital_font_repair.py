import unittest
import pikepdf
import fitz
import os
import io

from renderer.digital_font_repair import DigitalFontRepair

class TestDigitalFontRepair(unittest.TestCase):
    def setUp(self):
        # Create a dummy PDF with pikepdf for testing
        self.pdf = pikepdf.Pdf.new()
        self.pdf.add_blank_page(page_size=(612, 792))
        self.page = self.pdf.pages[0]
        
        self.page.Resources = pikepdf.Dictionary()
        self.page.Resources.Font = pikepdf.Dictionary()

    def _add_dummy_font(self, name, base_font, subtype="/Type1", fd=None):
        font_dict = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name(subtype),
            BaseFont=pikepdf.Name(base_font),
            Encoding=pikepdf.Name("/WinAnsiEncoding")
        )
        if fd:
            font_dict.FontDescriptor = fd
        
        self.page.Resources.Font[name] = self.pdf.make_indirect(font_dict)

    def test_embedded_font_preserved(self):
        # Setup already embedded font
        fd = pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"),
            FontFile=self.pdf.make_stream(b"dummy_font_data")
        )
        self._add_dummy_font("/F1", "/Helvetica", fd=fd)
        
        repairer = DigitalFontRepair(self.pdf)
        repairer.repair_fonts()
        
        font = self.page.Resources.Font["/F1"]
        self.assertEqual(str(font.Subtype), "/Type1")
        self.assertEqual(str(font.FontDescriptor.Type), "/FontDescriptor")
        
        diag = repairer.diagnostics[0]
        self.assertTrue(diag["embedded"])
        self.assertEqual(diag["action"], "PRESERVED")

    def test_unembedded_helvetica_replaced(self):
        self._add_dummy_font("/F1", "/Helvetica")
        
        repairer = DigitalFontRepair(self.pdf)
        repairer.repair_fonts()
        
        font = self.page.Resources.Font["/F1"]
        self.assertEqual(str(font.Subtype), "/TrueType")
        self.assertEqual(str(font.BaseFont), "/Helvetica")
        self.assertEqual(str(font.Encoding), "/WinAnsiEncoding")
        self.assertTrue("/FontFile2" in font.FontDescriptor)
        self.assertTrue(len(font.Widths) > 0)
        
        diag = repairer.diagnostics[0]
        self.assertFalse(diag["embedded"])
        self.assertEqual(diag["action"], "REPLACED")
        self.assertEqual(diag["replacement"], "Liberation Sans")

    def test_unembedded_times_roman_replaced(self):
        self._add_dummy_font("/F2", "/Times-Roman")
        
        repairer = DigitalFontRepair(self.pdf)
        repairer.repair_fonts()
        
        font = self.page.Resources.Font["/F2"]
        self.assertEqual(str(font.Subtype), "/TrueType")
        self.assertEqual(str(font.BaseFont), "/Times-Roman")
        
        diag = repairer.diagnostics[0]
        self.assertEqual(diag["action"], "REPLACED")
        self.assertEqual(diag["replacement"], "Liberation Serif")

    def test_unknown_font_preserved(self):
        self._add_dummy_font("/F3", "/ComicSans")
        
        repairer = DigitalFontRepair(self.pdf)
        repairer.repair_fonts()
        
        font = self.page.Resources.Font["/F3"]
        self.assertEqual(str(font.Subtype), "/Type1") # Unchanged
        
        diag = repairer.diagnostics[0]
        self.assertEqual(diag["action"], "PRESERVED")
        self.assertEqual(diag["status"], "UNRESOLVED")

if __name__ == '__main__':
    unittest.main()
