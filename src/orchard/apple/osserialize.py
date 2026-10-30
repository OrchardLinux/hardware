"""Apple legacy OSUnserialize XML (ID/IDREF, without a plist wrapper)."""
import base64
import xml.etree.ElementTree as ET


def apple_xml(blob):
    # Apple's legacy OSUnserialize XML uses ID/IDREF and no <plist> wrapper.
    ids = {}

    def visit(e):
        if 'IDREF' in e.attrib:
            return ids[e.attrib['IDREF']]
        text = e.text or ''
        if e.tag == 'dict':
            children = list(e)
            if len(children) % 2:
                raise ValueError('Unbalanced dictionary')
            value = {children[i].text: visit(children[i+1]) for i in range(0, len(children), 2)}
        elif e.tag == 'array':
            value = [visit(c) for c in e]
        elif e.tag in ('string', 'key'):
            value = text
        elif e.tag == 'integer':
            value = int(text, 16 if text.strip().startswith('0x') else 10)
        elif e.tag == 'data':
            value = base64.b64decode(text)
        elif e.tag in ('true', 'false'):
            value = e.tag == 'true'
        else:
            raise ValueError('Unexpected XML tag: ' + e.tag)
        if 'ID' in e.attrib:
            ids[e.attrib['ID']] = value
        return value

    return visit(ET.fromstring(blob.rstrip(b'\0')))
