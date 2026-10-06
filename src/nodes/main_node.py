"""AgentCore Platform v1.0"""

# TEL-C2-002 — MainNode: REMOVED (SR #12, Low).
#
# The single-node ``main`` implementation was superseded by the Cat 2 two-layer
# nested architecture. The ``main`` backbone slot of
# TelecomContractDocumentOCRAgent is filled by DocumentExtractionGraphNode
# (src/graph/graph.py), which delegates the domain workflow to DomainWorkflowGraph
# (inner BaseGraph) and its domain nodes in src/nodes/.
#
# The former ``MainNode`` class was a deprecated, unreachable compatibility stub
# (never imported by graph.py or any other template code). It has been removed so
# there is no dead node class left to discover. This file is retained only as a
# tombstone marker and carries no code; add domain logic to the inner
# DomainWorkflowGraph nodes, never here.
