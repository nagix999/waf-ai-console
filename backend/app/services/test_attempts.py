"""Read-only retry lineage. Original test membership and failed rows stay intact."""
from sqlalchemy import func, literal, select
from ..models import Analysis, TestRunItem

EVALUATION_RETRY_AUDIT_ACTION = "rescore_test_with_retries_v1"


def test_attempts(run_id=None, *, before=None, include_retries=True):
    seed = select(TestRunItem.id.label("item_id"), TestRunItem.test_run_id,
                  TestRunItem.analysis_id.label("original_analysis_id"),
                  TestRunItem.analysis_id.label("analysis_id"), literal(0).label("retry_count"))
    if run_id is not None:
        seed = seed.where(TestRunItem.test_run_id == run_id)
    nodes = seed.cte(recursive=True)
    children = select(nodes.c.item_id, nodes.c.test_run_id, nodes.c.original_analysis_id,
                      Analysis.id, nodes.c.retry_count + 1).join(
        Analysis, Analysis.retry_of_analysis_id == nodes.c.analysis_id).where(Analysis.analysis_purpose == "test")
    if before is not None:
        children = children.where(Analysis.created_at <= before)
    if include_retries:
        nodes = nodes.union_all(children)
    ranked = select(nodes, func.row_number().over(partition_by=nodes.c.item_id,
        order_by=nodes.c.retry_count.desc()).label("position")).subquery()
    current = select(*(ranked.c[name] for name in nodes.c.keys())).where(ranked.c.position == 1).subquery()
    return nodes, current


def reference_ancestors(analysis_ids=None):
    # Test retries inherit an earlier reference only if no reference was entered
    # on the new attempt itself. Production's existing reference policy stays.
    seed = select(Analysis.id.label("target_id"), Analysis.id.label("ancestor_id"), literal(0).label("depth"))
    if analysis_ids is not None:
        seed = seed.where(Analysis.id.in_(analysis_ids))
    nodes = seed.cte(recursive=True)
    parents = select(nodes.c.target_id, Analysis.retry_of_analysis_id, nodes.c.depth + 1).join(
        Analysis, Analysis.id == nodes.c.ancestor_id).where(
        Analysis.analysis_purpose == "test", Analysis.retry_of_analysis_id.is_not(None))
    return nodes.union_all(parents)
