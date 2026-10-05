"""Parse a bounded XML subset without loading external entities or DTDs.

The implementation uses Python's Expat binding for XML syntax. The policy,
entity preflight, output accounting, and tree are independently implemented.
It intentionally does not implement a general XML resolver or serializer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.parsers import expat


class XmlBoundaryError(ValueError):
    """The input is outside this parser's XML policy."""


class XmlBudgetExceeded(XmlBoundaryError):
    """A configured input, declaration, or output limit was exceeded."""


class ExternalResourceDenied(XmlBoundaryError):
    """An external DTD or entity was requested or declared."""


class UnsupportedXmlConstruct(XmlBoundaryError):
    """The document uses an XML feature outside this narrow subset."""


class MalformedXmlError(XmlBoundaryError):
    """Expat rejected the document's XML syntax."""


@dataclass(frozen=True)
class XmlPolicy:
    max_input_bytes: int = 64 * 1024
    max_visible_bytes: int = 64 * 1024
    max_entity_declarations: int = 16
    max_entity_declaration_bytes: int = 512
    max_entity_expansion_bytes: int = 256
    max_entity_nesting: int = 12
    max_elements: int = 4096
    max_depth: int = 64
    max_attributes_per_element: int = 64
    max_name_bytes: int = 256

    def __post_init__(self) -> None:
        positive = (
            "max_input_bytes",
            "max_visible_bytes",
            "max_entity_declaration_bytes",
            "max_entity_expansion_bytes",
            "max_entity_nesting",
            "max_elements",
            "max_depth",
            "max_attributes_per_element",
            "max_name_bytes",
        )
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        value = self.max_entity_declarations
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("max_entity_declarations must be a nonnegative integer")
        if self.max_entity_nesting > 128 or self.max_depth > 128:
            raise ValueError("entity and element depth limits must not exceed 128")


@dataclass(frozen=True)
class XmlNode:
    name: str
    attributes: tuple[tuple[str, str], ...]
    children: tuple[XmlNode | str, ...]

    def text_content(self) -> str:
        return "".join(
            child if isinstance(child, str) else child.text_content()
            for child in self.children
        )


@dataclass(frozen=True)
class XmlResult:
    root: XmlNode
    input_bytes: int
    visible_bytes: int
    elements: int
    entity_declarations: int


@dataclass
class _NodeBuilder:
    name: str
    attributes: tuple[tuple[str, str], ...]
    children: list[_NodeBuilder | str] = field(default_factory=list)

    def freeze(self) -> XmlNode:
        return XmlNode(
            self.name,
            self.attributes,
            tuple(
                child if isinstance(child, str) else child.freeze()
                for child in self.children
            ),
        )


_PREDEFINED = {"amp": "&", "lt": "<", "gt": ">", "apos": "'", "quot": '"'}


def _count_dtd_declarations(subset: str, policy: XmlPolicy) -> int:
    """Count actual ENTITY markup and reject parameter references in a DTD subset.

    Expat's entity callback omits later declarations of an already declared
    name. A separate default-handler pass supplies the original DTD tokens,
    including those omitted declarations and unresolved parameter references.
    """
    declarations = 0
    cursor = 0
    while cursor < len(subset):
        if subset.startswith("<!--", cursor):
            end = subset.find("-->", cursor + 4)
            if end < 0:
                raise UnsupportedXmlConstruct("incomplete DTD comment")
            cursor = end + 3
        elif subset.startswith("<?", cursor):
            end = subset.find("?>", cursor + 2)
            if end < 0:
                raise UnsupportedXmlConstruct("incomplete DTD processing instruction")
            cursor = end + 2
        elif subset[cursor] in ('"', "'"):
            end = subset.find(subset[cursor], cursor + 1)
            if end < 0:
                raise UnsupportedXmlConstruct("incomplete DTD quoted value")
            cursor = end + 1
        elif subset.startswith("<!ENTITY", cursor):
            declarations += 1
            if declarations > policy.max_entity_declarations:
                raise XmlBudgetExceeded("entity declaration count limit exceeded")
            cursor += len("<!ENTITY")
        elif subset[cursor] == "%":
            raise UnsupportedXmlConstruct("parameter entities are unsupported")
        else:
            cursor += 1
    return declarations


