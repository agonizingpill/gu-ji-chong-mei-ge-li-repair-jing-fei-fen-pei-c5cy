from fastapi import HTTPException
from sqlalchemy import select,delete,func
from .models import Grant,Encumbrance,Treatment,Step,Accession,Reduction,ReductionItem,now
from .common import get,reject,audit,dump
from .catalog_service import latest_observation
from .algorithm import classify
from .constants import ReductionState

# 临时减拨分两段：
# 1. 提出减拨只生成待确认预案，不改预算与占款，待确认期间工序签证照常进行。
# 2. 确认时按同一加锁顺序重新读取最新签证与最新复测重算；重复确认直接返回，不重复退占款。

def _risk(db,accession_id):
    obs=latest_observation(db,accession_id)
    if not obs:return 'high',None
    level,_=classify(obs.humidity,obs.holes,obs.mold)
    return str(level),obs.id

def _has_visa(db,treatment_id):
    return db.scalar(select(func.count()).select_from(Step).where(Step.treatment_id==treatment_id,Step.done==True))>0

def _held(db,grant_id):
    return list(db.scalars(select(Encumbrance).where(Encumbrance.grant_id==grant_id,Encumbrance.status=='held').order_by(Encumbrance.id).execution_options(populate_existing=True)))

def _allocate(optional,capacity):
    """整笔取舍：先尽量多留高风险册，再尽量少退方案（即尽量多留方案）。
    金额按分整数，先取最多册高风险，其最小花费即按金额升序前缀和；余量再按金额升序补其他方案。"""
    highs=sorted((e for e in optional if e['risk']=='high'),key=lambda e:(e['amount_cents'],e['treatment_id']))
    others=sorted((e for e in optional if e['risk']!='high'),key=lambda e:(e['amount_cents'],e['treatment_id']))
    kept=set();used=0
    for e in highs:
        if used+e['amount_cents']>capacity:break
        kept.add(e['treatment_id']);used+=e['amount_cents']
    for e in others:
        if used+e['amount_cents']>capacity:break
        kept.add(e['treatment_id']);used+=e['amount_cents']
    return kept,used

def evaluate(db,grant,target_cents):
    """在当前数据下计算减拨预案。已核销支出与已有施工签证的方案占款为保底，不参与退回。"""
    spent=grant.spent_cents
    entries=[]
    for enc in _held(db,grant.id):
        plan=db.get(Treatment,enc.treatment_id)
        if not plan:continue
        level,obs_id=_risk(db,plan.accession_id)
        entries.append({'treatment_id':plan.id,'accession_id':plan.accession_id,
            'amount_cents':enc.amount_cents,'risk':level,'observation_id':obs_id,
            'protected':_has_visa(db,plan.id),'decision':'protected'})
    protected=[e for e in entries if e['protected']]
    optional=[e for e in entries if not e['protected']]
    floor=spent+sum(e['amount_cents'] for e in protected)
    feasible=floor<=target_cents
    capacity=max(target_cents-floor,0) if feasible else 0
    kept,kept_cents=_allocate(optional,capacity) if feasible else (set(),0)
    returned_cents=0
    for e in optional:
        if e['treatment_id'] in kept:e['decision']='kept'
        else:e['decision']='returned';returned_cents+=e['amount_cents']
    return {'feasible':feasible,'floor_cents':floor,'capacity_cents':capacity,
        'kept_cents':kept_cents,'returned_cents':returned_cents,
        'available_cents':capacity-kept_cents if feasible else 0,'entries':entries}

def _write_items(db,reduction,result):
    db.execute(delete(ReductionItem).where(ReductionItem.reduction_id==reduction.id))
    for e in result['entries']:
        db.add(ReductionItem(reduction_id=reduction.id,treatment_id=e['treatment_id'],
            accession_id=e['accession_id'],observation_id=e['observation_id'],
            amount_cents=e['amount_cents'],risk=e['risk'],
            protected=e['protected'],decision=e['decision']))
    db.flush()

def _view(db,row):
    data=dump(row)
    data['items']=[dump(i) for i in db.scalars(select(ReductionItem).where(ReductionItem.reduction_id==row.id).order_by(ReductionItem.id))]
    return data

