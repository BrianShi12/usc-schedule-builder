from sqlalchemy import Column, Integer, String, ARRAY, DateTime, func, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from flask_login import UserMixin

Base = declarative_base()

class User(Base, UserMixin):
    __tablename__ = "users"
    id       = Column(Integer, primary_key=True)
    oauth_id = Column(String, unique=True, nullable=False)  # Added for Google OAuth
    email    = Column(String, unique=True, nullable=False)
    name     = Column(String)

    schedules = relationship("SavedSchedule", back_populates="user")


class SavedSchedule(Base):
    __tablename__ = "schedules"
    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    term_id    = Column(Integer, nullable=False)       # e.g. 20253 for Fall 2025
    name       = Column(String, nullable=False)        # “Fall 2025 Draft”
    sections   = Column(ARRAY(Integer), nullable=False)  
    # e.g. [12345, 67890, ...] — the list of CRNs

    user = relationship("User", back_populates="schedules")

class CourseCache(Base):
    __tablename__ = "course_cache"

    term_id    = Column(Integer, primary_key=True)
    department = Column(String,  primary_key=True)
    payload    = Column(JSONB,   nullable=False)
    fetched_at = Column(DateTime, nullable=False, server_default=func.now())
