"""Konservative, GUI-unabhaengige Infrastruktur- und Routengraphen."""

from .model import (
    InfrastructureEdge,
    InfrastructureNode,
    OperationalRouteEdge,
    OperationalRouteGraph,
    OperationalRouteNode,
    PlatformEvidence,
    RawInfrastructureGraph,
    RouteAnchor,
    RoutePath,
)
from .graph_builder import InfrastructureGraphBuilder
from .persistence import save_generated_graph
from .artifact_identity import (
    IdentityValidation, SavedStellwerkIdentity, archive_artifact, artifact_metadata,
    atomic_write_json, find_identity_candidate, validate_saved_stellwerk_identity,
)
from .editable_topology import (
    BildfahrplanRouteInstance, DefinedRoute, EditableTopologyGraph, NODE_TYPES, PathEnumerationResult,
    TopologyEdge, TopologyNode,
)
from .editable_topology_persistence import EditableTopologyGraphStore
from .operating_point_assignments import (
    AssignableRawItem, EditableOperatingPoint, EntryInfrastructureElement, EntryPoint,
    InvalidAssignment, OperatingPointAssignments, OperatingPointConfigStore,
    can_assign_kind, entry_point_id, entry_points_from_raw_graph,
    is_unprefixed_numeric, natural_sort_key, related_selection,
)
from .wege_parser import parse_bahnsteigliste, parse_wege
from .schedule_graph import (
    OperatingPoint, OperatingPointGraph, OperatingPointResolver, PlatformRelationGraph,
    RouteAxisGraph, RouteAxisNode, ScheduleCaptureProvenance, ScheduleEdge, SchedulePointGraph,
    SchedulePointNode, station_key,
)
from .corridor import (
    BackboneEdge, BackboneScore, BetweenConstraint, BranchAttachment, CorridorGraph, CorridorGraphBuilder,
    DeferredExternalBoundaryCandidate, DerivedRouteEdge, DirectionChangeEvidence,
    ExplicitExternalBoundaryEvidence, HiddenExternalBoundaryEvidence,
    ExternalTargetResolution, HaltAwareTravelTimeComparison,
    IntermediateStopOrSkippedPointEvidence, JunctionPositionEstimate,
    OrderedScheduleSequenceEvidence, PathTimeStats, RawAdjacencyEvidence,
    SameServiceTripleEvidence, TriangleHypothesisEvidence,
    SyntheticExternalBoundaryNode, SyntheticJunctionNode, TerminalEvidence,
    TopologyQuestion, TravelTimeStats, TriangleResolutionEvidence,
)

__all__ = [
    "InfrastructureEdge", "InfrastructureGraphBuilder", "InfrastructureNode",
    "OperationalRouteEdge", "OperationalRouteGraph", "OperationalRouteNode",
    "PlatformEvidence", "RawInfrastructureGraph", "RouteAnchor", "RoutePath",
    "parse_bahnsteigliste", "parse_wege", "save_generated_graph",
    "IdentityValidation", "SavedStellwerkIdentity", "archive_artifact", "artifact_metadata",
    "BildfahrplanRouteInstance", "DefinedRoute", "EditableTopologyGraph", "NODE_TYPES", "PathEnumerationResult",
    "TopologyEdge", "TopologyNode", "EditableTopologyGraphStore",
    "atomic_write_json", "find_identity_candidate", "validate_saved_stellwerk_identity",
    "AssignableRawItem", "EditableOperatingPoint", "EntryInfrastructureElement", "EntryPoint",
    "InvalidAssignment", "OperatingPointAssignments", "OperatingPointConfigStore",
    "can_assign_kind", "entry_point_id", "entry_points_from_raw_graph",
    "is_unprefixed_numeric", "natural_sort_key", "related_selection",
    "OperatingPoint", "OperatingPointGraph", "OperatingPointResolver", "ScheduleEdge",
    "ScheduleCaptureProvenance", "SchedulePointGraph", "SchedulePointNode",
    "PlatformRelationGraph", "RouteAxisGraph", "RouteAxisNode", "station_key",
    "CorridorGraph", "CorridorGraphBuilder", "DerivedRouteEdge", "DirectionChangeEvidence",
    "BackboneEdge", "BackboneScore", "BetweenConstraint", "PathTimeStats", "RawAdjacencyEvidence", "TerminalEvidence",
    "TravelTimeStats", "TriangleResolutionEvidence", "BranchAttachment",
    "JunctionPositionEstimate", "SyntheticJunctionNode",
    "DeferredExternalBoundaryCandidate", "ExplicitExternalBoundaryEvidence",
    "ExternalTargetResolution", "HaltAwareTravelTimeComparison",
    "IntermediateStopOrSkippedPointEvidence", "HiddenExternalBoundaryEvidence",
    "OrderedScheduleSequenceEvidence", "SameServiceTripleEvidence",
    "TriangleHypothesisEvidence", "SyntheticExternalBoundaryNode", "TopologyQuestion",
]
