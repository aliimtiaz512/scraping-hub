from sqlalchemy import Column, Integer, String, DateTime
from database import Base

class Bid(Base):
    __tablename__ = "bids"

    id = Column(Integer, primary_key=True, index=True)
    reference_number = Column(String, unique=True, index=True)
    solicitation_number = Column(String)
    solicitation_type = Column(String)
    title = Column(String)
    publication_date = Column(String)
    question_acceptance_deadline = Column(String)
    closing_date = Column(String)
    documents_count = Column(String)
