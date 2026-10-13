import unittest
from renderer.page_renderer import PageRenderer
from renderer.mcid_writer import MCIDWriter

class TestInvalidBulletRedaction(unittest.TestCase):

    def test_mcid_writer_fallback(self):
        """Test that MCIDWriter emits fallback bullet for Helvetica 0x7F bullets"""
        font_runs = [
            {
                "font": "Helvetica",
                "text": "\u2022",
                "size": 11.0,
                "bbox": [100, 100, 110, 110],
                "origin": [100, 100]
            }
        ]
        
        # Test with Lbl tag (should use fallback)
        stream_lbl = MCIDWriter.build_native_font_stream(font_runs, 0, "Lbl")
        self.assertIn("/fallback", stream_lbl)
        self.assertIn("0 Tr", stream_lbl) # Invisible mode for fallback
        self.assertIn(" Tj", stream_lbl)
        
        # Test with P tag (should NOT use fallback, because it's not a list label)
        stream_p = MCIDWriter.build_native_font_stream(font_runs, 0, "P")
        self.assertNotIn("/fallback", stream_p)
        self.assertNotIn("0 Tr", stream_p)

    def test_mcid_writer_normal_helvetica(self):
        """Test that normal Helvetica text is not affected"""
        font_runs = [
            {
                "font": "Helvetica",
                "text": "Normal",
                "size": 11.0,
                "bbox": [100, 100, 110, 110],
                "origin": [100, 100]
            }
        ]
        stream_p = MCIDWriter.build_native_font_stream(font_runs, 0, "P")
        self.assertNotIn("/fallback", stream_p)
        
    def test_mcid_writer_times(self):
        """Test that Times-Roman text is not affected"""
        font_runs = [
            {
                "font": "Times-Roman",
                "text": "\u2022",
                "size": 11.0,
                "bbox": [100, 100, 110, 110],
                "origin": [100, 100]
            }
        ]
        stream_lbl = MCIDWriter.build_native_font_stream(font_runs, 0, "Lbl")
        self.assertNotIn("/fallback", stream_lbl)

if __name__ == '__main__':
    unittest.main()
