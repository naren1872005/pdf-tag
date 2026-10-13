import unittest
import xml.etree.ElementTree as ET
from metadata.metadata_model import DocumentMetadata
from metadata.xmp_builder import XmpBuilder

class TestXmpBuilder(unittest.TestCase):

    def test_builds_valid_xml(self):
        meta = DocumentMetadata(
            title="Sample PDF/UA Report",
            pdfua_part=1
        )
        xmp_str = XmpBuilder.build(meta)
        
        self.assertTrue(xmp_str.startswith('<?xpacket begin="'))
        self.assertTrue(xmp_str.endswith('<?xpacket end="w"?>'))
        
        # Strip xpacket to parse as pure XML
        core_xml = xmp_str.split('?>\n', 1)[1].rsplit('\n<?xpacket', 1)[0]
        
        root = ET.fromstring(core_xml)
        self.assertTrue(root.tag.endswith('xmpmeta'))
        
        # Verify namespaces and elements
        rdf = root.find('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
        self.assertIsNotNone(rdf)
        
        desc = rdf.find('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
        self.assertIsNotNone(desc)
        
        title_li = desc.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
        self.assertEqual(title_li.text, "Sample PDF/UA Report")
        
        part = desc.find('{http://www.aiim.org/pdfua/ns/id/}part')
        self.assertEqual(part.text, "1")

    def test_builds_without_optional_fields(self):
        meta = DocumentMetadata() # Empty
        xmp_str = XmpBuilder.build(meta)
        
        core_xml = xmp_str.split('?>\n', 1)[1].rsplit('\n<?xpacket', 1)[0]
        root = ET.fromstring(core_xml)
        desc = root.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
        
        self.assertIsNone(desc.find('.//{http://purl.org/dc/elements/1.1/}title'))
        self.assertIsNone(desc.find('{http://www.aiim.org/pdfua/ns/id/}part'))
