"""Freeze independently verified inference evidence and receipt audit records."""
from robotics_data_platform.ingestion.contracts import DataIssue
from robotics_data_platform.planning.ingestion import ProposalReceiver
from robotics_data_platform.planning.evidence import validate_evidence


def assess_proposals(raw_root,catalog,receipts,findings):
    receiver=ProposalReceiver(None,raw_root)
    accepted={row['proposal_id']:row for row in catalog}
    members=[]
    for proposal in sorted({row['proposal_id'] for row in receipts}):
        row=accepted.get(proposal)
        bundle,receipt,issues=None,None,[]
        if row:
            try:
                receipt=receiver.read_receipt(row['receipt_sha256'])
                bundle=validate_evidence(receiver.store,receipt)
                if bundle['fingerprint']!=row['manifest_sha256'] or bundle['manifest']!=row['manifest'] or bundle['configuration']!=row['configuration']:
                    raise DataIssue('PROJECTION_MISMATCH','Projected inference evidence differs from immutable source')
                manifest=bundle['manifest']
                if any(row[k]!=manifest[k] for k in ['proposal_id','episode_id','provider_status','outcome','mode','execution_eligible']) or row['model_id']!=bundle['configuration']['profile']['model_id'] or row['model_revision']!=bundle['configuration']['profile']['model_revision']:
                    raise DataIssue('PROJECTION_MISMATCH','Projected inference status/provenance differs')
            except (ValueError,OSError) as error:
                issues=[{'code':getattr(error,'code','RAW_EVIDENCE_INVALID'),'message':str(error)[:256]}]
        else:
            issues=[{'code':'ATTEMPT_NOT_ACCEPTED','message':'No complete accepted inference evidence'}]
        eligible=bundle is not None and not issues
        members.append({'proposal_id':proposal,'catalog':row,'eligible':eligible,'quality':'COMPLETE' if eligible else 'CORRUPT' if row else 'INCOMPLETE','issues':issues,'bundle':bundle if eligible else None,'receipt':receipt if eligible else None})
    return {'contract_version':'1.0.0','members':members,'receipts':receipts,'findings':findings}


def verify_proposals(snapshot,raw_root):
    receiver=ProposalReceiver(None,raw_root)
    for member in snapshot.get('proposals',{}).get('members',[]):
        if member['eligible']:
            bundle=validate_evidence(receiver.store,member['receipt'])
            if bundle['fingerprint']!=member['bundle']['fingerprint']:
                raise ValueError('Frozen inference evidence differs')
