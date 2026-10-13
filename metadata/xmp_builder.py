# GUI_V1/metadata/xmp_builder.py
import xml.etree.ElementTree as ET
from metadata.metadata_model import DocumentMetadata

class XmpBuilder:
    """
    Builds an XMP XML packet from the canonical DocumentMetadata.
    """

    @staticmethod
    def build(metadata: DocumentMetadata) -> str:
        # Define namespaces
        x_ns = "adobe:ns:meta/"
        rdf_ns = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        dc_ns = "http://purl.org/dc/elements/1.1/"
        pdfuaid_ns = "http://www.aiim.org/pdfua/ns/id/"
        
        ET.register_namespace("x", x_ns)
        ET.register_namespace("rdf", rdf_ns)
        ET.register_namespace("dc", dc_ns)
        ET.register_namespace("pdfuaid", pdfuaid_ns)

        # Root <x:xmpmeta>
        xmpmeta = ET.Element(f"{{{x_ns}}}xmpmeta")
        
        # <rdf:RDF>
        rdf = ET.SubElement(xmpmeta, f"{{{rdf_ns}}}RDF")
        
        # <rdf:Description rdf:about="">
        desc = ET.SubElement(rdf, f"{{{rdf_ns}}}Description")
        desc.set(f"{{{rdf_ns}}}about", "")

        # <dc:title>
        if metadata.title:
            title_elem = ET.SubElement(desc, f"{{{dc_ns}}}title")
            alt = ET.SubElement(title_elem, f"{{{rdf_ns}}}Alt")
            li = ET.SubElement(alt, f"{{{rdf_ns}}}li")
            li.set("xml:lang", "x-default")
            li.text = metadata.title

        # <dc:creator> for Author
        if metadata.author:
            creator_elem = ET.SubElement(desc, f"{{{dc_ns}}}creator")
            seq = ET.SubElement(creator_elem, f"{{{rdf_ns}}}Seq")
            li = ET.SubElement(seq, f"{{{rdf_ns}}}li")
            li.text = metadata.author

        # <dc:description> for Subject
        if metadata.subject:
            desc_elem = ET.SubElement(desc, f"{{{dc_ns}}}description")
            alt = ET.SubElement(desc_elem, f"{{{rdf_ns}}}Alt")
            li = ET.SubElement(alt, f"{{{rdf_ns}}}li")
            li.set("xml:lang", "x-default")
            li.text = metadata.subject

        # <pdf:Keywords>
        if metadata.keywords:
            pdf_ns = "http://ns.adobe.com/pdf/1.3/"
            ET.register_namespace("pdf", pdf_ns)
            keywords_elem = ET.SubElement(desc, f"{{{pdf_ns}}}Keywords")
            keywords_elem.text = metadata.keywords

        # <pdfuaid:part>
        if metadata.pdfua_part is not None:
            part_elem = ET.SubElement(desc, f"{{{pdfuaid_ns}}}part")
            part_elem.text = str(metadata.pdfua_part)

        # Convert to string and wrap in xpacket
        xml_str = ET.tostring(xmpmeta, encoding="unicode", xml_declaration=False)
        
        # Standard XMP packet header and footer required by PDF specification
        packet = (
            '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            f'{xml_str}\n'
            '<?xpacket end="w"?>'
        )
        return packet
