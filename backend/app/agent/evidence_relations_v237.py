"""Consume checked v2.37 star links using the unchanged source-bound format.

The model's contribution memberships are inverted by contracts_v237, not here.
The existing reviewed-call adapter reads the final expanded slots after source
review, retaining corrected source identities, exact canonical aliases and the
full dependency closure. Original encrypted wire snapshots remain untouched.
"""
from .evidence_relations_v234 import EvidenceRelationsError, build_evidence_relations