def request_reduction(db,grant_id,data,user):
    # 只锁专项本行串行化并发发起；不动占款，因此不阻塞并发签证。
    grant=get(db,Grant,grant_id,True)
    reject(db.scalar(select(Reduction).where(Reduction.grant_id==grant_id,Reduction.status==ReductionState.pending.value)) is not None,'该专项已有待确认减拨，请先确认后再发起')
    reject(data.target_cents>=grant.budget_cents,'减拨后预算须低于当前预算')
    result=evaluate(db,grant,data.target_cents)
    reject(not result['feasible'],'减拨额度不足以保住已核销支出与已签证占款，已驳回减拨')
    row=Reduction(grant_id=grant_id,target_cents=data.target_cents,previous_budget_cents=grant.budget_cents,
        floor_cents=result['floor_cents'],available_cents=result['available_cents'],
        status=ReductionState.pending.value,reason='',recomputed=False,created_by=user.username)
    db.add(row);db.flush()
    _write_items(db,row,result)
    audit(db,user,'提出临时减拨',row.id);db.commit();return _view(db,row)

def confirm(db,id,user):
    row=db.get(Reduction,id)
    if not row:raise HTTPException(404,'减拨记录不存在')
    if row.status!=ReductionState.pending.value:return _view(db,row)   # 幂等：重试不重复退占款
    grant_id=row.grant_id
    # 与既有资金/施工动作保持同一加锁顺序：先卷册，再方案，最后专项。
    # 先按当前占款锁定卷册与方案，再取专项锁；专项锁到手后新占款无法再插入，
    # 若此前瞬间又新增了占款方案，则补齐其卷册/方案锁后重取（此时对方已提交，不会成环）。
    locked_t=set()
    while True:
        new=[t for t in db.scalars(select(Encumbrance.treatment_id).where(Encumbrance.grant_id==grant_id).distinct()) if t not in locked_t]
        if new:
            plans=list(db.scalars(select(Treatment).where(Treatment.id.in_(new))))
            for aid in sorted({p.accession_id for p in plans}):get(db,Accession,aid,True)
            for tid in sorted(new):get(db,Treatment,tid,True);locked_t.add(tid)
        grant=get(db,Grant,grant_id,True)
        remaining=set(db.scalars(select(Encumbrance.treatment_id).where(Encumbrance.grant_id==grant_id).distinct()))-locked_t
        if not remaining:break
    row=get(db,Reduction,id,True)
    if row.status!=ReductionState.pending.value:return _view(db,row)
    # 加锁后按最新签证与最新复测重算；并发签证或复测会在此反映。
    result=evaluate(db,grant,row.target_cents)
    before={(i.treatment_id,i.decision,i.risk) for i in db.scalars(select(ReductionItem).where(ReductionItem.reduction_id==row.id))}
    after={(e['treatment_id'],e['decision'],e['risk']) for e in result['entries']}
    recomputed=before!=after or result['floor_cents']!=row.floor_cents
    if not result['feasible']:
        row.status=ReductionState.rejected.value;row.reason='确认时保底已超过减拨目标，已驳回减拨'
        row.floor_cents=result['floor_cents'];row.available_cents=0;row.recomputed=recomputed;row.confirmed_at=now()
        _write_items(db,row,result)
        audit(db,user,'驳回临时减拨',row.id);db.commit();return _view(db,row)
    returned=0
    for e in result['entries']:
        if e['decision']!='returned':continue
        enc=db.scalar(select(Encumbrance).where(Encumbrance.treatment_id==e['treatment_id'],Encumbrance.status=='held'))
        if enc:enc.status='released';returned+=enc.amount_cents
        plan=db.get(Treatment,e['treatment_id'])
        if plan:plan.status='draft'
        get(db,Accession,e['accession_id'],True).status='isolated'
    grant.budget_cents=row.target_cents
    grant.reserved_cents=result['floor_cents']-grant.spent_cents+result['kept_cents']
    row.status=ReductionState.confirmed.value
    row.floor_cents=result['floor_cents'];row.available_cents=result['available_cents']
    row.recomputed=recomputed;row.reason='';row.confirmed_at=now()
    _write_items(db,row,result)
    audit(db,user,'确认临时减拨',f'#{row.id} 退回占款 {returned} 分')
    db.commit();return _view(db,row)

def list_for_grant(db,grant_id):
    get(db,Grant,grant_id)
    return [_view(db,r) for r in db.scalars(select(Reduction).where(Reduction.grant_id==grant_id).order_by(Reduction.id.desc()))]
