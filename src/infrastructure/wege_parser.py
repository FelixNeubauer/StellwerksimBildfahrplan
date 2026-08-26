"""Literalparser fuer komplette ``wege``- und ``bahnsteigliste``-Container."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .model import InfrastructureEdge, InfrastructureNode, PlatformEvidence, RawInfrastructureGraph


def _element(value: str | ET.Element, expected: str) -> ET.Element:
    root = ET.fromstring(value) if isinstance(value, str) else value
    if root.tag != expected:
        raise ValueError(f"Erwartet <{expected}>, erhalten <{root.tag}>")
    return root


def parse_wege(value: str | ET.Element) -> RawInfrastructureGraph:
    """Bildet nur explizite ENR-/Namens-Endpunkte und Connectoren ab.

    Weil die Richtungssemantik von ``connector`` nicht dokumentiert ist, sind
    alle erzeugten Kanten ungerichtet. Saemtliche Originalattribute bleiben in
    ``metadata`` erhalten; unbekannte Tags und Typen werden als Nodes bewahrt.
    """
    root = _element(value, "wege")
    graph = RawInfrastructureGraph()
    endpoint_index: dict[tuple[str, str], str] = {}

    def ensure(kind: str, raw_value: str, attrs: dict[str, str] | None = None) -> str:
        key = (kind, raw_value)
        if key not in endpoint_index:
            node_id = f"{kind}:{raw_value}"
            endpoint_index[key] = node_id
            graph.nodes[node_id] = InfrastructureNode(
                id=node_id, raw_name=raw_value if kind == "name" else None,
                element_type="reference", enr=raw_value if kind == "enr" else None,
                metadata=dict(attrs or {}),
            )
        return endpoint_index[key]

    connectors: list[tuple[int, ET.Element]] = []
    for index, item in enumerate(root.iter()):
        if item is root:
            continue
        attrs = dict(item.attrib)
        if item.tag == "connector" or any(key in attrs for key in ("enr1", "enr2", "name1", "name2")):
            connectors.append((index, item))
            continue
        node_id = f"enr:{attrs['enr']}" if attrs.get("enr") else f"element:{index}"
        raw_name = attrs.get("name")
        graph.nodes[node_id] = InfrastructureNode(
            id=node_id, raw_name=raw_name, element_type=attrs.get("type", item.tag),
            enr=attrs.get("enr"), metadata={"tag": item.tag, **attrs},
        )
        if attrs.get("enr"):
            endpoint_index[("enr", attrs["enr"])] = node_id
        if raw_name:
            endpoint_index.setdefault(("name", raw_name), node_id)

    for index, item in connectors:
        attrs = dict(item.attrib)
        endpoints: list[str] = []
        for suffix in ("1", "2"):
            if attrs.get(f"enr{suffix}"):
                endpoints.append(ensure("enr", attrs[f"enr{suffix}"]))
            elif attrs.get(f"name{suffix}"):
                endpoints.append(ensure("name", attrs[f"name{suffix}"]))
        # Ein einzelner Connector-Endpunkt ist Rohinformation, aber keine
        # explizite Verbindung und erzeugt daher absichtlich keine Kante.
        if len(endpoints) == 2 and endpoints[0] != endpoints[1]:
            graph.edges.append(InfrastructureEdge(
                id=f"connector:{index}", source=endpoints[0], target=endpoints[1],
                directed=False, metadata={"tag": item.tag, **attrs},
            ))
    return graph


def parse_bahnsteigliste(value: str | ET.Element) -> tuple[PlatformEvidence, ...]:
    root = _element(value, "bahnsteigliste")
    return tuple(
        PlatformEvidence(
            raw_name=item.get("name", ""),
            related_names=tuple(child.get("name", "") for child in item.findall("n") if child.get("name")),
            metadata=dict(item.attrib),
        )
        for item in root.findall("bahnsteig")
    )