def _preflight_dtd(document: bytes, policy: XmlPolicy) -> int:
    """Inspect DTD tokens without expanding general or parameter entities."""
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    subset: list[str] = []
    in_doctype = False

    class _DtdCaptured(Exception):
        pass

    def on_start(_name: str, system_id: str | None, public_id: str | None, _has_subset: int) -> None:
        nonlocal in_doctype
        if system_id is not None or public_id is not None:
            raise ExternalResourceDenied("external DTDs are denied")
        in_doctype = True

    def on_default(value: str) -> None:
        if in_doctype:
            subset.append(value)

    def on_end() -> None:
        raise _DtdCaptured()

    parser.StartDoctypeDeclHandler = on_start
    parser.EndDoctypeDeclHandler = on_end
    # DefaultHandler prevents expansion of internal general entities. It also
    # exposes DTD text that EntityDeclHandler silently omits for duplicates.
    parser.DefaultHandler = on_default
    try:
        parser.Parse(document, True)
    except _DtdCaptured:
        pass
    except expat.ExpatError as error:
        raise MalformedXmlError(
            f"XML syntax rejected at line {error.lineno}, column {error.offset}"
        ) from None
    return _count_dtd_declarations("".join(subset), policy)


def _preflight_entities(entities: dict[str, str], policy: XmlPolicy) -> None:
    """Bound each declared replacement before Expat can use it in content."""
    memo: dict[str, tuple[int, int]] = {}
    visiting: set[str] = set()

    def size_of(name: str) -> tuple[int, int]:
        if name in _PREDEFINED:
            return len(_PREDEFINED[name].encode("utf-8")), 0
        if name in memo:
            return memo[name]
        if name not in entities:
            raise UnsupportedXmlConstruct("entity references must name declared internal entities")
        if name in visiting or len(visiting) >= policy.max_entity_nesting:
            raise XmlBudgetExceeded("entity nesting limit exceeded")
        visiting.add(name)
        value = entities[name]
        total = 0
        height = 1
        cursor = 0
        while cursor < len(value):
            ampersand = value.find("&", cursor)
            if ampersand < 0:
                total += len(value[cursor:].encode("utf-8"))
                break
            total += len(value[cursor:ampersand].encode("utf-8"))
            semicolon = value.find(";", ampersand + 1)
            if semicolon < 0:
                raise UnsupportedXmlConstruct("entity replacement contains an incomplete reference")
            reference = value[ampersand + 1 : semicolon]
            reference_size, reference_height = size_of(reference)
            total += reference_size
            height = max(height, reference_height + 1)
            if total > policy.max_entity_expansion_bytes:
                raise XmlBudgetExceeded("entity replacement expansion limit exceeded")
            cursor = semicolon + 1
        visiting.remove(name)
        if height > policy.max_entity_nesting:
            raise XmlBudgetExceeded("entity nesting limit exceeded")
        if total > policy.max_entity_expansion_bytes:
            raise XmlBudgetExceeded("entity replacement expansion limit exceeded")
        memo[name] = total, height
        return memo[name]

    for declared_name in entities:
        size_of(declared_name)


