"""Policy and parser behavior for the deliberately limited XML entry point."""

from __future__ import annotations

import unittest
from xml.parsers import expat

from xml_entity_resource_boundary import (
    ExternalResourceDenied,
    MalformedXmlError,
    UnsupportedXmlConstruct,
    XmlBudgetExceeded,
    XmlPolicy,
    parse_xml,
)

from lab_support import nested_document


class XmlBoundaryTests(unittest.TestCase):
    def test_normal_xml_preserves_element_attribute_and_text_structure(self) -> None:
        result = parse_xml(b'<root><item id="7">safe &amp; sound</item></root>')
        self.assertEqual(result.root.name, "root")
        item = result.root.children[0]
        self.assertEqual(item.name, "item")
        self.assertEqual(item.attributes, (("id", "7"),))
        self.assertEqual(item.text_content(), "safe & sound")
        self.assertEqual(result.elements, 2)

    def test_small_nested_internal_entity_is_preserved(self) -> None:
        result = parse_xml(nested_document(3))
        self.assertEqual(result.root.text_content(), "ha" * 8)
        self.assertEqual(result.entity_declarations, 4)

    def test_forward_internal_reference_is_accounted(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY first "&later;"><!ENTITY later "ok">]><r>&first;</r>'
        self.assertEqual(parse_xml(document).root.text_content(), "ok")

    def test_nested_entity_exceeds_preflight_replacement_limit(self) -> None:
        with self.assertRaisesRegex(XmlBudgetExceeded, "replacement expansion"):
            parse_xml(nested_document(7), policy=XmlPolicy(max_entity_expansion_bytes=64))

    def test_repeated_entity_references_exceed_visible_budget(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "1234">]><r>&a;&a;&a;</r>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "visible XML data"):
            parse_xml(document, policy=XmlPolicy(max_visible_bytes=10))

    def test_attribute_expansion_is_counted(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "1234">]><r value="&a;&a;&a;"/>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "visible XML data"):
            parse_xml(document, policy=XmlPolicy(max_visible_bytes=10))

    def test_default_attribute_names_are_counted_for_every_element(self) -> None:
        document = b'<!DOCTYPE root [<!ATTLIST item longname CDATA "">]><root><item/><item/><item/></root>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "visible XML data"):
            parse_xml(document, policy=XmlPolicy(max_visible_bytes=32))

    def test_external_general_entity_is_denied_even_without_reference(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///tmp/owned-lab-marker">]><r/>'
        with self.assertRaises(ExternalResourceDenied):
            parse_xml(document)

    def test_external_https_entity_is_denied(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY x SYSTEM "https://example.invalid/x">]><r>&x;</r>'
        with self.assertRaises(ExternalResourceDenied):
            parse_xml(document)

    def test_external_dtd_is_denied(self) -> None:
        document = b'<!DOCTYPE r SYSTEM "file:///tmp/owned-lab.dtd"><r/>'
        with self.assertRaises(ExternalResourceDenied):
            parse_xml(document)

    def test_parameter_entity_declaration_is_denied(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY % p "ignored">]><r/>'
        with self.assertRaises(UnsupportedXmlConstruct):
            parse_xml(document)

    def test_undeclared_parameter_reference_skipped_by_expat_is_denied(self) -> None:
        document = b'<!DOCTYPE r [%unknown;]><r/>'
        raw_parser = expat.ParserCreate()
        raw_parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        seen_declarations: list[str] = []
        raw_parser.EntityDeclHandler = lambda name, *_rest: seen_declarations.append(name)
        raw_parser.Parse(document, True)
        self.assertEqual(seen_declarations, [])
        with self.assertRaisesRegex(UnsupportedXmlConstruct, "parameter entities"):
            parse_xml(document)

    def test_duplicate_entity_declaration_is_denied_even_below_budget(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "x"><!ENTITY a "y">]><r>&a;</r>'
        raw_parser = expat.ParserCreate()
        seen_declarations: list[str] = []
        raw_parser.EntityDeclHandler = lambda name, *_rest: seen_declarations.append(name)
        raw_parser.Parse(document, True)
        self.assertEqual(seen_declarations, ["a"])
        with self.assertRaisesRegex(UnsupportedXmlConstruct, "duplicate or skipped"):
            parse_xml(document)

    def test_duplicate_declarations_count_against_budget(self) -> None:
        document = b'<!DOCTYPE r [' + b'<!ENTITY a "x">' * 20 + b']><r>&a;</r>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "declaration count"):
            parse_xml(document)
        with self.assertRaisesRegex(XmlBudgetExceeded, "declaration count"):
            parse_xml(document, policy=XmlPolicy(max_entity_declarations=1))

    def test_duplicate_external_entity_declaration_is_denied(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "x"><!ENTITY a SYSTEM "file:///tmp/owned-lab-marker">]><r/>'
        with self.assertRaisesRegex(UnsupportedXmlConstruct, "duplicate or skipped"):
            parse_xml(document)

    def test_dtd_comment_and_literal_do_not_count_as_declarations_or_references(self) -> None:
        document = (
            b'<!DOCTYPE r [<!-- <!ENTITY ghost "x"> %ghost; -->'
            b'<!ATTLIST r note CDATA "100%">'
            b'<!ENTITY a "x">]><r>&a;</r>'
        )
        result = parse_xml(document, policy=XmlPolicy(max_entity_declarations=1))
        self.assertEqual(result.entity_declarations, 1)
        self.assertEqual(result.root.attributes, (("note", "100%"),))
        self.assertEqual(result.root.text_content(), "x")

    def test_input_byte_limit(self) -> None:
        with self.assertRaisesRegex(XmlBudgetExceeded, "input byte"):
            parse_xml(b"<r>12345</r>", policy=XmlPolicy(max_input_bytes=8))

    def test_declaration_count_limit(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "x"><!ENTITY b "y">]><r/>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "declaration count"):
            parse_xml(document, policy=XmlPolicy(max_entity_declarations=1))

    def test_entity_declaration_byte_limit(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "abcde">]><r/>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "declaration byte"):
            parse_xml(document, policy=XmlPolicy(max_entity_declaration_bytes=4))

    def test_entity_nesting_limit(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "&b;"><!ENTITY b "&c;"><!ENTITY c "x">]><r>&a;</r>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "entity nesting"):
            parse_xml(document, policy=XmlPolicy(max_entity_nesting=2))

    def test_entity_nesting_limit_is_independent_of_declaration_order(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY c "x"><!ENTITY b "&c;"><!ENTITY a "&b;">]><r>&a;</r>'
        with self.assertRaisesRegex(XmlBudgetExceeded, "entity nesting"):
            parse_xml(document, policy=XmlPolicy(max_entity_nesting=2))

    def test_circular_entity_is_denied(self) -> None:
        document = b'<!DOCTYPE r [<!ENTITY a "&a;">]><r>&a;</r>'
        with self.assertRaises(XmlBudgetExceeded):
            parse_xml(document)

    def test_element_count_limit(self) -> None:
        with self.assertRaisesRegex(XmlBudgetExceeded, "element or depth"):
            parse_xml(b"<r><a/><b/></r>", policy=XmlPolicy(max_elements=2))

    def test_depth_limit(self) -> None:
        with self.assertRaisesRegex(XmlBudgetExceeded, "element or depth"):
            parse_xml(b"<r><a><b/></a></r>", policy=XmlPolicy(max_depth=2))

    def test_attribute_count_limit(self) -> None:
        with self.assertRaisesRegex(XmlBudgetExceeded, "attribute count"):
            parse_xml(b'<r a="1" b="2"/>', policy=XmlPolicy(max_attributes_per_element=1))

    def test_name_byte_limit(self) -> None:
        with self.assertRaisesRegex(XmlBudgetExceeded, "name byte"):
            parse_xml(b"<longname/>", policy=XmlPolicy(max_name_bytes=4))

    def test_malformed_xml_error_omits_source_text(self) -> None:
        marker = "SYNTHETIC_SECRET"
        with self.assertRaises(MalformedXmlError) as error:
            parse_xml(("<r>" + marker).encode())
        self.assertNotIn(marker, str(error.exception))

    def test_input_requires_bytes(self) -> None:
        with self.assertRaises(TypeError):
            parse_xml("<r/>")

    def test_policy_types_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            XmlPolicy(max_visible_bytes=True)
        with self.assertRaises(ValueError):
            XmlPolicy(max_entity_declarations=-1)
        with self.assertRaises(ValueError):
            XmlPolicy(max_entity_nesting=129)
        with self.assertRaises(ValueError):
            XmlPolicy(max_depth=129)

    def test_utf16_xml_with_no_entities_is_supported(self) -> None:
        result = parse_xml("<r><a>雪</a></r>".encode("utf-16"))
        self.assertEqual(result.root.text_content(), "雪")
        self.assertEqual(result.elements, 2)

    def test_utf16_dtd_uses_the_same_entity_policy(self) -> None:
        accepted = '<!DOCTYPE r [<!ENTITY a "雪">]><r>&a;</r>'.encode("utf-16")
        self.assertEqual(parse_xml(accepted).root.text_content(), "雪")
        unresolved = "<!DOCTYPE r [%unknown;]><r/>".encode("utf-16")
        with self.assertRaisesRegex(UnsupportedXmlConstruct, "parameter entities"):
            parse_xml(unresolved)
        duplicate = '<!DOCTYPE r [<!ENTITY a "x"><!ENTITY a "y">]><r/>'.encode("utf-16")
        with self.assertRaisesRegex(UnsupportedXmlConstruct, "duplicate or skipped"):
            parse_xml(duplicate)


if __name__ == "__main__":
    unittest.main()
