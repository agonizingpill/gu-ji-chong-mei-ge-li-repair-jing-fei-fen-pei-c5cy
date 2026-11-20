from datetime import datetime,timezone
from sqlalchemy import Integer,String,Float,ForeignKey,UniqueConstraint,CheckConstraint,JSON,DateTime,Boolean
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
from .constants import FolioState,PlanState,ReductionState
def now():return datetime.now(timezone.utc)
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    username:Mapped[str]=mapped_column(String(40),unique=True)
    password:Mapped[str]=mapped_column(String(128))
    role:Mapped[str]=mapped_column(String(20))
class Accession(Base):
    __tablename__='accessions'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    title:Mapped[str]=mapped_column(String(100))
    era:Mapped[str]=mapped_column(String(40))
    paper:Mapped[str]=mapped_column(String(40))
    status:Mapped[str]=mapped_column(String(20),default=FolioState.registered.value)
class Observation(Base):
    __tablename__='observations'
    __table_args__=(CheckConstraint('humidity >= 0 AND humidity <= 100'),CheckConstraint('holes >= 0'),CheckConstraint('mold >= 0 AND mold <= 100'))
    id:Mapped[int]=mapped_column(primary_key=True)
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    humidity:Mapped[float]=mapped_column(Float)
    holes:Mapped[int]=mapped_column(Integer)
    mold:Mapped[float]=mapped_column(Float)
    note:Mapped[str]=mapped_column(String(300),default='')
    at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Cabinet(Base):
    __tablename__='cabinets'
    __table_args__=(CheckConstraint('capacity > 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(30),unique=True)
    capacity:Mapped[int]=mapped_column(Integer)
class Isolation(Base):
    __tablename__='isolations'
    id:Mapped[int]=mapped_column(primary_key=True)
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    cabinet_id:Mapped[int]=mapped_column(ForeignKey('cabinets.id'))
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    entered:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    exited:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class RiskAssessment(Base):
    __tablename__='risk_assessments'
    id:Mapped[int]=mapped_column(primary_key=True)
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    observation_id:Mapped[int]=mapped_column(ForeignKey('observations.id'))
    status:Mapped[str]=mapped_column(String(20),default='queued')
    predicted:Mapped[str|None]=mapped_column(String(20),nullable=True)
    approved:Mapped[str|None]=mapped_column(String(20),nullable=True)
    review_note:Mapped[str]=mapped_column(String(200),default='')
    metrics:Mapped[dict]=mapped_column(JSON,default=dict)
    error:Mapped[str]=mapped_column(String(200),default='')
class Treatment(Base):
    __tablename__='treatments'
    __table_args__=(CheckConstraint('estimated_cents > 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    name:Mapped[str]=mapped_column(String(100))
    estimated_cents:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String(20),default=PlanState.draft.value)
class Step(Base):
    __tablename__='steps'
    __table_args__=(UniqueConstraint('treatment_id','position'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    treatment_id:Mapped[int]=mapped_column(ForeignKey('treatments.id'))
    name:Mapped[str]=mapped_column(String(80))
    position:Mapped[int]=mapped_column(Integer)
    depends_on:Mapped[int|None]=mapped_column(ForeignKey('steps.id'),nullable=True)
    done:Mapped[bool]=mapped_column(Boolean,default=False)
    evidence:Mapped[str]=mapped_column(String(200),default='')
class Grant(Base):
    __tablename__='grants'
    __table_args__=(CheckConstraint('budget_cents >= 0 AND reserved_cents >= 0 AND spent_cents >= 0'),CheckConstraint('reserved_cents + spent_cents <= budget_cents'))
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    name:Mapped[str]=mapped_column(String(100))
    budget_cents:Mapped[int]=mapped_column(Integer)
    reserved_cents:Mapped[int]=mapped_column(Integer,default=0)
    spent_cents:Mapped[int]=mapped_column(Integer,default=0)
class Encumbrance(Base):
    __tablename__='encumbrances'
    id:Mapped[int]=mapped_column(primary_key=True)
    treatment_id:Mapped[int]=mapped_column(ForeignKey('treatments.id'))
    grant_id:Mapped[int]=mapped_column(ForeignKey('grants.id'))
    amount_cents:Mapped[int]=mapped_column(Integer)
    actual_cents:Mapped[int|None]=mapped_column(Integer,nullable=True)
    status:Mapped[str]=mapped_column(String(20),default='held')
class Reduction(Base):
    # 一次性临时减拨：提出时冻结预案，确认时按最新数据重算并落账。
    __tablename__='reductions'
    __table_args__=(CheckConstraint('target_cents >= 0'),CheckConstraint('previous_budget_cents >= 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    grant_id:Mapped[int]=mapped_column(ForeignKey('grants.id'))
    target_cents:Mapped[int]=mapped_column(Integer)
    previous_budget_cents:Mapped[int]=mapped_column(Integer)
    floor_cents:Mapped[int]=mapped_column(Integer,default=0)
    available_cents:Mapped[int]=mapped_column(Integer,default=0)
    status:Mapped[str]=mapped_column(String(20),default=ReductionState.pending.value)
    reason:Mapped[str]=mapped_column(String(200),default='')
    recomputed:Mapped[bool]=mapped_column(Boolean,default=False)
    created_by:Mapped[str]=mapped_column(String(40),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    confirmed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class ReductionItem(Base):
    # 减拨预案中的逐方案取舍：protected 为已签证占款，kept 为整笔保留，returned 为退回草案。
    __tablename__='reduction_items'
    id:Mapped[int]=mapped_column(primary_key=True)
    reduction_id:Mapped[int]=mapped_column(ForeignKey('reductions.id'))
    treatment_id:Mapped[int]=mapped_column(ForeignKey('treatments.id'))
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    observation_id:Mapped[int|None]=mapped_column(ForeignKey('observations.id'),nullable=True)
    amount_cents:Mapped[int]=mapped_column(Integer)
    risk:Mapped[str]=mapped_column(String(20),default='high')
    protected:Mapped[bool]=mapped_column(Boolean,default=False)
    decision:Mapped[str]=mapped_column(String(20),default='protected')
class Handover(Base):
    __tablename__='handovers'
    id:Mapped[int]=mapped_column(primary_key=True)
    accession_id:Mapped[int]=mapped_column(ForeignKey('accessions.id'))
    kind:Mapped[str]=mapped_column(String(20))
    recipient:Mapped[str]=mapped_column(String(100))
    note:Mapped[str]=mapped_column(String(200))
    at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class Audit(Base):
    __tablename__='audit'
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(40))
    action:Mapped[str]=mapped_column(String(100))
    detail:Mapped[str]=mapped_column(String(300))
    at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