def parse_xml(document: bytes, *, policy: XmlPolicy | None = None) -> XmlResult:
    """Parse bytes into a small tree, rejecting external resources and excess work.

    The output budget counts UTF-8 bytes retained as names, text, and expanded
    attribute values. Per-entity preflight bounds a single internal replacement. These
    limits do not replace the native Expat implementation's own safeguards.
    """
    if not isinstance(document, bytes):
        raise TypeError("document must be bytes")
    selected = policy if policy is not None else XmlPolicy()
    if not isinstance(selected, XmlPolicy):
        raise TypeError("policy must be XmlPolicy")
    if len(document) > selected.max_input_bytes:
        raise XmlBudgetExceeded("input byte limit exceeded")
    expected_declarations = _preflight_dtd(document, selected)

    parser = expat.ParserCreate()
    parser.buffer_text = False
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    entities: dict[str, str] = {}
    root: _NodeBuilder | None = None
    stack: list[_NodeBuilder] = []
    visible_bytes = 0
    elements = 0
    declaration_callbacks = 0

    def account_visible(value: str) -> None:
        nonlocal visible_bytes
        size = len(value.encode("utf-8"))
        if size > selected.max_visible_bytes - visible_bytes:
            raise XmlBudgetExceeded("visible XML data byte budget exceeded")
        visible_bytes += size

    def check_name(name: str) -> None:
        if len(name.encode("utf-8")) > selected.max_name_bytes:
            raise XmlBudgetExceeded("XML name byte limit exceeded")

    def on_start(name: str, attributes: dict[str, str]) -> None:
        nonlocal root, elements
        check_name(name)
        if len(stack) >= selected.max_depth or elements >= selected.max_elements:
            raise XmlBudgetExceeded("element or depth limit exceeded")
        if len(attributes) > selected.max_attributes_per_element:
            raise XmlBudgetExceeded("attribute count limit exceeded")
        account_visible(name)
        for key, value in attributes.items():
            check_name(key)
            account_visible(key)
            account_visible(value)
        node = _NodeBuilder(name, tuple(attributes.items()))
        if stack:
            stack[-1].children.append(node)
        else:
            root = node
        stack.append(node)
        elements += 1

    def on_end(_name: str) -> None:
        stack.pop()

    def on_text(value: str) -> None:
        account_visible(value)
        if stack and value:
            stack[-1].children.append(value)

    def on_doctype(_name: str, system_id: str | None, public_id: str | None, _has_subset: int) -> None:
        if system_id is not None or public_id is not None:
            raise ExternalResourceDenied("external DTDs are denied")

    def on_entity(
        name: str,
        is_parameter: int,
        value: str | None,
        _base: str | None,
        system_id: str | None,
        public_id: str | None,
        notation_name: str | None,
    ) -> None:
        nonlocal declaration_callbacks
        declaration_callbacks += 1
        if system_id is not None or public_id is not None or notation_name is not None or value is None:
            raise ExternalResourceDenied("external entities are denied")
        if is_parameter:
            raise UnsupportedXmlConstruct("parameter entities are unsupported")
        check_name(name)
        if declaration_callbacks > selected.max_entity_declarations:
            raise XmlBudgetExceeded("entity declaration count limit exceeded")
        if len(value.encode("utf-8")) > selected.max_entity_declaration_bytes:
            raise XmlBudgetExceeded("entity declaration byte limit exceeded")
        entities[name] = value

    def on_external(_context: str | None, _base: str | None, _system_id: str, _public_id: str | None) -> int:
        raise ExternalResourceDenied("external entity resolution is denied")

    def on_skipped(_name: str, _is_parameter: int) -> None:
        raise UnsupportedXmlConstruct("skipped XML entities are unsupported")

    def on_end_doctype() -> None:
        if declaration_callbacks != expected_declarations:
            raise UnsupportedXmlConstruct("duplicate or skipped entity declarations are unsupported")
        _preflight_entities(entities, selected)

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CharacterDataHandler = on_text
    parser.StartDoctypeDeclHandler = on_doctype
    parser.EndDoctypeDeclHandler = on_end_doctype
    parser.EntityDeclHandler = on_entity
    parser.ExternalEntityRefHandler = on_external
    parser.SkippedEntityHandler = on_skipped

    try:
        parser.Parse(document, True)
    except expat.ExpatError as error:
        raise MalformedXmlError(
            f"XML syntax rejected at line {error.lineno}, column {error.offset}"
        ) from None
    if root is None or stack:
        raise MalformedXmlError("XML document has no complete root element")
    return XmlResult(root.freeze(), len(document), visible_bytes, elements, declaration_callbacks)
