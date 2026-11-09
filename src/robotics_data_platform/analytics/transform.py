"""Version-dispatched transforms preserve historical baseline rebuilds."""
from robotics_data_platform.analytics.baseline_v1 import materialize as baseline


def materialize(snapshot,directory):
    tables,metrics=baseline(snapshot,directory)
    metrics['split']=snapshot['task_set']['split']
    if 'proposals' in snapshot:
        from robotics_data_platform.analytics.proposal_transform import materialize_proposals
        extra,inference_metrics=materialize_proposals(snapshot,directory)
        tables.update(extra)
        metrics['proposals']=inference_metrics
    return tables,metrics
