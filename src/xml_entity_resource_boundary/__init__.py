"""A deliberately limited XML parser with entity and resource boundaries."""

from .parser import (
    ExternalResourceDenied,
    MalformedXmlError,
    UnsupportedXmlConstruct,
    XmlBoundaryError,
    XmlBudgetExceeded,
    XmlNode,
    XmlPolicy,
    XmlResult,
    parse_xml,
)

__version__ = "0.1.0"

__all__ = [
    "ExternalResourceDenied",
    "MalformedXmlError",
    "UnsupportedXmlConstruct",
    "XmlBoundaryError",
    "XmlBudgetExceeded",
    "XmlNode",
    "XmlPolicy",
    "XmlResult",
    "parse_xml",
]
