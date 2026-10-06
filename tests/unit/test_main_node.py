# TEL-C2-002 — MainNode tests: REMOVED (SR #12, Low).
#
# The MainNode class was removed (see src/nodes/main_node.py) when the Cat 2
# two-layer nested architecture superseded the single-node ``main`` slot. The
# corresponding unit tests no longer apply and have been removed. Node behaviour
# is now covered by tests/unit/test_nodes.py (domain nodes + graph composition)
# and tests/proof_of_boundary/test_pb_invoke_order.py (backbone invoke order).
# This module is intentionally empty (retained as a tombstone). It also drops the
# previous unused ``import pytest`` (ruff F401).
